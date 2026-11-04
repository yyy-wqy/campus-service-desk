from html.parser import HTMLParser

from django.contrib.auth import SESSION_KEY
from django.test import TestCase
from django.urls import reverse

from .forms import RegistrationForm
from .models import User


PASSWORD = "Campus!Repair-2026"


class FormMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.selected_role = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs[attrs.get("name")] = attrs
        if tag == "option" and "selected" in attrs:
            self.selected_role = attrs.get("value")


class RegistrationFormatTests(TestCase):
    def payload(self, **extra):
        return {
            "role": "student", "username": "202610150001", "real_name": "测试用户",
            "phone": "13800138000", "password1": PASSWORD, "password2": PASSWORD,
            **extra,
        }

    def assert_rejected(self, field, value, role="student"):
        response = self.client.post(reverse("accounts:register"), self.payload(
            **{"role": role, field: value}
        ))
        self.assertEqual(response.status_code, 200)
        message = (
            RegistrationForm.PHONE_ERROR if field == "phone"
            else RegistrationForm.STUDENT_NUMBER_ERROR
        )
        self.assertEqual(response.context["form"].errors[field], [message])
        self.assertContains(response, message)
        self.assertFalse(User.objects.exists())
        self.assertNotIn(SESSION_KEY, self.client.session)
        return response

    def test_all_roles_require_eleven_ascii_phone_digits(self):
        invalid = (
            "", "1380013800", "138001380000", "1380013800a", "1380013800+",
            "138 0138000", " 13800138000", "13800138000 ", "13800138000\n",
            "１３８００１３８０００", "١٣٨٠٠١٣٨٠٠٠", "1380013800１",
        )
        for role in ("student", "teacher", "maintenance"):
            for phone in invalid:
                with self.subTest(role=role, phone=repr(phone)):
                    self.assert_rejected("phone", phone, role)

    def test_student_number_requires_twelve_ascii_digits(self):
        invalid = (
            "", "20261015001", "2026101500001", "20261015000a", "20261015000+",
            "2026 0150001", " 202610150001", "202610150001 ", "202610150001\n",
            "２０２６１０１５０００１", "٢٠٢٦١٠١٥٠٠٠١", "20261015000１",
        )
        for username in invalid:
            with self.subTest(username=repr(username)):
                self.assert_rejected("username", username)

    def test_leading_zeros_are_stored_as_strings_for_every_role(self):
        for role, username in (
            ("student", "000000000001"),
            ("teacher", "teacher.01"),
            ("maintenance", "repair_01"),
        ):
            with self.subTest(role=role):
                response = self.client.post(reverse("accounts:register"), self.payload(
                    role=role, username=username, phone="00000000001",
                ))
                self.assertRedirects(response, reverse("accounts:login"))
                user = User.objects.get(username=username)
                self.assertEqual(user.username, username)
                self.assertEqual(user.phone, "00000000001")
                self.assertTrue(user.check_password(PASSWORD))
                self.assertEqual(user.role, role)
                self.assertEqual(user.approval_status, "approved" if role == "student" else "pending")
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertNotIn(SESSION_KEY, self.client.session)

    def test_nonstudent_accounts_keep_original_username_rules(self):
        for role in ("teacher", "maintenance"):
            for username in ("t", "repair.team_1", "1234567890123"):
                with self.subTest(role=role, username=username):
                    form = RegistrationForm(data=self.payload(role=role, username=username))
                    self.assertTrue(form.is_valid(), form.errors)
            form = RegistrationForm(data=self.payload(role=role, username="bad account!"))
            self.assertFalse(form.is_valid())
            self.assertIn("username", form.errors)

    def test_duplicate_student_number_is_still_rejected(self):
        User.objects.create_user(username="202610150001", password=PASSWORD)
        response = self.client.post(reverse("accounts:register"), self.payload())
        self.assertIn("username", response.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_student_submission_preserves_nonpassword_inputs(self):
        payload = self.payload(username="２０２６１０１５０００１", phone="13800138000 ")
        response = self.client.post(reverse("accounts:register"), payload)
        markup = FormMarkup(response.content.decode())
        for field in ("username", "real_name", "phone"):
            self.assertEqual(markup.inputs[field]["value"], payload[field])
        self.assertEqual(markup.selected_role, "student")
        for field in ("password1", "password2"):
            self.assertEqual(markup.inputs[field].get("value", ""), "")
        for field, message in (("username", RegistrationForm.STUDENT_NUMBER_ERROR), ("phone", RegistrationForm.PHONE_ERROR)):
            errors = response.context["form"][field].errors
            self.assertEqual(errors, [message])
            self.assertIn(str(errors), response.content.decode())
        self.assertFalse(User.objects.exists())

    def test_nonstudent_failure_preserves_selected_role_and_account(self):
        response = self.assert_rejected("phone", "invalid", role="maintenance")
        markup = FormMarkup(response.content.decode())
        self.assertEqual(markup.selected_role, "maintenance")
        self.assertEqual(markup.inputs["username"]["value"], "202610150001")
        self.assertNotIn("pattern", markup.inputs["username"])
        self.assertEqual(markup.inputs["username"]["maxlength"], "150")

    def test_page_provides_native_constraints_and_role_switch_script(self):
        response = self.client.get(reverse("accounts:register"))
        markup = FormMarkup(response.content.decode())
        self.assertEqual(markup.inputs["phone"]["type"], "tel")
        self.assertEqual(markup.inputs["phone"]["pattern"], "[0-9]{11}")
        self.assertEqual(markup.inputs["phone"]["minlength"], "11")
        self.assertEqual(markup.inputs["phone"]["maxlength"], "11")
        self.assertEqual(markup.inputs["username"]["type"], "text")
        self.assertEqual(markup.inputs["username"]["pattern"], "[0-9]{12}")
        self.assertEqual(markup.inputs["username"]["minlength"], "12")
        self.assertEqual(markup.inputs["username"]["maxlength"], "12")
        self.assertEqual(markup.inputs["username"]["placeholder"], "请输入 12 位学号")
        self.assertContains(response, '/static/js/register.js')
        for field in ("username", "phone"):
            self.assertContains(response, f'data-validation-error="{field}"')
