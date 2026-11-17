from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User

from . import test_query_history as query_tests
from .models import Ticket, TicketHistory
from .note_services import add_ticket_note


class TicketNoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        query_tests.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def url(self, ticket):
        return reverse("tickets:add_note", args=[ticket.pk])

    def notes(self, ticket):
        return ticket.history.filter(event_type="note_added")

    def test_allowed_roles_append_without_changing_ticket_or_old_history(self):
        cases = [
            ("student_a", self.first),
            ("worker_a", self.second),
            ("manager", self.first),
            ("teacher", self.teacher_ticket),
        ]
        for name, ticket in cases:
            with self.subTest(user=name):
                self.client.force_login(self.users[name])
                before = Ticket.objects.values().get(pk=ticket.pk)
                old_history = list(
                    ticket.history.order_by("pk").values()
                )
                old_ids = [item["id"] for item in old_history]
                count = self.notes(ticket).count()

                response = self.client.post(
                    self.url(ticket),
                    {"text": f"{name}补充现场情况"},
                )

                self.assertRedirects(
                    response,
                    reverse("tickets:detail", args=[ticket.pk]),
                    fetch_redirect_response=False,
                )
                self.assertEqual(self.notes(ticket).count(), count + 1)
                latest = self.notes(ticket).latest("pk")
                self.assertEqual(latest.actor, self.users[name])
                self.assertEqual(latest.description, f"{name}补充现场情况")
                self.assertEqual(
                    Ticket.objects.values().get(pk=ticket.pk), before
                )
                self.assertEqual(
                    list(
                        ticket.history.filter(pk__in=old_ids)
                        .order_by("pk").values()
                    ),
                    old_history,
                )

    def test_cancellation_pending_still_allows_notes(self):
        self.client.force_login(self.users["student_a"])
        response = self.client.post(
            self.url(self.first),
            {"text": "取消审核期间补充情况"},
            follow=True,
        )
        self.assertContains(response, "取消审核期间补充情况")
        self.assertContains(response, "取消申请待审核")
        self.first.refresh_from_db()
        self.assertTrue(self.first.cancellation_pending)
        self.assertEqual(self.first.status, "pending_acceptance")

    def test_other_users_cannot_append_by_changing_id(self):
        for name, ticket in (
            ("student_b", self.first),
            ("teacher", self.first),
            ("worker_b", self.second),
        ):
            with self.subTest(user=name):
                self.client.force_login(self.users[name])
                before = TicketHistory.objects.count()
                response = self.client.post(
                    self.url(ticket), {"text": "越权说明"}
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(TicketHistory.objects.count(), before)

    def test_forged_actor_and_business_fields_are_rejected(self):
        self.client.force_login(self.users["student_a"])
        fields = {
            "actor_id": self.users["manager"].pk,
            "actor": self.users["manager"].pk,
            "status": "completed",
            "event_type": "status_changed",
            "title": "覆盖原标题",
            "current_assignee": self.users["worker_b"].pk,
            "metadata": "{}",
        }
        for key, value in fields.items():
            with self.subTest(field=key):
                before = Ticket.objects.values().get(pk=self.first.pk)
                count = TicketHistory.objects.count()
                response = self.client.post(
                    self.url(self.first),
                    {"text": "正常说明", key: value},
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(TicketHistory.objects.count(), count)
                self.assertEqual(
                    Ticket.objects.values().get(pk=self.first.pk), before
                )

    def test_empty_and_oversized_notes_are_rejected(self):
        self.client.force_login(self.users["student_a"])
        for text in ("", "   ", "x" * 2001):
            with self.subTest(length=len(text)):
                response = self.client.post(
                    self.url(self.first), {"text": text}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("text", response.context["note_form"].errors)
                self.assertFalse(self.notes(self.first).exists())

    def test_admin_keeps_workflow_forms_after_note_validation_error(self):
        self.client.force_login(self.users["manager"])
        response = self.client.post(self.url(self.first), {"text": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text", response.context["note_form"].errors)
        self.assertIn("assignment_form", response.context)
        self.assertIn("reject_form", response.context)
        self.assertIn("priority_form", response.context)

    def test_terminal_tickets_are_read_only(self):
        self.client.force_login(self.users["student_a"])
        for status in ("rejected", "completed", "cancelled"):
            with self.subTest(status=status):
                Ticket.objects.filter(pk=self.first.pk).update(
                    status=status,
                    current_assignee=(
                        self.users["worker_a"] if status == "completed"
                        else None
                    ),
                )
                response = self.client.post(
                    self.url(self.first), {"text": "终态说明"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "只能查看")
                self.assertFalse(self.notes(self.first).exists())
                self.first.refresh_from_db()
                self.assertEqual(self.first.status, status)

    def test_login_post_and_csrf_are_required(self):
        url = self.url(self.first)
        self.assertEqual(
            self.client.post(url, {"text": "未登录"}).status_code, 302
        )
        self.client.force_login(self.users["student_a"])
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.users["student_a"])
        self.assertEqual(
            csrf_client.post(url, {"text": "缺少CSRF"}).status_code, 403
        )
        self.assertFalse(self.notes(self.first).exists())

    def test_service_rechecks_current_assignee(self):
        old_worker = self.users["worker_a"]
        Ticket.objects.filter(pk=self.second.pk).update(
            current_assignee=self.users["worker_b"]
        )
        with self.assertRaises(PermissionDenied):
            add_ticket_note(
                ticket_id=self.second.pk,
                actor=old_worker,
                text="旧处理人不能再追加",
            )
        self.assertFalse(self.notes(self.second).exists())

    def test_service_rechecks_account_status(self):
        actor = self.users["student_a"]
        User.objects.filter(pk=actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            add_ticket_note(
                ticket_id=self.first.pk,
                actor=actor,
                text="停用账号不能追加",
            )
        self.assertFalse(self.notes(self.first).exists())

    def test_database_failure_rolls_back_insert_and_shows_feedback(self):
        original_create = TicketHistory.objects.create

        def insert_then_fail(*args, **kwargs):
            original_create(*args, **kwargs)
            raise DatabaseError("test failure after insertion")

        self.client.force_login(self.users["student_a"])
        before = Ticket.objects.values().get(pk=self.first.pk)
        with patch.object(
            TicketHistory.objects, "create", side_effect=insert_then_fail
        ):
            response = self.client.post(
                self.url(self.first), {"text": "失败的说明"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "本次未追加")
        self.assertFalse(self.notes(self.first).exists())
        self.assertEqual(
            Ticket.objects.values().get(pk=self.first.pk), before
        )

    def test_lock_retry_does_not_leave_duplicate_notes(self):
        original_create = TicketHistory.objects.create
        attempts = []

        def insert_with_first_lock(*args, **kwargs):
            record = original_create(*args, **kwargs)
            attempts.append(True)
            if len(attempts) == 1:
                raise OperationalError("database is locked")
            return record

        self.client.force_login(self.users["student_a"])
        with patch.object(
            TicketHistory.objects,
            "create",
            side_effect=insert_with_first_lock,
        ), patch("tickets.note_services.time.sleep"):
            response = self.client.post(
                self.url(self.first), {"text": "重试后只保存一次"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.notes(self.first).count(), 1)
