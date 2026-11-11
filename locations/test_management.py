from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from locations.forms import PublicAreaOptionForm
from locations.models import Building, FaultCategory, PublicAreaOption


def make_user(username, role):
    return User.objects.create_user(
        username=username,
        password="test-password",
        role=role,
        approval_status=User.ApprovalStatus.APPROVED,
        phone=f"136{username[-8:]}",
    )


class ManagePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin000020", User.Role.ADMIN)
        cls.student = make_user("student00020", User.Role.STUDENT)
        cls.dorm = Building.objects.create(name="1号宿舍楼", building_type="dorm")

    def test_admin_can_open_manage_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("locations:manage"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "基础资料管理")
        self.assertContains(response, "1号宿舍楼")

    def test_student_cannot_open_or_change_base_data(self):
        self.client.force_login(self.student)

        self.assertEqual(self.client.get(reverse("locations:manage")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("locations:building_create")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("locations:building_create"),
                {"name": "偷偷加的楼", "building_type": "teach", "is_active": "on"},
            ).status_code,
            403,
        )
        self.assertFalse(Building.objects.filter(name="偷偷加的楼").exists())

        self.assertEqual(
            self.client.post(
                reverse("locations:manage_toggle", args=["building", self.dorm.pk])
            ).status_code,
            403,
        )
        self.dorm.refresh_from_db()
        self.assertTrue(self.dorm.is_active)

    def test_admin_can_create_rename_and_disable(self):
        self.client.force_login(self.admin)

        self.client.post(
            reverse("locations:building_create"),
            {"name": "2号宿舍楼", "building_type": "dorm", "is_active": "on"},
        )
        building = Building.objects.get(name="2号宿舍楼")

        self.client.post(
            reverse("locations:building_edit", args=[building.pk]),
            {"name": "2号学生公寓", "building_type": "dorm", "is_active": "on"},
        )
        building.refresh_from_db()
        self.assertEqual(building.name, "2号学生公寓")

        self.client.post(reverse("locations:manage_toggle", args=["building", building.pk]))
        building.refresh_from_db()
        self.assertFalse(building.is_active)

        self.client.post(
            reverse("locations:category_create"),
            {"name": "投影仪故障", "is_teacher_only": "on", "is_active": "on"},
        )
        self.assertTrue(FaultCategory.objects.filter(name="投影仪故障", is_teacher_only=True).exists())

        area = PublicAreaOption.objects.get(name="走廊")
        self.client.post(
            reverse("locations:public_area_edit", args=[area.pk]),
            {"name": "走廊", "sort_order": 3, "is_active": "on"},
        )
        area.refresh_from_db()
        self.assertEqual(area.sort_order, 3)

    def test_renamed_building_is_used_by_new_selections(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("locations:building_edit", args=[self.dorm.pk]),
            {"name": "1号学生公寓", "building_type": "dorm", "is_active": "on"},
        )

        self.client.force_login(self.student)
        response = self.client.get(reverse("locations:housing"))
        self.assertContains(response, "1号学生公寓")


class PublicAreaOptionRuleTests(TestCase):
    def test_only_one_other_option_is_allowed(self):
        # 默认数据里已经有“其他”选项
        self.assertTrue(PublicAreaOption.objects.filter(name="其他", is_other=True).exists())

        form = PublicAreaOptionForm(
            data={"name": "另一个其他", "sort_order": 10, "is_other": "on", "is_active": "on"}
        )

        self.assertFalse(form.is_valid())
        self.assertIn("is_other", form.errors)


class DjangoAdminProtectionTests(TestCase):
    def test_base_data_cannot_be_deleted_in_django_admin(self):
        for model in (Building, FaultCategory, PublicAreaOption):
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_delete_permission(request=None))
