import re

from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .contact_validation import EMAIL_IN_USE, PHONE_IN_USE
from .models import User


PASSWORD = "Campus!Repair-2026"
NEW_PASSWORD = "Recovered!Campus-2026"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def payload(self, **extra):
        return {
            "username": "202611040001", "role": "student", "real_name": "组合测试", "department": "测试部门",
            "phone": "00000000001", "email": "student@example.com",
            "password1": PASSWORD, "password2": PASSWORD, **extra,
        }

    def test_valid_format_duplicate_contacts_are_rejected_for_each_role(self):
        User.objects.create_user(
            username="existing", phone="00000000001", email="owner@example.com",
        )
        for role in ("student", "teacher", "maintenance"):
            for data, field, message in (
                ({"phone": "00000000001"}, "phone", PHONE_IN_USE),
                ({"phone": "13800138000", "email": "OWNER@EXAMPLE.COM"}, "email", EMAIL_IN_USE),
            ):
                with self.subTest(role=role, field=field):
                    username = "202611040001" if role == "student" else f"{role}.staff"
                    response = self.client.post(reverse("accounts:register"), self.payload(
                        role=role, username=username, **data,
                    ))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["form"].errors[field], [message])
                    self.assertNotIn("username", response.context["form"].errors)
                    self.assertEqual(User.objects.count(), 1)
                    self.assertNotIn(SESSION_KEY, self.client.session)

    def test_formatted_registration_email_reset_login_and_logout(self):
        response = self.client.post(reverse("accounts:register"), self.payload())
        self.assertRedirects(response, reverse("accounts:login"))
        user = User.objects.get(username="202611040001")
        self.assertEqual((user.phone, user.email), ("00000000001", "student@example.com"))
        response = self.client.post(reverse("accounts:password_reset"), {
            "username": user.username, "email": "STUDENT@example.com",
        })
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student@example.com"])
        link = re.search(r"http://testserver(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.assertTrue(page.context["validlink"])
        response = self.client.post(page.request["PATH_INFO"], {
            "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD,
        })
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        user.refresh_from_db()
        self.assertFalse(user.check_password(PASSWORD))
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual((user.role, user.approval_status), ("student", "approved"))
        self.assertContains(self.client.get(link), "链接已失效")
        response = self.client.post(reverse("accounts:login"), {
            "username": user.username, "password": NEW_PASSWORD,
        })
        self.assertRedirects(response, reverse("core:home"))
        self.assertEqual(self.client.session[SESSION_KEY], str(user.pk))
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))
        self.assertNotIn(SESSION_KEY, self.client.session)
