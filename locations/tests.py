from django.test import TestCase
from .models import Building, FaultCategory

class ModelTestCase(TestCase):
    def test_create_building(self):
        # 测试教学楼
        teach_building = Building.objects.create(name="一号教学楼", building_type="teach")
        self.assertEqual(teach_building.name, "一号教学楼")
        self.assertEqual(teach_building.building_type, "teach")

        # 测试宿舍楼
        dorm_building = Building.objects.create(name="3号宿舍楼", building_type="dorm")
        self.assertEqual(dorm_building.building_type, "dorm")

    def test_fault_category(self):
        # 普通报修分类
        cat1 = FaultCategory.objects.create(name="电灯损坏", is_teacher_only=False)
        self.assertEqual(cat1.is_teacher_only, False)

        # 教师专用报修分类
        cat2 = FaultCategory.objects.create(name="投影仪故障", is_teacher_only=True)
        self.assertEqual(cat2.is_teacher_only, True)
