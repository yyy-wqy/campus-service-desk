from django.contrib import admin

from .models import Ticket, TicketDailySequence, TicketHistory


class TicketHistoryInline(admin.TabularInline):
    model = TicketHistory
    extra = 0
    can_delete = False
    readonly_fields = (
        "event_type",
        "actor",
        "from_status",
        "to_status",
        "description",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "title",
        "status",
        "priority",
        "reporter",
        "current_assignee",
        "created_at",
    )
    list_filter = ("status", "priority", "building_type_snapshot")
    search_fields = ("number", "title", "contact_name_snapshot")
    readonly_fields = Ticket.IMMUTABLE_FIELDS + (
        "current_assignee",
        "priority",
        "status",
        "cancellation_pending",
        "created_at",
        "updated_at",
    )
    inlines = (TicketHistoryInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TicketHistory)
class TicketHistoryAdmin(admin.ModelAdmin):
    list_display = ("ticket", "event_type", "actor", "created_at")
    list_filter = ("event_type",)
    readonly_fields = (
        "ticket",
        "event_type",
        "actor",
        "from_status",
        "to_status",
        "description",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TicketDailySequence)
class TicketDailySequenceAdmin(admin.ModelAdmin):
    list_display = ("business_date", "last_value")
    readonly_fields = ("business_date", "last_value")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
