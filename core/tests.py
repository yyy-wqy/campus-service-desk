from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class PublicPageTests(TestCase):
    def test_home_page_renders_public_service_content(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "校园设施报修，从提交到完成全程可追踪")
        self.assertContains(response, "登录")
        self.assertContains(response, "创建账号")

    def test_login_preview_uses_account_credentials_without_role_selector(self):
        response = self.client.get(reverse("core:login_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertNotContains(response, 'name="role"')


class RoleNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.users = {
            role: user_model.objects.create_user(
                username=f"{role}_user",
                password="test-password",
                role=role,
            )
            for role in ("student", "teacher", "maintenance", "admin")
        }

    def assert_navigation(self, role, visible, hidden):
        self.client.force_login(self.users[role])
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        for label in visible:
            with self.subTest(role=role, visible=label):
                self.assertContains(response, label)
        for label in hidden:
            with self.subTest(role=role, hidden=label):
                self.assertNotContains(response, label)

    def test_reporter_navigation(self):
        for role in ("student", "teacher"):
            self.assert_navigation(
                role,
                visible=("提交报修", "我的工单", "个人信息"),
                hidden=("我的任务", "用户审核", "基础资料管理"),
            )

    def test_maintenance_navigation(self):
        self.assert_navigation(
            "maintenance",
            visible=("我的任务", "个人信息"),
            hidden=("提交报修", "我的工单", "用户审核"),
        )

    def test_admin_navigation(self):
        self.assert_navigation(
            "admin",
            visible=("工作概览", "工单管理", "取消申请", "用户审核", "基础资料管理"),
            hidden=("提交报修", "我的任务"),
        )
