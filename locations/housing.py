import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.utils import timezone

from .models import Building, HousingChangeLog, StudentHousing

# 同一学生每 30 天最多自行更换 1 次住宿楼栋
HOUSING_CHANGE_INTERVAL = timedelta(days=30)

DORM = "dorm"

# SQLite 写锁竞争的有限重试次数；重试耗尽后返回可读提示，不抛 500
LOCK_RETRIES = 4


class HousingChangeTooSoon(ValidationError):
    """自助更换楼栋过于频繁。"""

    def __init__(self, next_allowed_at):
        self.next_allowed_at = next_allowed_at
        allowed = timezone.localtime(next_allowed_at).strftime("%Y-%m-%d %H:%M")
        super().__init__(f"每 30 天只能自行更换 1 次住宿楼栋，下次可更换时间：{allowed}")


class HousingChangeBusy(ValidationError):
    """并发写锁竞争，本次请求没有完成。"""

    def __init__(self):
        super().__init__("另一次换楼请求正在处理，请稍后刷新重试。")


def _run_with_lock_retry(operation, *args, **kwargs):
    """SQLite 并发写入会短暂返回 database is locked，做有限重试。"""

    for attempt in range(LOCK_RETRIES):
        try:
            return operation(*args, **kwargs)
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == LOCK_RETRIES - 1:
                raise HousingChangeBusy() from exc
            time.sleep(0.05 * (attempt + 1))
    raise HousingChangeBusy()


def next_allowed_change(housing):
    """返回下一次允许自助更换的时间；从未自助变更为 None。"""

    if housing is None or housing.last_self_change_at is None:
        return None
    return housing.last_self_change_at + HOUSING_CHANGE_INTERVAL


def get_housing(student):
    """读取住宿档案；并发写锁时重试，避免读操作直接把页面打成 500。"""

    return _run_with_lock_retry(
        lambda: StudentHousing.objects.select_related("building").filter(student=student).first()
    )


def _check_target(building):
    if building.building_type != DORM:
        raise ValidationError({"building": "住宿楼栋只能选择宿舍楼。"})
    if not building.is_active:
        raise ValidationError({"building": "该楼栋已停用，请重新选择。"})


def _log_change(student, actor, source, from_building, to_building):
    HousingChangeLog.objects.create(
        student=student,
        actor=actor,
        source=source,
        from_building=from_building,
        to_building=to_building,
        from_building_name=from_building.name if from_building else "",
        to_building_name=to_building.name,
    )


def _building_is_reportable_dorm(building_id):
    """数据库当前状态是否仍为可用宿舍楼。"""

    return Building.objects.filter(
        pk=building_id, building_type=DORM, is_active=True
    ).exists()


def _apply_change(housing, building, now):
    """带时间和楼栋条件的 UPDATE；返回受影响行数。

    条件里带上“目标楼栋仍是可用宿舍楼”，保证管理员把楼栋改成教学楼后，
    即使学生请求用的是旧对象，也不会把住宿档案写进教学楼。
    """

    return (
        StudentHousing.objects.filter(pk=housing.pk)
        .filter(
            models.Q(last_self_change_at__isnull=True)
            | models.Q(last_self_change_at__lte=now - HOUSING_CHANGE_INTERVAL)
        )
        .filter(
            models.Exists(
                Building.objects.filter(
                    pk=building.pk, building_type=DORM, is_active=True
                )
            )
        )
        .update(building=building, last_self_change_at=now, updated_at=now)
    )


def _raise_change_failure(housing, building, now):
    """UPDATE 没生效时给出准确原因：楼栋变了，还是 30 天未到。"""

    if not _building_is_reportable_dorm(building.pk):
        raise ValidationError(
            {"building": "该楼栋已改为教学楼或已停用，请重新选择。"}
        )
    fresh = StudentHousing.objects.select_related("building").get(pk=housing.pk)
    raise HousingChangeTooSoon(next_allowed_change(fresh) or now)


def _change_own_once(student, building):
    _check_target(building)
    now = timezone.now()
    with transaction.atomic():
        housing = StudentHousing.objects.select_related("building").filter(student=student).first()
        if housing is None:
            try:
                with transaction.atomic():
                    StudentHousing.objects.create(student=student, building=building)
                    # 先写入取得写锁，再复核楼栋当前状态，避免与管理员改类型并发。
                    if not _building_is_reportable_dorm(building.pk):
                        raise ValidationError(
                            {"building": "该楼栋已改为教学楼或已停用，请重新选择。"}
                        )
            except IntegrityError:
                # 并发首次填写：另一个请求已经建好档案，转为变更流程。
                housing = StudentHousing.objects.select_related("building").get(student=student)
            else:
                _log_change(student, student, HousingChangeLog.Source.SELF, None, building)
                return StudentHousing.objects.get(student=student)

        if housing.building_id == building.pk:
            return housing

        next_allowed = next_allowed_change(housing)
        if next_allowed is not None and now < next_allowed:
            raise HousingChangeTooSoon(next_allowed)

        old_building = housing.building
        updated = _apply_change(housing, building, now)
        if not updated:
            _raise_change_failure(housing, building, now)

        _log_change(student, student, HousingChangeLog.Source.SELF, old_building, building)
        return StudentHousing.objects.get(pk=housing.pk)


def change_own_building(student, building):
    """学生自助填写或更换住宿楼栋。

    - 首次填写不计次、不启动冷却；
    - 同一楼栋重复保存不计次；
    - 楼栋实际变更后，满 30 天（含第 30 天整）才允许下一次自助变更；
    - 并发请求通过带条件的 UPDATE 保证只有一次生效；
    - 数据库写锁竞争做有限重试，最终给出可读提示而不是 500。
    """

    return _run_with_lock_retry(_change_own_once, student, building)


def _admin_change_once(student, building, actor):
    _check_target(building)
    now = timezone.now()
    with transaction.atomic():
        housing = StudentHousing.objects.select_related("building").filter(student=student).first()
        if housing is None:
            StudentHousing.objects.create(student=student, building=building)
            if not _building_is_reportable_dorm(building.pk):
                raise ValidationError(
                    {"building": "该楼栋已改为教学楼或已停用，请重新选择。"}
                )
            _log_change(student, actor, HousingChangeLog.Source.ADMIN, None, building)
            return StudentHousing.objects.get(student=student)

        if housing.building_id == building.pk:
            return housing

        old_building = housing.building
        updated = (
            StudentHousing.objects.filter(pk=housing.pk)
            .filter(
                models.Exists(
                    Building.objects.filter(
                        pk=building.pk, building_type=DORM, is_active=True
                    )
                )
            )
            .update(building=building, updated_at=now)
        )
        if not updated:
            raise ValidationError(
                {"building": "该楼栋已改为教学楼或已停用，请重新选择。"}
            )
        _log_change(student, actor, HousingChangeLog.Source.ADMIN, old_building, building)
        return StudentHousing.objects.get(pk=housing.pk)


def set_building_by_admin(student, building, actor):
    """管理员随时修改住宿楼栋。

    不计入学生的自助次数，也不重置学生的 30 天冷却，只做单独留痕。
    后台（Django admin 与基础资料管理页）都必须通过本函数写入，避免无痕修改。
    """

    return _run_with_lock_retry(_admin_change_once, student, building, actor)
