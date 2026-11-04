import io
import re
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import User


OLD_PASSWORD = "Campus!Repair-2026"
NEW_PASSWORD = "Recovered!Campus-2026"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordRecoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="recover_user", password=OLD_PASSWORD, email="owner@example.com",
            role="student", approval_status="approved",
        )

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def request_reset(self, **extra):
        return self.client.post(reverse("accounts:password_reset"), {
            "username": self.user.username, "email": self.user.email, **extra,
        })

    def token_url(self, token=None, user=None):
        user = user or self.user
        return reverse("accounts:password_reset_confirm", kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": token or default_token_generator.make_token(user),
        })

    def assert_invalid_link(self, url):
        response = self.client.get(url, follow=True)
        self.assertContains(response, "链接已失效")
        self.assertFalse(response.context["validlink"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_login_has_forgot_password_and_anonymous_pages_are_accessible(self):
        self.assertContains(self.client.get(reverse("accounts:login")), reverse("accounts:password_reset"))
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"accounts:{name}")).status_code, 200)

    def test_email_link_completes_reset_without_automatic_login(self):
        response = self.request_reset(email="OWNER@example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        link = re.search(r"http://testserver(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.assertTrue(page.context["validlink"])
        intermediate = page.request["PATH_INFO"]
        self.assertIn("set-password", intermediate)
        response = self.client.post(intermediate, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(OLD_PASSWORD))
        self.assertEqual((self.user.role, self.user.approval_status), ("student", "approved"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(self.client.login(username=self.user.username, password=OLD_PASSWORD))
        response = self.client.post(reverse("accounts:login"), {"username": self.user.username, "password": NEW_PASSWORD})
        self.assertRedirects(response, reverse("core:home"))

    def test_unknown_mismatched_and_ineligible_accounts_get_same_response(self):
        cases = (
            ({"username": "unknown"}, {}),
            ({"email": "other@example.com"}, {}),
            ({}, {"email": ""}),
            ({}, {"is_active": False}),
            ({}, {"password": "!unusable"}),
            ({}, {"role": "invalid"}),
        )
        for request_data, user_data in cases:
            with self.subTest(request_data=request_data, user_data=user_data):
                cache.clear()
                # Reset the fixture before every ineligibility case.
                User.objects.filter(pk=self.user.pk).update(
                    **{"email": "owner@example.com", "is_active": True, "role": "student", "password": self.user.password, **user_data}
                )
                response = self.request_reset(**request_data)
                self.assertRedirects(response, reverse("accounts:password_reset_done"))
                self.assertEqual(len(mail.outbox), 0)
                self.assertNotIn(SESSION_KEY, self.client.session)
        done = self.client.get(reverse("accounts:password_reset_done"))
        self.assertNotContains(done, "owner@example.com")
        self.assertNotContains(done, self.user.username)
        self.assertNotContains(done, "/accounts/reset/")

    def test_another_account_cannot_use_the_target_mailbox_to_request_a_reset(self):
        other = User.objects.create_user(username="other_user", password=OLD_PASSWORD, email="other@example.com")
        self.request_reset(username=other.username)
        self.assertEqual(len(mail.outbox), 0)
        self.request_reset()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.username, mail.outbox[0].body)
        self.assertNotIn(other.username, mail.outbox[0].body)

    def test_repeated_request_is_throttled_without_different_response(self):
        first = self.request_reset()
        second = self.request_reset(email="OWNER@example.com")
        self.assertEqual(first.url, second.url)
        self.assertEqual(len(mail.outbox), 1)

    def test_request_can_be_repeated_after_cooldown(self):
        self.request_reset()
        after_cooldown = time.time() + 61
        with patch("django.core.cache.backends.locmem.time.time", return_value=after_cooldown):
            self.request_reset()
        self.assertEqual(len(mail.outbox), 2)

    def test_pending_existing_session_can_access_recovery_but_not_business(self):
        User.objects.filter(pk=self.user.pk).update(approval_status="pending")
        self.client.force_login(self.user)
        for name in ("password_reset", "password_reset_done", "password_reset_complete"):
            self.assertEqual(self.client.get(reverse(f"accounts:{name}")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 403)

    def test_required_and_invalid_email_fields_do_not_send(self):
        for data in ({"username": ""}, {"email": ""}, {"email": "invalid"}):
            response = self.request_reset(**data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_tampered_and_bad_uid_links_are_rejected(self):
        issued = datetime(2026, 10, 20, 10, 0, 0)
        with patch.object(default_token_generator, "_now", return_value=issued):
            token = default_token_generator.make_token(self.user)
        with patch.object(default_token_generator, "_now", return_value=issued + timedelta(seconds=3601)):
            self.assert_invalid_link(self.token_url(token))
        self.assert_invalid_link(self.token_url("tampered-token"))
        self.assert_invalid_link(reverse("accounts:password_reset_confirm", kwargs={"uidb64": "bad-uid", "token": "bad-token"}))

    def test_link_expires_while_form_is_open(self):
        issued = datetime(2026, 10, 20, 10, 0, 0)
        with patch.object(default_token_generator, "_now", return_value=issued):
            page = self.client.get(self.token_url(), follow=True)
        with patch.object(default_token_generator, "_now", return_value=issued + timedelta(seconds=3601)):
            response = self.client.post(page.request["PATH_INFO"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertContains(response, "链接已失效")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_disabled_after_issue_cannot_reset_even_after_opening_form(self):
        page = self.client.get(self.token_url(), follow=True)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.client.post(page.request["PATH_INFO"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertContains(response, "链接已失效")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_weak_and_mismatched_passwords_are_rejected_and_cleared(self):
        page = self.client.get(self.token_url(), follow=True)
        for first, second in (("123", "123"), (NEW_PASSWORD, "different")):
            response = self.client.post(page.request["PATH_INFO"], {"new_password1": first, "new_password2": second})
            self.assertTrue(response.context["form"].errors)
            self.assertNotContains(response, f'value="{first}"')
            self.assertNotContains(response, f'value="{second}"')
            self.assertNotIn(SESSION_KEY, self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_used_link_and_preexisting_login_session_are_invalidated(self):
        previous = Client()
        previous.force_login(self.user)
        self.user.refresh_from_db()
        url = self.token_url()
        page = self.client.get(url, follow=True)
        self.client.post(page.request["PATH_INFO"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertContains(self.client.get(url), "链接已失效")
        self.assertFalse(previous.get(reverse("core:home")).context["user"].is_authenticated)
        self.assertNotIn(SESSION_KEY, previous.session)

    def test_pending_and_rejected_status_remain_after_reset(self):
        for status in ("pending", "rejected"):
            with self.subTest(status=status):
                User.objects.filter(pk=self.user.pk).update(role="teacher", approval_status=status)
                self.user.refresh_from_db()
                page = self.client.get(self.token_url(), follow=True)
                self.assertTrue(page.context["validlink"])
                self.client.post(page.request["PATH_INFO"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
                response = self.client.post(reverse("accounts:login"), {"username": self.user.username, "password": NEW_PASSWORD})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(SESSION_KEY, self.client.session)
                self.user.refresh_from_db()
                self.assertEqual((self.user.role, self.user.approval_status), ("teacher", status))

    @override_settings(DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_prints_email_instead_of_delivering(self):
        output = io.StringIO()
        with redirect_stdout(output):
            response = self.request_reset()
        self.assertEqual(response.status_code, 302)
        self.assertIn("To: owner@example.com", output.getvalue())
        self.assertIn("http://testserver/accounts/reset/", output.getvalue())
        self.assertNotIn(OLD_PASSWORD, output.getvalue())
        self.assertContains(self.client.get(reverse("accounts:password_reset_done")), "不会实际投递到邮箱")

    def test_csrf_is_required_for_reset_and_email_binding(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("accounts:password_reset"), {"username": self.user.username, "email": self.user.email}).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("accounts:recovery_email"), {"email": "new@example.com", "current_password": OLD_PASSWORD}).status_code, 403)
        client.get(reverse("accounts:password_reset"))
        response = client.post(reverse("accounts:password_reset"), {
            "username": self.user.username, "email": self.user.email,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        client = Client(enforce_csrf_checks=True)
        page = client.get(self.token_url(), follow=True)
        self.assertEqual(client.post(page.request["PATH_INFO"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}).status_code, 403)


class RecoveryEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="email_user", password=OLD_PASSWORD, role="student", approval_status="approved")

    def test_anonymous_users_cannot_bind_email(self):
        response = self.client.get(reverse("accounts:recovery_email"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_binding_requires_current_password_and_valid_address(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("core:home")), reverse("accounts:recovery_email"))
        for email, password in (("new@example.com", "wrong"), ("invalid", OLD_PASSWORD)):
            response = self.client.post(reverse("accounts:recovery_email"), {"email": email, "current_password": password})
            self.assertTrue(response.context["form"].errors)
            self.user.refresh_from_db()
            self.assertEqual(self.user.email, "")
        response = self.client.post(reverse("accounts:recovery_email"), {
            "email": "new@example.com", "current_password": OLD_PASSWORD,
            "role": "admin", "approval_status": "rejected",
        }, follow=True)
        self.assertContains(response, "找回邮箱已保存")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertEqual((self.user.role, self.user.approval_status), ("student", "approved"))

    def test_changing_email_invalidates_old_reset_links(self):
        self.client.force_login(self.user)
        # Make the token after login, to isolate invalidation by email change.
        self.user.refresh_from_db()
        token = default_token_generator.make_token(self.user)
        self.client.post(reverse("accounts:recovery_email"), {"email": "new@example.com", "current_password": OLD_PASSWORD})
        self.user.refresh_from_db()
        self.assertFalse(default_token_generator.check_token(self.user, token))

    def test_registration_can_store_optional_recovery_email(self):
        data = {"username": "202610200001", "role": "student", "real_name": "测试用户", "phone": "13800138000", "password1": OLD_PASSWORD, "password2": OLD_PASSWORD}
        response = self.client.post(reverse("accounts:register"), {**data, "email": "invalid"})
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="202610200001").exists())
        response = self.client.post(reverse("accounts:register"), {**data, "email": "new@example.com"})
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertEqual(User.objects.get(username="202610200001").email, "new@example.com")
