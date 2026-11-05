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
