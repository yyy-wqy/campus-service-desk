from django.db import models

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
