from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import User
from locations.housing import (
    HOUSING_CHANGE_INTERVAL,
    HousingChangeTooSoon,
    change_own_building,
    set_building_by_admin,
)
from locations.models import Building, HousingChangeLog, StudentHousing


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(
        username=username,
        password="test-password",
        role=role,
        approval_status=User.ApprovalStatus.APPROVED,
        phone=f"138{username[-8:]}",
    )


class HousingChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dorm_a = Building.objects.create(name="1号宿舍楼", building_type="dorm")
        cls.dorm_b = Building.objects.create(name="2号宿舍楼", building_type="dorm")
        cls.dorm_c = Building.objects.create(name="3号宿舍楼", building_type="dorm")
        cls.teach = Building.objects.create(name="1号教学楼", building_type="teach")

    def setUp(self):
        self.student = make_user("student00001")

    def test_first_fill_does_not_start_cooldown(self):
        housing = change_own_building(self.student, self.dorm_a)

        self.assertIsNone(housing.last_self_change_at)
        log = HousingChangeLog.objects.get(student=self.student)
        self.assertEqual(log.source, HousingChangeLog.Source.SELF)
        self.assertIsNone(log.from_building)
        self.assertEqual(log.to_building_name, "1号宿舍楼")

    def test_first_change_allowed_then_second_rejected(self):
        change_own_building(self.student, self.dorm_a)
        change_own_building(self.student, self.dorm_b)

        with self.assertRaises(HousingChangeTooSoon):
            change_own_building(self.student, self.dorm_c)

        self.assertEqual(StudentHousing.objects.get(student=self.student).building, self.dorm_b)

    def test_thirty_day_boundary(self):
        start = timezone.now()
        with patch("locations.housing.timezone.now", return_value=start):
            change_own_building(self.student, self.dorm_a)
        with patch("locations.housing.timezone.now", return_value=start):
            change_own_building(self.student, self.dorm_b)

        just_before = start + HOUSING_CHANGE_INTERVAL - timedelta(seconds=1)
        with patch("locations.housing.timezone.now", return_value=just_before):
            with self.assertRaises(HousingChangeTooSoon):
                change_own_building(self.student, self.dorm_c)

        exactly = start + HOUSING_CHANGE_INTERVAL
        with patch("locations.housing.timezone.now", return_value=exactly):
            change_own_building(self.student, self.dorm_c)

        self.assertEqual(StudentHousing.objects.get(student=self.student).building, self.dorm_c)

    def test_same_building_repeat_is_free(self):
        start = timezone.now()
        with patch("locations.housing.timezone.now", return_value=start):
            change_own_building(self.student, self.dorm_a)
            change_own_building(self.student, self.dorm_b)
            change_own_building(self.student, self.dorm_b)

        housing = StudentHousing.objects.get(student=self.student)
        self.assertEqual(housing.building, self.dorm_b)
        self.assertEqual(housing.last_self_change_at, start)
        # 首次填写 1 条 + 一次实际变更 1 条 = 2 条
        self.assertEqual(HousingChangeLog.objects.filter(student=self.student).count(), 2)

    def test_change_does_not_touch_other_profile_fields(self):
        self.student.real_name = "张三"
        self.student.phone = "13900001111"
        self.student.save()

        change_own_building(self.student, self.dorm_a)

        self.student.refresh_from_db()
        self.assertEqual(self.student.real_name, "张三")
        self.assertEqual(self.student.phone, "13900001111")

    def test_rejects_teaching_building_and_inactive_dorm(self):
        with self.assertRaises(ValidationError):
            change_own_building(self.student, self.teach)

        self.dorm_a.is_active = False
        self.dorm_a.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            change_own_building(self.student, self.dorm_a)

    def test_admin_can_change_anytime_without_touching_cooldown(self):
        start = timezone.now()
        admin = make_user("admin000001", role=User.Role.ADMIN)
        with patch("locations.housing.timezone.now", return_value=start):
            change_own_building(self.student, self.dorm_a)
            change_own_building(self.student, self.dorm_b)

        with patch("locations.housing.timezone.now", return_value=start + timedelta(minutes=1)):
            set_building_by_admin(self.student, self.dorm_c, admin)

        housing = StudentHousing.objects.get(student=self.student)
        self.assertEqual(housing.building, self.dorm_c)
        # 管理员改动不重置学生冷却
        self.assertEqual(housing.last_self_change_at, start)

        # 冷却仍未结束，学生不能自助换楼
        with patch("locations.housing.timezone.now", return_value=start + timedelta(minutes=2)):
            with self.assertRaises(HousingChangeTooSoon):
                change_own_building(self.student, self.dorm_a)

        log = HousingChangeLog.objects.filter(student=self.student).latest("created_at", "pk")
        self.assertEqual(log.source, HousingChangeLog.Source.ADMIN)
        self.assertEqual(log.actor, admin)
        self.assertEqual(log.from_building_name, "2号宿舍楼")
        self.assertEqual(log.to_building_name, "3号宿舍楼")

    def test_change_log_is_append_only(self):
        change_own_building(self.student, self.dorm_a)
        log = HousingChangeLog.objects.get(student=self.student)

        log.to_building_name = "被篡改"
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()


class HousingConcurrencyTests(TransactionTestCase):
    def test_concurrent_changes_apply_once(self):
        dorm_a = Building.objects.create(name="A栋", building_type="dorm")
        dorm_b = Building.objects.create(name="B栋", building_type="dorm")
        dorm_c = Building.objects.create(name="C栋", building_type="dorm")
        student = make_user("student00002")
        StudentHousing.objects.create(
            student=student,
            building=dorm_a,
            last_self_change_at=timezone.now() - HOUSING_CHANGE_INTERVAL - timedelta(days=1),
        )

        def attempt(target):
            close_old_connections()
            try:
                change_own_building(student, target)
                return True
            except Exception:
                return False
            finally:
                close_old_connections()

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [dorm_b, dorm_c]))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertIsNotNone(StudentHousing.objects.get(student=student).last_self_change_at)
