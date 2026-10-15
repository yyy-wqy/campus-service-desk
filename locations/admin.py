from django.contrib import admin
from .models import Building, FaultCategory

@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ["name", "building_type", "is_active"]
    list_filter = ["building_type", "is_active"]
    search_fields = ["name"]

@admin.register(FaultCategory)
class FaultCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "is_teacher_only", "is_active"]
    list_filter = ["is_teacher_only", "is_active"]
    search_fields = ["name"]
