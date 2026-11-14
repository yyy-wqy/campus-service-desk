from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


class BuildingTypeChangeConflict(ValidationError):
    """楼栋仍有住宿档案时不能改成教学楼（并发下的最终保护）。"""


# 建筑楼栋模型（区分宿舍楼 / 教学楼）
class Building(models.Model):
    BUILDING_TYPE_CHOICES = [
        ("dorm", "宿舍楼"),
        ("teach", "教学楼"),
    ]
    name = models.CharField(max_length=100, verbose_name="楼栋名称")
    building_type = models.CharField(max_length=10, choices=BUILDING_TYPE_CHOICES, verbose_name="建筑类型")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    def __str__(self):
        return self.name

    def clean(self):
        """宿舍楼不能在有学生住宿档案时改成教学楼。"""

        super().clean()
        if not self.pk:
            return
        original_type = (
            type(self)
            ._default_manager.filter(pk=self.pk)
            .values_list("building_type", flat=True)
            .first()
        )
        if (
            original_type == "dorm"
            and self.building_type != "dorm"
            and StudentHousing.objects.filter(building_id=self.pk).exists()
        ):
            raise ValidationError(
                {
                    "building_type": (
                        "该楼栋仍有学生住宿档案，请先迁移关联学生，再修改楼栋类型。"
                    )
                }
            )

    def save(self, *args, **kwargs):
        """宿舍楼改教学楼时先写入、再在同一事务内复核住宿关联。

        先写入是为了取得数据库写锁：并发的学生换楼必须等本事务结束。
        若仍有住宿档案则抛错回滚，两个入口（业务页、Django 后台）共用本保护。
        """

        if not self.pk:
            return super().save(*args, **kwargs)

        original_type = (
            type(self)
            ._default_manager.filter(pk=self.pk)
            .values_list("building_type", flat=True)
            .first()
        )
        if original_type != "dorm" or self.building_type == "dorm":
            return super().save(*args, **kwargs)

        with transaction.atomic():
            super().save(*args, **kwargs)
            if StudentHousing.objects.filter(building_id=self.pk).exists():
                raise BuildingTypeChangeConflict(
                    "该楼栋仍有学生住宿档案，请先迁移关联学生，再修改楼栋类型。"
                )

    class Meta:
        verbose_name = "校园楼栋"
        verbose_name_plural = "校园楼栋"


# 故障分类模型（普通设施 / 教师专用设备）
class FaultCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name="分类名称")
    is_teacher_only = models.BooleanField(default=False, verbose_name="仅教师可报修")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "报修故障分类"
        verbose_name_plural = "报修故障分类"


# 公共区域固定选项（走廊 / 楼梯间 / 卫生间 / 大厅 / 其他）
class PublicAreaOption(models.Model):
    name = models.CharField("名称", max_length=50, unique=True)
    is_other = models.BooleanField("是“其他”选项", default=False)
    sort_order = models.PositiveSmallIntegerField("排序", default=0)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "公共区域选项"
        verbose_name_plural = "公共区域选项"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("is_other",),
                condition=models.Q(is_other=True),
                name="locations_single_other_public_area",
            )
        ]


# 学生住宿档案：一生一档，只记录当前楼栋
class StudentHousing(models.Model):
    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="housing",
        verbose_name="学生",
    )
    building = models.ForeignKey(
        Building,
        on_delete=models.PROTECT,
        related_name="residents",
        verbose_name="住宿楼栋",
        limit_choices_to={"building_type": "dorm"},
    )
    last_self_change_at = models.DateTimeField(
        "最近一次自助变更时间", null=True, blank=True
    )
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    def __str__(self):
        return f"{self.student} - {self.building}"

    class Meta:
        verbose_name = "学生住宿信息"
        verbose_name_plural = "学生住宿信息"


# 住宿变更留痕：只追加，不覆盖
class HousingChangeLog(models.Model):
    class Source(models.TextChoices):
        SELF = "self", "学生自助"
        ADMIN = "admin", "管理员"

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="housing_changes",
        verbose_name="学生",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="housing_changes_made",
        verbose_name="操作人",
    )
    source = models.CharField("变更来源", max_length=10, choices=Source.choices)
    from_building = models.ForeignKey(
        Building,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="原楼栋",
    )
    to_building = models.ForeignKey(
        Building,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="新楼栋",
    )
    from_building_name = models.CharField("原楼栋名称快照", max_length=100, blank=True)
    to_building_name = models.CharField("新楼栋名称快照", max_length=100)
    created_at = models.DateTimeField("记录时间", auto_now_add=True)

    def __str__(self):
        return f"{self.student} {self.from_building_name or '未填写'} → {self.to_building_name}"

    class Meta:
        verbose_name = "住宿变更记录"
        verbose_name_plural = "住宿变更记录"
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("student", "created_at"), name="housing_log_student_time_idx")
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("住宿变更记录只能追加，不能更新。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("住宿变更记录只能追加，不能删除。")
