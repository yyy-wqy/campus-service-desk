from django.contrib.auth.models import AbstractUser
from django.db import models


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
