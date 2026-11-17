from django.core.exceptions import ValidationError

from accounts.models import User

from .models import Building, FaultCategory, PublicAreaOption, StudentHousing

LOCATION_KIND_ROOM = "room"
LOCATION_KIND_PUBLIC_AREA = "public_area"
LOCATION_KIND_CHOICES = (
    (LOCATION_KIND_ROOM, "房间"),
    (LOCATION_KIND_PUBLIC_AREA, "公共区域"),
)

DORM = "dorm"
TEACH = "teach"
ROOM_NUMBER_MAX_LENGTH = 20
PUBLIC_AREA_OTHER_MAX_LENGTH = 50


def can_report(user):
    """只有已通过审核的学生、教师可以提交报修。"""

    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.role in {User.Role.STUDENT, User.Role.TEACHER}
        and user.approval_status == User.ApprovalStatus.APPROVED
    )


def _reload(model, instance):
    """按主键重新读取数据库当前状态，调用方传入的对象只提供主键。

    表单里拿到的对象可能已经被缓存（例如管理员刚把学生从 A 栋迁到 B 栋），
    校验必须以数据库当前值为准。
    """

    if instance is None:
        return None
    pk = getattr(instance, "pk", None)
    if pk is None:
        return None
    return model.objects.filter(pk=pk).first()


def validate_repair_scope(
    *,
    reporter,
    building,
    fault_category,
    location_kind,
    room_number="",
    public_area=None,
    public_area_other="",
):
    """统一的服务端组合校验，供工单创建调用。

    参数与报修表单字段一一对应；校验失败时抛出 ValidationError，
    错误字典的键就是表单字段名，前端可直接显示在对应字段下方。
    校验通过时返回可直接写入工单的展示信息，以及本次校验实际使用的
    数据库最新对象：

        {
            "location_kind": "room"|"public_area",
            "location_detail": "301",
            "reporter": reporter,
            "building": building,
            "fault_category": fault_category,
        }

    所有资料都按主键重新读取（身份、住宿、楼栋、类别、公共区域），
    调用方缓存的旧对象不能让停用项或已迁出的宿舍通过校验。
    """

    errors = {}

    # 1. 身份与住宿档案：都按 ID 读取当前资料。
    reporter = _reload(User, reporter)
    if reporter is None or not can_report(reporter):
        raise ValidationError({"reporter": "只有已通过审核的学生或教师可以提交报修。"})
    housing = (
        StudentHousing.objects.select_related("building")
        .filter(student_id=reporter.pk)
        .first()
    )

    # 2. 楼栋、类别、公共区域：同样以数据库当前状态为准。
    #    是否“提交了公共区域”要按原始参数判断：选项被删后重新查询会是空值，
    #    不能用空值反推“没提交”，否则房间号与公共区域的冲突会被忽略。
    public_area_provided = public_area is not None
    building = _reload(Building, building)
    fault_category = _reload(FaultCategory, fault_category)
    public_area = _reload(PublicAreaOption, public_area)

    if building is None:
        errors["building"] = "楼栋不存在，请重新选择。"
    elif not building.is_active:
        errors["building"] = "该楼栋已停用，请重新选择。"

    if fault_category is None:
        errors["fault_category"] = "故障类别不存在，请重新选择。"
    elif not fault_category.is_active:
        errors["fault_category"] = "该故障类别已停用，请重新选择。"

    # 3. 角色与楼栋类型、故障类别的权限范围。
    if not errors and building is not None and fault_category is not None:
        if reporter.role == User.Role.TEACHER:
            if building.building_type != TEACH:
                errors["building"] = "教师只能报修教学楼的设施。"
        else:
            if fault_category.is_teacher_only:
                errors["fault_category"] = "该故障类别为教师专用设备，学生不能报修。"
            if building.building_type == DORM:
                if housing is None:
                    errors["building"] = "请先在“住宿信息”中填写宿舍楼栋，再报修宿舍。"
                elif housing.building_id != building.pk:
                    errors["building"] = "学生只能报修本人住宿楼栋的宿舍设施。"

    # 4. 位置类型：房间号与公共区域二选一，且不能提交与选项不一致的文字。
    other_text = (public_area_other or "").strip()
    location_detail = ""
    if location_kind not in dict(LOCATION_KIND_CHOICES):
        errors["location_kind"] = "请选择有效的位置类型。"
    elif location_kind == LOCATION_KIND_ROOM:
        if public_area_provided:
            errors["public_area"] = "房间号与公共区域只能填写一项。"
        if other_text:
            errors["public_area_other"] = "只有选择“其他”公共区域时才能填写具体位置。"
        text = (room_number or "").strip()
        if not text:
            errors["room_number"] = "请填写房间号。"
        elif len(text) > ROOM_NUMBER_MAX_LENGTH:
            errors["room_number"] = f"房间号不能超过 {ROOM_NUMBER_MAX_LENGTH} 个字符。"
        else:
            location_detail = text
    else:
        if (room_number or "").strip():
            errors["room_number"] = "房间号与公共区域只能填写一项。"
        if public_area is None:
            errors["public_area"] = "请选择公共区域。"
        elif not public_area.is_active:
            errors["public_area"] = "该公共区域已停用，请重新选择。"
        elif public_area.is_other:
            if not other_text:
                errors["public_area_other"] = "选择“其他”时必须填写具体位置。"
            elif len(other_text) > PUBLIC_AREA_OTHER_MAX_LENGTH:
                errors["public_area_other"] = (
                    f"具体位置不能超过 {PUBLIC_AREA_OTHER_MAX_LENGTH} 个字符。"
                )
            else:
                location_detail = f"{public_area.name}（{other_text}）"
        elif other_text:
            errors["public_area_other"] = "该公共区域不是“其他”选项，请勿填写具体位置。"
        else:
            location_detail = public_area.name

    if errors:
        raise ValidationError(errors)

    return {
        "location_kind": location_kind,
        "location_detail": location_detail,
        "reporter": reporter,
        "building": building,
        "fault_category": fault_category,
    }


def active_public_areas():
    return PublicAreaOption.objects.filter(is_active=True)


def reportable_buildings(user):
    """返回角色可选的楼栋集合（用于表单下拉，最终仍以服务端校验为准）。"""

    buildings = Building.objects.filter(is_active=True)
    if getattr(user, "role", None) == User.Role.TEACHER:
        return buildings.filter(building_type=TEACH)
    if getattr(user, "role", None) == User.Role.STUDENT:
        housing = (
            StudentHousing.objects.filter(student_id=user.pk).first()
            if getattr(user, "pk", None)
            else None
        )
        if housing is None:
            return buildings.filter(building_type=TEACH)
        return buildings.filter(building_type=TEACH) | buildings.filter(
            pk=housing.building_id
        )
    return buildings.none()


def reportable_categories(user):
    categories = FaultCategory.objects.filter(is_active=True)
    if getattr(user, "role", None) == User.Role.STUDENT:
        return categories.filter(is_teacher_only=False)
    return categories
