from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .contact_validation import EMAIL_IN_USE, PHONE_IN_USE
from .forms import RegistrationForm
from .models import User
from .recovery_forms import RecoveryEmailForm


PASSWORD = "Campus!Repair-2026"


class UniqueContactTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="contact_owner", password=PASSWORD,
            phone="13800138000", email="owner@example.com", approval_status="approved",
        )
        cls.other = User.objects.create_user(
            username="contact_other", password=PASSWORD,
            phone="13800138001", email="other@example.com", approval_status="approved",
        )

    def payload(self, **extra):
        return {
            "username": "202610209999", "role": "student", "real_name": "新用户",
            "phone": "13800138002", "email": "new@example.com",
            "password1": PASSWORD, "password2": PASSWORD, **extra,
        }

    def assert_registration_conflict(self, payload, field, message):
        response = self.client.post(reverse("accounts:register"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors[field], [message])
        self.assertContains(response, message)
        self.assertEqual(User.objects.count(), 2)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(response, f'value="{PASSWORD}"')
        return response

    def test_phone_is_unique_across_all_roles(self):
        for role in ("student", "teacher", "maintenance"):
            with self.subTest(role=role):
                self.assert_registration_conflict(
                    self.payload(role=role, phone=self.owner.phone), "phone", PHONE_IN_USE
                )

    def test_email_is_unique_regardless_of_case_or_outer_spaces(self):
        for email in ("owner@example.com", "OWNER@EXAMPLE.COM", " Owner@Example.com "):
            with self.subTest(email=email):
                self.assert_registration_conflict(self.payload(email=email), "email", EMAIL_IN_USE)

    def test_both_errors_are_near_fields_and_preserve_nonpassword_values(self):
        response = self.assert_registration_conflict(
            self.payload(phone=self.owner.phone, email="OWNER@example.com"), "phone", PHONE_IN_USE
        )
        self.assertEqual(response.context["form"].errors["email"], [EMAIL_IN_USE])
        for field in ("phone", "email"):
            self.assertIn(str(response.context["form"][field].errors), response.content.decode())
        self.assertContains(response, 'value="OWNER@example.com"')
        self.assertContains(response, f'value="{self.owner.phone}"')
        self.assertContains(response, 'value="202610209999"')

    def test_distinct_contacts_register_and_store_canonical_email(self):
        response = self.client.post(reverse("accounts:register"), self.payload(email=" NEW@EXAMPLE.COM "))
        self.assertRedirects(response, reverse("accounts:login"))
        user = User.objects.get(username="202610209999")
        self.assertEqual((user.phone, user.email), ("13800138002", "new@example.com"))
        self.assertEqual((user.role, user.approval_status), ("student", "approved"))
        self.assertTrue(user.check_password(PASSWORD))

    def test_omitted_email_can_be_shared_by_multiple_accounts(self):
        for index in (2, 3):
            response = self.client.post(reverse("accounts:register"), self.payload(
                username=f"20261020999{index}", phone=f"1380013800{index}", email="",
            ))
            self.assertRedirects(response, reverse("accounts:login"))
        self.assertEqual(User.objects.filter(email="").count(), 2)

    def test_save_normalizes_contacts_without_removing_leading_zeros(self):
        user = User.objects.create_user(
            username="normalized_user", phone=" 00000000001 ", email=" Mixed@Example.com ",
        )
        user.refresh_from_db()
        self.assertEqual(user.phone, "00000000001")
        self.assertEqual(user.email, "mixed@example.com")

    def test_database_rejects_phone_collision_even_if_model_save_is_bypassed(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.other.pk).update(phone=" 13800138000 ")
        self.other.refresh_from_db()
        self.assertEqual(self.other.phone, "13800138001")

    def test_database_rejects_case_insensitive_email_collision(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.other.pk).update(email=" OWNER@EXAMPLE.COM ")
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, "other@example.com")

    def test_database_allows_multiple_legacy_users_without_contacts(self):
        for index in range(2):
            User.objects.create_user(username=f"legacy_{index}")
        self.assertEqual(User.objects.filter(phone="", email="").count(), 2)

    def test_registration_race_returns_field_error_instead_of_server_error(self):
        # Simulate passing advisory validation just before another request claims
        # the contact. Keep the real unique database index active.
        with patch.object(RegistrationForm, "clean_phone", lambda form: form.cleaned_data["phone"]), patch.object(User, "validate_constraints"):
            self.assert_registration_conflict(
                self.payload(phone=self.owner.phone), "phone", PHONE_IN_USE
            )

    def test_unrelated_database_errors_are_not_misreported_as_contact_conflicts(self):
        with patch.object(RegistrationForm, "save", side_effect=IntegrityError("unrelated")):
            with self.assertRaises(IntegrityError):
                self.client.post(reverse("accounts:register"), self.payload())
        self.assertEqual(User.objects.count(), 2)

    def test_binding_cannot_claim_another_users_email_but_allows_own_address(self):
        self.client.force_login(self.other)
        response = self.client.post(reverse("accounts:recovery_email"), {
            "email": "OWNER@example.com", "current_password": PASSWORD,
        })
        self.assertEqual(response.context["form"].errors["email"], [EMAIL_IN_USE])
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, "other@example.com")
        response = self.client.post(reverse("accounts:recovery_email"), {
            "email": " OTHER@EXAMPLE.COM ", "current_password": PASSWORD,
        })
        self.assertRedirects(response, reverse("accounts:recovery_email"))
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, "other@example.com")

    def test_email_binding_race_is_also_reported_as_field_error(self):
        self.client.force_login(self.other)
        with patch.object(RecoveryEmailForm, "clean_email", lambda form: form.cleaned_data["email"]):
            response = self.client.post(reverse("accounts:recovery_email"), {
                "email": "owner@example.com", "current_password": PASSWORD,
            })
        self.assertEqual(response.context["form"].errors["email"], [EMAIL_IN_USE])
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, "other@example.com")


class ContactMigrationTests(TransactionTestCase):
    before = [("accounts", "0001_initial")]
    after = [("accounts", "0002_user_accounts_unique_nonempty_phone_and_more")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.OldUser = executor.loader.project_state(self.before).apps.get_model("accounts", "User")

    def tearDown(self):
        self.OldUser.objects.all().delete()
        MigrationExecutor(connection).migrate(self.after)
        super().tearDown()

    def test_migration_normalizes_existing_values_and_keeps_empty_contacts(self):
        user = self.OldUser.objects.create(username="legacy", phone=" 00000000001 ", email=" Mixed@Example.com ")
        for index in range(2):
            self.OldUser.objects.create(username=f"empty_{index}", phone="", email="")
        MigrationExecutor(connection).migrate(self.after)
        user.refresh_from_db()
        self.assertEqual((user.phone, user.email), ("00000000001", "mixed@example.com"))
        self.assertEqual(self.OldUser.objects.count(), 3)

    def test_migration_stops_on_duplicates_without_deleting_or_editing_users(self):
        first = self.OldUser.objects.create(username="first", phone=" 13800138000 ", email=" OWNER@example.com ")
        second = self.OldUser.objects.create(username="second", phone="13800138000", email="owner@example.com")
        with self.assertRaisesRegex(RuntimeError, "Duplicate user contacts found"):
            MigrationExecutor(connection).migrate(self.after)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.phone, " 13800138000 ")
        self.assertEqual(first.email, " OWNER@example.com ")
        self.assertEqual(second.email, "owner@example.com")
        self.assertEqual(self.OldUser.objects.count(), 2)
