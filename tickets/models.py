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
    def update(self, **kwargs):
        immutable_fields = set(self.model.IMMUTABLE_FIELDS)
        for field in self.model._meta.concrete_fields:
            if field.attname in immutable_fields:
                immutable_fields.add(field.name)

        blocked_fields = sorted(immutable_fields.intersection(kwargs))
        if blocked_fields:
            raise ValidationError(
                {
                    field: "工单原始报修信息提交后不可覆盖。"
                    for field in blocked_fields
                }
            )
        return super().update(**kwargs)

    def visible_to(self, user):
        from .permissions import current_business_user

        current = current_business_user(user)
        if current is None:
            return self.none()
        if current.role == "admin":
            return self
        if current.role in {"student", "teacher"}:
            return self.filter(reporter=current)
        if current.role == "maintenance":
            return self.filter(current_assignee=current)
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
    repair_version = models.PositiveIntegerField("维修操作版本", default=0, editable=False)
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


class TicketCancellation(models.Model):
    class Decision(models.TextChoices):
        DIRECT = "direct", "报修人自行取消"
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已批准"
        REJECTED = "rejected", "已驳回"

    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="cancellations")
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_ticket_cancellations"
    )
    reason = models.CharField("申请原因", max_length=1000, blank=True)
    created_at = models.DateTimeField("申请时间", auto_now_add=True)
    decision = models.CharField("审核结果", max_length=10, choices=Decision.choices, default=Decision.PENDING)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviewed_ticket_cancellations",
        null=True, blank=True,
    )
    review_reason = models.CharField("审核说明", max_length=1000, blank=True)
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("ticket",),
                condition=models.Q(decision="pending"),
                name="ticket_one_pending_cancellation",
            ),
        ]


class TicketPhoto(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="photos")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    storage_name = models.CharField(max_length=200, unique=True, editable=False)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=30)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    disposed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "pk")


class TicketPhotoAccess(models.Model):
    photo = models.ForeignKey(TicketPhoto, on_delete=models.PROTECT, related_name="accesses")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class TicketPhotoDisposal(models.Model):
    photo = models.ForeignKey(TicketPhoto, on_delete=models.PROTECT, related_name="disposals")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    policy_reference = models.CharField(max_length=200)
    status = models.CharField(max_length=10, default="started", choices=[("started", "处理中"), ("completed", "已完成"), ("failed", "失败")])
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)


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
        PROGRESS_ADDED = "progress_added", "维修进度"
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

    def get_from_status_display(self):
        return dict(Ticket.Status.choices).get(
            self.from_status, self.from_status or "—"
        )

    def get_to_status_display(self):
        return dict(Ticket.Status.choices).get(
            self.to_status, self.to_status or "—"
        )

    def __str__(self):
        return f"{self.ticket.number} {self.get_event_type_display()}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("工单历史只能追加，不能更新。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("工单历史只能追加，不能删除。")

class RepairRecordQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("维修记录只能新增，不能覆盖。")

    def delete(self):
        raise ValidationError("维修记录必须保留，不能删除。")


class RepairRecord(models.Model):
    created_at = models.DateTimeField("记录时间", auto_now_add=True)
    objects = RepairRecordQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ("created_at", "pk")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("维修记录只能新增，不能覆盖。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("维修记录必须保留，不能删除。")


class RepairProgress(RepairRecord):
    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="repair_progress"
    )
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="repair_progress_records",
    )
    round_number = models.PositiveIntegerField("维修轮次")
    content = models.TextField("进度内容", max_length=2000)

    class Meta(RepairRecord.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(
                condition=models.Q(round_number__gte=1),
                name="repair_progress_round_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(content=""),
                name="repair_progress_content_nonempty",
            ),
        ]


class RepairResult(RepairRecord):
    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="repair_results"
    )
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="repair_result_records",
    )
    round_number = models.PositiveIntegerField("维修轮次")
    content = models.TextField("维修结果", max_length=5000)

    class Meta(RepairRecord.Meta):
        abstract = False
        ordering = ("round_number", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("ticket", "round_number"),
                name="repair_result_unique_round",
            ),
            models.CheckConstraint(
                condition=models.Q(round_number__gte=1),
                name="repair_result_round_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(content=""),
                name="repair_result_content_nonempty",
            ),
        ]


class RepairConfirmation(RepairRecord):
    class Decision(models.TextChoices):
        RESOLVED = "resolved", "已解决"
        UNRESOLVED = "unresolved", "未解决，退回维修"

    result = models.OneToOneField(
        RepairResult, on_delete=models.PROTECT, related_name="confirmation"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="repair_confirmations",
    )
    decision = models.CharField(
        "确认结果", max_length=20, choices=Decision.choices
    )
    reason = models.TextField("未解决原因", max_length=2000, blank=True)

    class Meta(RepairRecord.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(
                condition=models.Q(decision__in=("resolved", "unresolved")),
                name="repair_confirmation_valid_decision",
            ),
            models.CheckConstraint(
                condition=models.Q(decision="resolved") | ~models.Q(reason=""),
                name="repair_unresolved_requires_reason",
            ),
        ]
