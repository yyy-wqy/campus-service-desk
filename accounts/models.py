from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower, Trim

from .contact_validation import normalize_email, normalize_phone


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "学生"
        TEACHER = "teacher", "教师"
        MAINTENANCE = "maintenance", "维修人员"
        ADMIN = "admin", "管理员"

    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "未通过"

    role = models.CharField(
        "角色", max_length=20, choices=Role.choices, default=Role.STUDENT
    )
    real_name = models.CharField("姓名", max_length=50, blank=True)
    phone = models.CharField("联系方式", max_length=30, blank=True)
    department = models.CharField("院系/部门", max_length=100, blank=True)
    organization_number = models.CharField("所属单位编号", max_length=100, blank=True)
    approval_status = models.CharField(
        "审核状态",
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    approval_reason = models.TextField("审核说明", blank=True)

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"
        constraints = [
            models.UniqueConstraint(
                Trim("phone"), condition=~models.Q(phone=""),
                name="accounts_unique_nonempty_phone",
            ),
            models.UniqueConstraint(
                Lower(Trim("email")), condition=~models.Q(email=""),
                name="accounts_unique_nonempty_email",
            ),
        ]

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        self.email = normalize_email(self.email)
        return super().save(*args, **kwargs)

    @property
    def can_handle_maintenance(self):
        if self.role != self.Role.MAINTENANCE or self.pk is None:
            return False
        return MaintenanceApplication.objects.filter(
            worker_id=self.pk,
            worker__role=self.Role.MAINTENANCE,
            worker__is_active=True,
            worker__approval_status=self.ApprovalStatus.APPROVED,
            status=MaintenanceApplication.Status.APPROVED,
        ).exists()


class TeacherReview(models.Model):
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="review_history")
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reviews_performed")
    result = models.CharField(max_length=20, choices=User.ApprovalStatus.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginProtection(models.Model):
    username = models.CharField(max_length=150, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(null=True)
    locked_until = models.DateTimeField(null=True)


class MaintenanceApplication(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "待提交"
        PENDING = "pending", "待审核"
        NEEDS_MORE = "needs_more", "待补充"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "未通过"

    worker = models.OneToOneField(User, on_delete=models.PROTECT, related_name="qualification_application")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class MaintenanceMaterial(models.Model):
    class Category(models.TextChoices):
        EMPLOYMENT = "employment", "工作关系证明"
        QUALIFICATION = "qualification", "专业资质证明"

    application = models.ForeignKey(MaintenanceApplication, on_delete=models.PROTECT, related_name="materials")
    category = models.CharField(max_length=20, choices=Category.choices)
    name = models.CharField("资质名称", max_length=150, blank=True)
    direction = models.CharField("专业方向", max_length=150, blank=True)
    certificate_category = models.CharField("证书类别", max_length=100, default="其他")
    certificate_number = models.CharField("证书编号", max_length=100, blank=True)
    issuer = models.CharField("发证机构", max_length=150, blank=True)
    issued_on = models.DateField("发证日期", null=True, blank=True)
    expires_on = models.DateField("有效期至", null=True, blank=True)
    permanent = models.BooleanField("长期有效", default=False)
    storage_name = models.CharField(max_length=200, unique=True)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    disposed_at = models.DateTimeField(null=True)


class MaintenanceReview(models.Model):
    application = models.ForeignKey(MaintenanceApplication, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    result = models.CharField(max_length=20, choices=MaintenanceApplication.Status.choices)
    reason = models.TextField(blank=True)
    materials = models.ManyToManyField(MaintenanceMaterial)
    created_at = models.DateTimeField(auto_now_add=True)


class MaintenanceDeactivation(models.Model):
    worker = models.ForeignKey(User, on_delete=models.PROTECT, related_name="deactivation_events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="deactivations_performed")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class MaterialDisposal(models.Model):
    worker = models.ForeignKey(User, on_delete=models.PROTECT)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="material_disposals")
    materials = models.ManyToManyField(MaintenanceMaterial)
    reason = models.TextField()
    policy_reference = models.CharField(max_length=200)
    status = models.CharField(max_length=20, default="started", choices=[("started", "处理中"), ("completed", "已完成"), ("failed", "失败待核验")])
    failure_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
