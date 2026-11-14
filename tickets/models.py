from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TicketDailySequence(models.Model):
    business_date = models.DateField("业务日期", unique=True)
    last_value = models.PositiveSmallIntegerField("最后流水号", default=0)

    class Meta:
        verbose_name = "工单每日流水"
        verbose_name_plural = "工单每日流水"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(last_value__gte=0, last_value__lte=9999),
                name="ticket_sequence_between_0_and_9999",
            )
        ]

    def __str__(self):
        return f"{self.business_date}: {self.last_value:04d}"


class TicketQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if user.role == "admin":
            return self
        if user.role in {"student", "teacher"}:
            return self.filter(reporter=user)
        if user.role == "maintenance":
            return self.filter(current_assignee=user)
        return self.none()


class Ticket(models.Model):
    class Status(models.TextChoices):
        PENDING_ACCEPTANCE = "pending_acceptance", "待受理"
        PENDING_WORK = "pending_work", "待处理"
        IN_PROGRESS = "in_progress", "处理中"
        PENDING_CONFIRMATION = "pending_confirmation", "待报修人确认"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"
        REJECTED = "rejected", "不予受理"

    class Priority(models.TextChoices):
        NORMAL = "normal", "普通"
        URGENT = "urgent", "紧急"

    class LocationKind(models.TextChoices):
        ROOM = "room", "房间"
        PUBLIC_AREA = "public_area", "公共区域"

    IMMUTABLE_FIELDS = (
        "number",
        "reporter_id",
        "building_id",
        "fault_category_id",
        "title",
        "description",
        "location_kind",
        "location_detail",
        "preferred_visit_time",
        "contact_name_snapshot",
        "contact_phone_snapshot",
        "reporter_role_snapshot",
        "building_name_snapshot",
        "building_type_snapshot",
        "category_name_snapshot",
        "category_teacher_only_snapshot",
    )
    ASSIGNEE_REQUIRED_STATUSES = (
        Status.PENDING_WORK,
        Status.IN_PROGRESS,
        Status.PENDING_CONFIRMATION,
        Status.COMPLETED,
    )
    ASSIGNEE_FORBIDDEN_STATUSES = (
        Status.PENDING_ACCEPTANCE,
        Status.REJECTED,
    )

    number = models.CharField("工单编号", max_length=18, unique=True, editable=False)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_tickets",
        verbose_name="报修人",
    )
    current_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_tickets",
        verbose_name="当前维修人员",
        blank=True,
        null=True,
    )
    building = models.ForeignKey(
        "locations.Building",
        on_delete=models.PROTECT,
        related_name="tickets",
        verbose_name="楼栋",
    )
    fault_category = models.ForeignKey(
        "locations.FaultCategory",
        on_delete=models.PROTECT,
        related_name="tickets",
        verbose_name="故障类别",
    )
    title = models.CharField("故障标题", max_length=120)
    description = models.TextField("故障描述")
    location_kind = models.CharField(
        "位置类型", max_length=20, choices=LocationKind.choices
    )
    location_detail = models.CharField("房间或公共区域", max_length=120)
    preferred_visit_time = models.CharField(
        "方便上门时间", max_length=120, blank=True
    )

    contact_name_snapshot = models.CharField("联系人快照", max_length=50)
    contact_phone_snapshot = models.CharField("联系方式快照", max_length=30)
    reporter_role_snapshot = models.CharField("报修角色快照", max_length=20)
    building_name_snapshot = models.CharField("楼栋名称快照", max_length=100)
    building_type_snapshot = models.CharField("楼栋类型快照", max_length=10)
    category_name_snapshot = models.CharField("故障类别快照", max_length=100)
    category_teacher_only_snapshot = models.BooleanField("教师专用类别快照")

    priority = models.CharField(
        "优先级", max_length=10, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(
        "工单状态",
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING_ACCEPTANCE,
    )
    cancellation_pending = models.BooleanField("取消申请待审核", default=False)
    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        verbose_name = "报修工单"
        verbose_name_plural = "报修工单"
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("status", "created_at"), name="ticket_status_created_idx"),
            models.Index(fields=("reporter", "created_at"), name="ticket_reporter_created_idx"),
            models.Index(
                fields=("current_assignee", "status"), name="ticket_assignee_status_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(
                        status__in=(
                            "pending_work",
                            "in_progress",
                            "pending_confirmation",
                            "completed",
                        )
                    )
                    | models.Q(current_assignee__isnull=False)
                ),
                name="ticket_processing_status_has_assignee",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=("pending_acceptance", "rejected"))
                    | models.Q(current_assignee__isnull=True)
                ),
                name="ticket_preassignment_status_has_no_assignee",
            ),
        ]

    def __str__(self):
        return f"{self.number} {self.title}"

    def clean(self):
        super().clean()
        errors = {}
        if self.reporter_id and self.reporter.role not in {"student", "teacher"}:
            errors["reporter"] = "只有学生或教师可以提交报修。"
        if self.current_assignee_id:
            if self.current_assignee.role != "maintenance":
                errors["current_assignee"] = "当前处理人必须是维修人员。"
            elif self.current_assignee.approval_status != "approved":
                errors["current_assignee"] = "当前处理人必须已审核通过。"
        if self.status in self.ASSIGNEE_REQUIRED_STATUSES and not self.current_assignee_id:
            errors["current_assignee"] = "该工单状态必须设置当前维修人员。"
        if self.status in self.ASSIGNEE_FORBIDDEN_STATUSES and self.current_assignee_id:
            errors["current_assignee"] = "该工单状态不能预先设置维修人员。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self.IMMUTABLE_FIELDS
            ).first()
            if original:
                changed = {
                    field: "工单原始报修信息提交后不可覆盖。"
                    for field in self.IMMUTABLE_FIELDS
                    if getattr(self, field) != original[field]
                }
                if changed:
                    raise ValidationError(changed)
        return super().save(*args, **kwargs)


class AppendOnlyHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("工单历史只能追加，不能更新。")

    def delete(self):
        raise TypeError("工单历史只能追加，不能删除。")


class TicketHistory(models.Model):
    class EventType(models.TextChoices):
        CREATED = "created", "创建工单"
        STATUS_CHANGED = "status_changed", "状态变更"
        ASSIGNED = "assigned", "分派"
        PRIORITY_CHANGED = "priority_changed", "优先级变更"
        NOTE_ADDED = "note_added", "追加说明"
        CANCELLATION_CHANGED = "cancellation_changed", "取消申请变更"

    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="history", verbose_name="工单"
    )
    event_type = models.CharField(
        "事件类型", max_length=30, choices=EventType.choices
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_history_entries",
        verbose_name="操作人",
    )
    from_status = models.CharField("原状态", max_length=30, blank=True)
    to_status = models.CharField("新状态", max_length=30, blank=True)
    description = models.TextField("事件说明", blank=True)
    metadata = models.JSONField("补充数据", default=dict, blank=True)
    created_at = models.DateTimeField("记录时间", auto_now_add=True)

    objects = AppendOnlyHistoryQuerySet.as_manager()

    class Meta:
        verbose_name = "工单历史"
        verbose_name_plural = "工单历史"
        ordering = ("created_at", "pk")
        indexes = [
            models.Index(fields=("ticket", "created_at"), name="ticket_history_time_idx")
        ]

    def __str__(self):
        return f"{self.ticket.number} {self.get_event_type_display()}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("工单历史只能追加，不能更新。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("工单历史只能追加，不能删除。")
