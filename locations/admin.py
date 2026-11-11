from django.contrib import admin

from .models import Building, FaultCategory, HousingChangeLog, PublicAreaOption, StudentHousing


class NoDeleteAdmin(admin.ModelAdmin):
    """基础资料只停用、不删除，避免历史工单失去关联名称。"""

    readonly_fields = ("created_at",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Building)
class BuildingAdmin(NoDeleteAdmin):
    list_display = ["name", "building_type", "is_active"]
    list_filter = ["building_type", "is_active"]
    search_fields = ["name"]


@admin.register(FaultCategory)
class FaultCategoryAdmin(NoDeleteAdmin):
    list_display = ["name", "is_teacher_only", "is_active"]
    list_filter = ["is_teacher_only", "is_active"]
    search_fields = ["name"]


@admin.register(PublicAreaOption)
class PublicAreaOptionAdmin(NoDeleteAdmin):
    list_display = ["name", "sort_order", "is_other", "is_active"]
    list_filter = ["is_other", "is_active"]
    search_fields = ["name"]


@admin.register(StudentHousing)
class StudentHousingAdmin(admin.ModelAdmin):
    list_display = ["student", "building", "last_self_change_at", "updated_at"]
    list_filter = ["building"]
    search_fields = ["student__username", "student__real_name"]
    readonly_fields = ("last_self_change_at", "updated_at")


@admin.register(HousingChangeLog)
class HousingChangeLogAdmin(admin.ModelAdmin):
    list_display = ["student", "source", "from_building_name", "to_building_name", "actor", "created_at"]
    list_filter = ["source", "created_at"]
    search_fields = ["student__username", "student__real_name"]
    readonly_fields = [field.name for field in HousingChangeLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
