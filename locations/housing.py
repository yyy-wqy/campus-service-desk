from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from .models import Building, HousingChangeLog, StudentHousing

# 同一学生每 30 天最多自行更换 1 次住宿楼栋
HOUSING_CHANGE_INTERVAL = timedelta(days=30)

DORM = "dorm"


class HousingChangeTooSoon(ValidationError):
    """自助更换楼栋过于频繁。"""

    def __init__(self, next_allowed_at):
        self.next_allowed_at = next_allowed_at
        allowed = timezone.localtime(next_allowed_at).strftime("%Y-%m-%d %H:%M")
        super().__init__(f"每 30 天只能自行更换 1 次住宿楼栋，下次可更换时间：{allowed}")


def next_allowed_change(housing):
    """返回下一次允许自助更换的时间；从未自助变更为 None。"""

    if housing is None or housing.last_self_change_at is None:
        return None
    return housing.last_self_change_at + HOUSING_CHANGE_INTERVAL


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


def change_own_building(student, building):
    """学生自助填写或更换住宿楼栋。

    - 首次填写不计次、不启动冷却；
    - 同一楼栋重复保存不计次；
    - 楼栋实际变更后，满 30 天（含第 30 天整）才允许下一次自助变更；
    - 并发请求通过带条件的 UPDATE 保证只有一次生效。
    """

    _check_target(building)
    now = timezone.now()
    with transaction.atomic():
        housing = StudentHousing.objects.select_related("building").filter(student=student).first()
        if housing is None:
            try:
                with transaction.atomic():
                    StudentHousing.objects.create(student=student, building=building)
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
        updated = (
            StudentHousing.objects.filter(pk=housing.pk)
            .filter(
                models.Q(last_self_change_at__isnull=True)
                | models.Q(last_self_change_at__lte=now - HOUSING_CHANGE_INTERVAL)
            )
            .update(building=building, last_self_change_at=now, updated_at=now)
        )
        if not updated:
            # 并发请求已经改过：重新读取真实状态给出正确的下次时间。
            fresh = StudentHousing.objects.select_related("building").get(pk=housing.pk)
            raise HousingChangeTooSoon(next_allowed_change(fresh) or now)

        _log_change(student, student, HousingChangeLog.Source.SELF, old_building, building)
        return StudentHousing.objects.get(pk=housing.pk)


def set_building_by_admin(student, building, actor):
    """管理员随时修改住宿楼栋。

    不计入学生的自助次数，也不重置学生的 30 天冷却，只做单独留痕。
    """

    _check_target(building)
    now = timezone.now()
    with transaction.atomic():
        housing = StudentHousing.objects.select_related("building").filter(student=student).first()
        if housing is None:
            StudentHousing.objects.create(student=student, building=building)
            _log_change(student, actor, HousingChangeLog.Source.ADMIN, None, building)
            return StudentHousing.objects.get(student=student)

        if housing.building_id == building.pk:
            return housing

        old_building = housing.building
        StudentHousing.objects.filter(pk=housing.pk).update(building=building, updated_at=now)
        _log_change(student, actor, HousingChangeLog.Source.ADMIN, old_building, building)
        return StudentHousing.objects.get(pk=housing.pk)
