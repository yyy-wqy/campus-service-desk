from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User
from locations.models import Building, FaultCategory, PublicAreaOption, StudentHousing
from locations.scope import (
    validate_repair_scope,
    reportable_buildings,
    reportable_categories,
)


def make_user(username, role=User.Role.STUDENT, approved=True):
    return User.objects.create_user(
        username=username,
        password="test-password",
        role=role,
        approval_status=(
            User.ApprovalStatus.APPROVED if approved else User.ApprovalStatus.PENDING
        ),
        phone=f"137{username[-8:]}",
    )


class RepairScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dorm = Building.objects.create(name="1号宿舍楼", building_type="dorm")
        cls.other_dorm = Building.objects.create(name="2号宿舍楼", building_type="dorm")
        cls.teach = Building.objects.create(name="1号教学楼", building_type="teach")
        cls.normal = FaultCategory.objects.create(name="灯具损坏", is_teacher_only=False)
        cls.teacher_only = FaultCategory.objects.create(name="投影仪故障", is_teacher_only=True)
        # 默认选项由数据迁移写入，这里直接复用，避免与种子数据重名冲突。
        cls.corridor, _ = PublicAreaOption.objects.get_or_create(
            name="走廊", defaults={"sort_order": 1}
        )
        cls.other_area, _ = PublicAreaOption.objects.get_or_create(
            name="其他", defaults={"sort_order": 9, "is_other": True}
        )

    def setUp(self):
        self.student = make_user("student00010")
        StudentHousing.objects.create(student=self.student, building=self.dorm)
        self.teacher = make_user("teacher00010", role=User.Role.TEACHER)

    def scope(self, **kwargs):
        params = {
            "reporter": self.student,
            "building": self.dorm,
            "fault_category": self.normal,
            "location_kind": "room",
            "room_number": "301",
        }
        params.update(kwargs)
        return validate_repair_scope(**params)

    def test_student_own_dorm_room_passes(self):
        result = self.scope()

        self.assertEqual(result["location_detail"], "301")

    def test_student_other_dorm_room_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=self.other_dorm)

        self.assertIn("building", ctx.exception.message_dict)

    def test_student_without_housing_cannot_report_dorm_but_can_report_teaching(self):
        student = make_user("student00011")

        with self.assertRaises(ValidationError) as ctx:
            self.scope(reporter=student)
        self.assertIn("building", ctx.exception.message_dict)

        result = self.scope(reporter=student, building=self.teach)
        self.assertEqual(result["location_detail"], "301")

    def test_student_cannot_use_teacher_only_category(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=self.teach, fault_category=self.teacher_only)

        self.assertIn("fault_category", ctx.exception.message_dict)

    def test_teacher_can_report_teaching_building_and_teacher_only(self):
        result = self.scope(
            reporter=self.teacher,
            building=self.teach,
            fault_category=self.teacher_only,
        )

        self.assertEqual(result["location_detail"], "301")

    def test_teacher_cannot_report_dorm(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(reporter=self.teacher, building=self.dorm)

        self.assertIn("building", ctx.exception.message_dict)

    def test_inactive_building_or_category_rejected(self):
        building = Building.objects.create(name="停用楼", building_type="teach", is_active=False)
        category = FaultCategory.objects.create(name="停用类别", is_active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=building)
        self.assertIn("building", ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=self.teach, fault_category=category)
        self.assertIn("fault_category", ctx.exception.message_dict)

    def test_room_number_and_public_area_are_exclusive(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="public_area", public_area=self.corridor)
        self.assertIn("room_number", ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="room", room_number="301", public_area=self.corridor)
        self.assertIn("public_area", ctx.exception.message_dict)

    def test_room_number_is_required(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(room_number="   ")

        self.assertIn("room_number", ctx.exception.message_dict)

    def test_public_area_requires_an_option(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="public_area", room_number="")

        self.assertIn("public_area", ctx.exception.message_dict)

    def test_inactive_public_area_rejected(self):
        area = PublicAreaOption.objects.create(name="旧大厅", is_active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="public_area", room_number="", public_area=area)

        self.assertIn("public_area", ctx.exception.message_dict)

    def test_other_requires_text_and_keeps_it_in_snapshot(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(
                location_kind="public_area",
                room_number="",
                public_area=self.other_area,
                public_area_other="   ",
            )
        self.assertIn("public_area_other", ctx.exception.message_dict)

        result = self.scope(
            location_kind="public_area",
            room_number="",
            public_area=self.other_area,
            public_area_other=" 三楼东侧 ",
        )
        self.assertEqual(result["location_detail"], "其他（三楼东侧）")

    def test_other_text_is_rejected_for_normal_public_area(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(
                location_kind="public_area",
                room_number="",
                public_area=self.corridor,
                public_area_other="不该出现的文字",
            )

        self.assertIn("public_area_other", ctx.exception.message_dict)

    def test_other_text_is_rejected_for_room_location(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(
                location_kind="room",
                room_number="301",
                public_area_other="三楼东侧",
            )

        self.assertIn("public_area_other", ctx.exception.message_dict)

    def test_invalid_location_kind_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="floor")

        self.assertIn("location_kind", ctx.exception.message_dict)

    def test_unapproved_or_wrong_role_cannot_report(self):
        pending = make_user("student00012", approved=False)
        maintenance = make_user("maint000010", role=User.Role.MAINTENANCE)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(reporter=pending, building=self.teach)
        self.assertIn("reporter", ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(reporter=maintenance, building=self.teach)
        self.assertIn("reporter", ctx.exception.message_dict)

    def test_dropdown_helpers_follow_role_scope(self):
        self.assertEqual(
            list(reportable_buildings(self.student).values_list("name", flat=True)),
            ["1号宿舍楼", "1号教学楼"],
        )
        self.assertEqual(
            list(reportable_categories(self.student).values_list("name", flat=True)),
            ["灯具损坏"],
        )
        self.assertEqual(
            list(reportable_buildings(self.teacher).values_list("name", flat=True)),
            ["1号教学楼"],
        )


class FreshDataTests(TestCase):
    """校验必须读数据库当前值，调用方缓存的旧对象不能放行。"""

    @classmethod
    def setUpTestData(cls):
        cls.dorm = Building.objects.create(name="新鲜A栋", building_type="dorm")
        cls.dorm_new = Building.objects.create(name="新鲜B栋", building_type="dorm")
        cls.teach = Building.objects.create(name="新鲜教学楼", building_type="teach")
        cls.category = FaultCategory.objects.create(name="新鲜灯具")
        cls.area, _ = PublicAreaOption.objects.get_or_create(
            name="走廊", defaults={"sort_order": 1}
        )

    def setUp(self):
        self.student = make_user("student00060")
        StudentHousing.objects.create(student=self.student, building=self.dorm)

    def scope(self, **kwargs):
        params = {
            "reporter": self.student,
            "building": self.dorm,
            "fault_category": self.category,
            "location_kind": "room",
            "room_number": "301",
        }
        params.update(kwargs)
        return validate_repair_scope(**params)

    def test_cached_old_dorm_is_rejected_after_admin_migration(self):
        stale_dorm = Building.objects.get(pk=self.dorm.pk)
        StudentHousing.objects.filter(student=self.student).update(building=self.dorm_new)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=stale_dorm)
        self.assertIn("building", ctx.exception.message_dict)

        # 新宿舍可以正常报修
        self.scope(building=self.dorm_new)

    def test_cached_reporter_housing_is_not_trusted(self):
        self.student.housing  # 触发反向关系缓存
        StudentHousing.objects.filter(student=self.student).update(building=self.dorm_new)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=self.dorm)
        self.assertIn("building", ctx.exception.message_dict)

    def test_disabled_building_cannot_pass_with_cached_object(self):
        stale = Building.objects.get(pk=self.dorm.pk)
        Building.objects.filter(pk=self.dorm.pk).update(is_active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=stale)
        self.assertIn("building", ctx.exception.message_dict)

    def test_disabled_category_cannot_pass_with_cached_object(self):
        stale = FaultCategory.objects.get(pk=self.category.pk)
        FaultCategory.objects.filter(pk=self.category.pk).update(is_active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(fault_category=stale)
        self.assertIn("fault_category", ctx.exception.message_dict)

    def test_disabled_public_area_cannot_pass_with_cached_object(self):
        stale = PublicAreaOption.objects.get(pk=self.area.pk)
        PublicAreaOption.objects.filter(pk=self.area.pk).update(is_active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(location_kind="public_area", room_number="", public_area=stale)
        self.assertIn("public_area", ctx.exception.message_dict)

    def test_teacher_only_flag_is_read_from_database(self):
        stale = FaultCategory.objects.get(pk=self.category.pk)
        FaultCategory.objects.filter(pk=self.category.pk).update(is_teacher_only=True)

        with self.assertRaises(ValidationError) as ctx:
            self.scope(building=self.teach, fault_category=stale)
        self.assertIn("fault_category", ctx.exception.message_dict)
