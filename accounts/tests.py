from django.contrib.auth import SESSION_KEY
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse

from .models import User
from .urls import urlpatterns as account_urls


PASSWORD = "Campus!Repair-2026"


def business_page(request):
    return HttpResponse("business content")


# Exercise the middleware against a real resolved business endpoint, without
# adding placeholder business pages to the application.
urlpatterns = [
    path("accounts/", include((account_urls + [path("protected/", business_page)], "accounts"))),
    path("", include("core.urls")),
    path("business/", include(([path("", business_page, name="test")], "tickets"))),
]


class RegistrationTests(TestCase):
    def payload(self, **extra):
        return {
            "username": "20261015001", "real_name": "测试用户",
            "phone": "13800138000", "role": "student",
            "password1": PASSWORD, "password2": PASSWORD, **extra,
        }

    def test_only_three_public_roles_are_offered(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="student"')
        self.assertContains(response, 'value="teacher"')
        self.assertContains(response, 'value="maintenance"')
        self.assertNotContains(response, 'value="admin"')

    def test_all_public_roles_register_with_server_controlled_status(self):
        for index, role in enumerate(("student", "teacher", "maintenance")):
            with self.subTest(role=role):
                phone = f"1380013800{index}"
                response = self.client.post(reverse("accounts:register"), self.payload(
                    username=role, role=role, phone=phone, approval_status="approved",
                    is_staff="true", is_superuser="true", approval_reason="forged",
                ), follow=True)
                self.assertRedirects(response, reverse("accounts:login"))
                user = User.objects.get(username=role)
                self.assertEqual(user.role, role)
                expected = "approved" if role == "student" else "pending"
                self.assertEqual(user.approval_status, expected)
                self.assertEqual(user.real_name, "测试用户")
                self.assertEqual(user.phone, phone)
                self.assertTrue(user.check_password(PASSWORD))
                self.assertNotEqual(user.password, PASSWORD)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertEqual(user.approval_reason, "")
                self.assertNotIn(SESSION_KEY, self.client.session)
                self.assertContains(response, "注册成功")
                if role != "student":
                    self.assertContains(response, "账号待审核")

    def test_forged_admin_and_invalid_roles_cannot_register(self):
        for role in ("admin", "superuser", ""):
            with self.subTest(role=role):
                response = self.client.post(reverse("accounts:register"), self.payload(role=role))
                self.assertEqual(response.status_code, 200)
                self.assertIn("role", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(username="20261015001", password=PASSWORD)
        response = self.client.post(reverse("accounts:register"), self.payload())
        self.assertIn("username", response.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_passwords_and_missing_details_do_not_create_accounts(self):
        cases = (
            {"password2": "different"}, {"password1": "123", "password2": "123"},
            {"real_name": ""}, {"phone": ""}, {"username": ""},
        )
        for extra in cases:
            with self.subTest(extra=extra):
                response = self.client.post(reverse("accounts:register"), self.payload(**extra))
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_new_student_can_log_in(self):
        self.client.post(reverse("accounts:register"), self.payload())
        response = self.client.post(reverse("accounts:login"), {
            "username": "20261015001", "password": PASSWORD,
        }, follow=True)
        self.assertRedirects(response, reverse("core:home"))
        self.assertContains(response, "退出登录")


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for role in User.Role.values:
            User.objects.create_user(username=role, password=PASSWORD, role=role, approval_status="approved")

    def login(self, account="student", **extra):
        return self.client.post(reverse("accounts:login"), {
            "username": account, "password": PASSWORD, **extra,
        })

    def test_login_page_only_requests_account_and_password(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertNotContains(response, 'name="role"')
        self.assertNotIn("role", response.context["form"].fields)

    def test_approved_roles_can_log_in_without_supplying_role(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                response = self.login(role)
                self.assertRedirects(response, reverse("core:home"))
                user = User.objects.get(username=role)
                self.assertEqual(self.client.session[SESSION_KEY], str(user.pk))
                home = self.client.get(reverse("core:home"))
                self.assertEqual(home.context["user"].role, role)
                self.assertContains(home, user.get_role_display())
                self.client.logout()

    def test_submitted_role_cannot_override_database_role(self):
        for role in ("admin", "", "invalid"):
            with self.subTest(role=role):
                response = self.login(role=role)
                self.assertRedirects(response, reverse("core:home"))
                home = self.client.get(reverse("core:home"))
                self.assertEqual(home.context["user"].role, "student")
                self.assertContains(home, "提交报修")
                self.assertNotContains(home, "用户审核")
                self.assertEqual(User.objects.get(username="student").role, "student")
                self.client.logout()

    def test_wrong_password_unknown_account_and_inactive_account_are_rejected(self):
        for extra in ({"password": "wrong"}, {"username": "unknown"}):
            with self.subTest(extra=extra):
                response = self.login(**extra)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertNotIn(SESSION_KEY, self.client.session)
        User.objects.filter(username="student").update(is_active=False)
        response = self.login()
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_invalid_database_role_cannot_log_in(self):
        User.objects.filter(username="student").update(role="invalid")
        response = self.login()
        self.assertContains(response, "账号角色异常")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_pending_and_rejected_accounts_cannot_log_in(self):
        for role in ("teacher", "maintenance"):
            for status, text in (("pending", "账号待审核"), ("rejected", "账号审核未通过")):
                with self.subTest(role=role, status=status):
                    User.objects.filter(username=role).update(approval_status=status, approval_reason="请核实工号")
                    response = self.login(role, role="admin")
                    self.assertContains(response, text)
                    if status == "rejected":
                        self.assertContains(response, "请核实工号")
                    self.assertNotIn(SESSION_KEY, self.client.session)

    def test_rejection_reason_is_escaped(self):
        User.objects.filter(username="teacher").update(approval_status="rejected", approval_reason="<script>alert(1)</script>")
        response = self.login("teacher")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_external_next_cannot_redirect_off_site(self):
        for target in ("https://example.com/", "//example.com/"):
            with self.subTest(target=target):
                response = self.login(next=target)
                self.assertRedirects(response, reverse("core:home"))
                self.client.logout()

    def test_safe_next_is_preserved_on_error_and_used_on_success(self):
        response = self.login(password="wrong", next="/?from=login")
        self.assertContains(response, 'value="/?from=login"')
        response = self.login(next="/?from=login")
        self.assertRedirects(response, "/?from=login")

    def test_logout_requires_post_and_clears_session(self):
        self.login()
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertIn(SESSION_KEY, self.client.session)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_authentication_forms_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        for name in ("register", "login", "logout"):
            self.assertEqual(client.post(reverse(f"accounts:{name}"), {}).status_code, 403)
        client.get(reverse("accounts:login"))
        response = client.post(reverse("accounts:login"), {
            "username": "student", "password": PASSWORD,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)


@override_settings(ROOT_URLCONF="accounts.tests")
class BusinessAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="teacher", password=PASSWORD, role="teacher", approval_status="approved")

    def test_anonymous_business_request_redirects_to_login(self):
        response = self.client.get("/business/?page=2")
        self.assertRedirects(response, reverse("accounts:login") + "?next=/business/%3Fpage%3D2")
        response = self.client.get("/accounts/protected/")
        self.assertRedirects(response, reverse("accounts:login") + "?next=/accounts/protected/")

    def test_approved_account_can_access_business_page(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/business/"), "business content")

    def test_existing_session_is_blocked_after_review_status_changes(self):
        self.client.force_login(self.user)
        for status in ("pending", "rejected", "invalid"):
            User.objects.filter(pk=self.user.pk).update(approval_status=status, approval_reason="需补充资料")
            for url in ("/", "/business/", "/accounts/protected/"):
                with self.subTest(status=status, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotContains(response, "business content", status_code=403)
                    self.assertNotContains(response, "提交报修", status_code=403)
                    if status == "rejected":
                        self.assertContains(response, "需补充资料", status_code=403)
        # The blocked user still has a working way to clear the session.
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_unknown_role_is_blocked(self):
        User.objects.filter(pk=self.user.pk).update(role="unknown")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/business/").status_code, 403)
