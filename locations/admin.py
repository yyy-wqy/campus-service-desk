from django.contrib import admin

from accounts.models import User

from .models import Building, FaultCategory, HousingChangeLog, PublicAreaOption, StudentHousing


class BusinessAdminOnly(admin.ModelAdmin):
    """后台只对业务管理员开放，并且不允许删除。

    仅按 Django 模型权限授权是不够的：教师即使被授予 locations 的模型权限，
    也不能在后台修改楼栋或其它学生的住宿档案。删除一律禁止，避免历史工单
    失去楼栋、类别名称。
    """

    def _is_business_admin(self, request):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == User.Role.ADMIN
            and getattr(user, "approval_status", None) == User.ApprovalStatus.APPROVED
        )

    def has_module_permission(self, request):
        return self._is_business_admin(request)

    def has_view_permission(self, request, obj=None):
        return self._is_business_admin(request)

    def has_add_permission(self, request):
        return self._is_business_admin(request)

    def has_change_permission(self, request, obj=None):
        return self._is_business_admin(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Building)
class BuildingAdmin(BusinessAdminOnly):
    list_display = ["name", "building_type", "is_active"]
    list_filter = ["building_type", "is_active"]
    search_fields = ["name"]
    readonly_fields = ("created_at",)


@admin.register(FaultCategory)
class FaultCategoryAdmin(BusinessAdminOnly):
    list_display = ["name", "is_teacher_only", "is_active"]
    list_filter = ["is_teacher_only", "is_active"]
    search_fields = ["name"]
    readonly_fields = ("created_at",)


@admin.register(PublicAreaOption)
class PublicAreaOptionAdmin(BusinessAdminOnly):
    list_display = ["name", "sort_order", "is_other", "is_active"]
    list_filter = ["is_other", "is_active"]
    search_fields = ["name"]
    readonly_fields = ("created_at",)


@admin.register(StudentHousing)
class StudentHousingAdmin(BusinessAdminOnly):
    """只读：住宿楼栋必须通过 /locations/manage/housing/ 修改，才能留痕。"""

    list_display = ["student", "building", "last_self_change_at", "updated_at"]
    list_filter = ["building"]
    search_fields = ["student__username", "student__real_name"]
    readonly_fields = ("student", "building", "last_self_change_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HousingChangeLog)
class HousingChangeLogAdmin(BusinessAdminOnly):
    list_display = ["student", "source", "from_building_name", "to_building_name", "actor", "created_at"]
    list_filter = ["source", "created_at"]
    search_fields = ["student__username", "student__real_name"]
    readonly_fields = [field.name for field in HousingChangeLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
