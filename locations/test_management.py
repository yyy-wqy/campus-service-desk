from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from locations.forms import PublicAreaOptionForm
from locations.models import (
    Building,
    FaultCategory,
    HousingChangeLog,
    PublicAreaOption,
    StudentHousing,
)


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

    def test_teacher_with_model_permissions_cannot_use_django_admin(self):
        """Django 模型权限不能越过业务角色限制。"""

        dorm = Building.objects.create(name="后台越权栋", building_type="dorm")
        teacher = make_user("teacher00030", User.Role.TEACHER)
        teacher.is_staff = True
        teacher.save(update_fields=["is_staff"])
        teacher.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="locations")
        )

        client = Client()
        client.force_login(teacher)

        self.assertEqual(client.get("/admin/locations/building/").status_code, 403)
        self.assertEqual(client.get("/admin/locations/studenthousing/").status_code, 403)
        self.assertEqual(
            client.post(
                f"/admin/locations/building/{dorm.pk}/change/",
                {"name": "被教师改掉", "building_type": "dorm", "is_active": "on"},
            ).status_code,
            403,
        )
        dorm.refresh_from_db()
        self.assertEqual(dorm.name, "后台越权栋")

    def test_student_housing_is_read_only_even_for_business_admin(self):
        student = make_user("student00031", User.Role.STUDENT)
        admin_user = make_user("admin000031", User.Role.ADMIN)
        admin_user.is_staff = True
        admin_user.save(update_fields=["is_staff"])
        dorm = Building.objects.create(name="只读栋", building_type="dorm")
        housing = StudentHousing.objects.create(student=student, building=dorm)

        client = Client()
        client.force_login(admin_user)

        self.assertEqual(client.get("/admin/locations/studenthousing/").status_code, 200)
        # 后台只读：可以看，但不能通过后台改楼栋
        self.assertEqual(
            client.get(f"/admin/locations/studenthousing/{housing.pk}/change/").status_code,
            200,
        )
        other_dorm = Building.objects.create(name="另一栋", building_type="dorm")
        self.assertEqual(
            client.post(
                f"/admin/locations/studenthousing/{housing.pk}/change/",
                {"student": student.pk, "building": other_dorm.pk},
            ).status_code,
            403,
        )
        housing.refresh_from_db()
        self.assertEqual(housing.building, dorm)


class AdminHousingPageTests(TestCase):
    """管理员代改住宿必须留痕：操作人、时间和前后楼栋。"""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = make_user("admin000040", User.Role.ADMIN)
        cls.student = make_user("student00040", User.Role.STUDENT)
        cls.dorm_a = Building.objects.create(name="管理A栋", building_type="dorm")
        cls.dorm_b = Building.objects.create(name="管理B栋", building_type="dorm")

    def setUp(self):
        StudentHousing.objects.create(student=self.student, building=self.dorm_a)

    def test_admin_change_is_logged_with_actor_and_before_after(self):
        client = Client()
        client.force_login(self.admin_user)

        response = client.post(
            reverse("locations:manage_housing"),
            {"student": self.student.pk, "building": self.dorm_b.pk},
        )

        self.assertRedirects(response, reverse("locations:manage_housing"))
        housing = StudentHousing.objects.get(student=self.student)
        self.assertEqual(housing.building, self.dorm_b)

        log = HousingChangeLog.objects.get(student=self.student)
        self.assertEqual(log.source, HousingChangeLog.Source.ADMIN)
        self.assertEqual(log.actor, self.admin_user)
        self.assertEqual(log.from_building_name, "管理A栋")
        self.assertEqual(log.to_building_name, "管理B栋")
        # 管理员改动不启动学生冷却
        self.assertIsNone(housing.last_self_change_at)

    def test_admin_can_fill_housing_for_student_without_record(self):
        newcomer = make_user("student00041", User.Role.STUDENT)
        client = Client()
        client.force_login(self.admin_user)

        client.post(
            reverse("locations:manage_housing"),
            {"student": newcomer.pk, "building": self.dorm_a.pk},
        )

        log = HousingChangeLog.objects.get(student=newcomer)
        self.assertIsNone(log.from_building)
        self.assertEqual(log.from_building_name, "")
        self.assertEqual(log.to_building_name, "管理A栋")

    def test_non_admin_cannot_open_or_submit_manage_housing(self):
        for user in (self.student, make_user("teacher00041", User.Role.TEACHER)):
            with self.subTest(role=user.role):
                client = Client()
                client.force_login(user)
                self.assertEqual(
                    client.get(reverse("locations:manage_housing")).status_code, 403
                )
                self.assertEqual(
                    client.post(
                        reverse("locations:manage_housing"),
                        {"student": self.student.pk, "building": self.dorm_b.pk},
                    ).status_code,
                    403,
                )

        self.assertEqual(
            StudentHousing.objects.get(student=self.student).building, self.dorm_a
        )
