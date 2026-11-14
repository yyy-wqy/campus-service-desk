from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from locations.db_errors import LOCK_CONFLICT_MESSAGE
from locations.models import Building


class BuildingLockConflictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(
            username="lock_test_admin",
            password="test-password",
            role=User.Role.ADMIN,
            approval_status=User.ApprovalStatus.APPROVED,
            is_staff=True,
        )
        cls.dorm = Building.objects.create(
            name="锁冲突测试宿舍",
            building_type="dorm",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)

    def endpoints(self):
        return [
            reverse("locations:building_edit", args=[self.dorm.pk]),
            f"/admin/locations/building/{self.dorm.pk}/change/",
        ]

    def data(self):
        return {
            "name": "修改后的名称",
            "building_type": "teach",
            "is_active": "on",
        }

    def test_both_pages_show_lock_conflict_message(self):
        for url in self.endpoints():
            with self.subTest(url=url):
                with patch.object(
                    Building,
                    "save",
                    side_effect=OperationalError("database is locked"),
                ):
                    response = self.client.post(url, self.data())

                if url.startswith("/admin/"):
                    self.assertRedirects(response, url, fetch_redirect_response=False)
                    response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, LOCK_CONFLICT_MESSAGE)

                self.dorm.refresh_from_db()
                self.assertEqual(self.dorm.name, "锁冲突测试宿舍")
                self.assertEqual(self.dorm.building_type, "dorm")

    def test_other_database_errors_are_not_hidden(self):
        for url in self.endpoints():
            with self.subTest(url=url):
                with patch.object(
                    Building,
                    "save",
                    side_effect=OperationalError("no such table: example"),
                ):
                    with self.assertRaises(OperationalError):
                        self.client.post(url, self.data())


class RealSQLiteLockTests(TransactionTestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="real_lock_admin",
            password="test-password",
            role=User.Role.ADMIN,
            approval_status=User.ApprovalStatus.APPROVED,
            is_staff=True,
        )
        self.dorm = Building.objects.create(
            name="Real lock dorm",
            building_type="dorm",
        )
        self.client.force_login(self.admin_user)

    def test_real_sqlite_lock_on_both_pages(self):
        import sqlite3
        from django.db import connection

        if connection.vendor != "sqlite":
            self.skipTest("SQLite-specific test")

        original_save = Building.save
        database_name = str(connection.settings_dict["NAME"])
        urls = [
            reverse("locations:building_edit", args=[self.dorm.pk]),
            f"/admin/locations/building/{self.dorm.pk}/change/",
        ]

        for url in urls:
            with self.subTest(url=url):
                lock_acquired = []

                def save_with_competing_writer(instance, *args, **kwargs):
                    other = sqlite3.connect(
                        database_name,
                        uri=True,
                        timeout=0.1,
                        isolation_level=None,
                    )
                    try:
                        other.execute("BEGIN IMMEDIATE")
                        lock_acquired.append(True)
                        return original_save(instance, *args, **kwargs)
                    finally:
                        other.rollback()
                        other.close()

                with patch.object(Building, "save", save_with_competing_writer):
                    response = self.client.post(
                        url,
                        {
                            "name": "Changed name",
                            "building_type": "teach",
                            "is_active": "on",
                        },
                    )

                self.assertTrue(lock_acquired)

                if url.startswith("/admin/"):
                    self.assertRedirects(
                        response, url, fetch_redirect_response=False
                    )
                    response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, LOCK_CONFLICT_MESSAGE)

                self.dorm.refresh_from_db()
                self.assertEqual(self.dorm.name, "Real lock dorm")
                self.assertEqual(self.dorm.building_type, "dorm")
