from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from . import test_query_history as fixtures
from .models import RepairConfirmation, RepairResult, Ticket, TicketHistory


class RepairViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.ticket = Ticket.objects.get(pk=self.second.pk)

    def url(self, action):
        return reverse(
            "tickets:repair_action", args=[self.ticket.pk, action]
        )

    def post(self, actor, action, **fields):
        self.client.force_login(self.users[actor])
        self.ticket.refresh_from_db()
        payload = {"version": self.ticket.repair_version, **fields}
        response = self.client.post(self.url(action), payload)
        self.ticket.refresh_from_db()
        return response

    def pending(self):
        self.assertEqual(self.post("worker_a", "start").status_code, 302)
        self.assertEqual(
            self.post(
                "worker_a", "result", content="第一轮维修结果"
            ).status_code,
            302,
        )
        return self.ticket.repair_results.get(round_number=1)

    def test_full_http_flow_preserves_both_rounds_and_buttons(self):
        self.client.force_login(self.users["worker_a"])
        detail = reverse("tickets:detail", args=[self.ticket.pk])
        page = self.client.get(detail)
        self.assertContains(page, ">开始维修</button>")

        self.assertEqual(self.post("worker_a", "start").status_code, 302)
        self.assertEqual(
            self.post("worker_a", "progress", content="检查现场线路").status_code,
            302,
        )
        self.assertEqual(
            self.post("worker_a", "result", content="第一次更换线路").status_code,
            302,
        )
        first = self.ticket.repair_results.get()

        self.client.force_login(self.users["student_a"])
        self.assertContains(self.client.get(detail), "提交确认决定")
        self.assertEqual(
            self.post(
                "student_a", "confirm",
                result_id=first.pk, decision="unresolved",
                reason="仍然闪烁",
            ).status_code,
            302,
        )
        self.assertEqual(
            self.post("worker_a", "result", content="第二次更换灯具").status_code,
            302,
        )
        second = self.ticket.repair_results.get(round_number=2)
        self.assertEqual(
            self.post(
                "student_a", "confirm",
                result_id=second.pk, decision="resolved", reason="",
            ).status_code,
            302,
        )
        self.assertEqual(self.ticket.status, "completed")

        page = self.client.get(detail)
        for text in (
            "检查现场线路", "第一次更换线路", "第二次更换灯具",
            "仍然闪烁", "第1轮维修结果", "第2轮维修结果", "已解决",
        ):
            self.assertContains(page, text)
        self.assertFalse(page.context["can_confirm_repair_result"])
        self.assertEqual(RepairConfirmation.objects.count(), 2)

    def test_admin_and_other_worker_cannot_act(self):
        self.assertEqual(self.post("manager", "start").status_code, 403)
        self.assertEqual(self.post("worker_b", "start").status_code, 404)
        self.assertEqual(self.post("student_a", "start").status_code, 403)

    def test_missing_version_preserves_result_input(self):
        self.post("worker_a", "start")
        response = self.client.post(
            self.url("result"), {"content": "已填写的结果"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "version", response.context["repair_error_form"].errors
        )
        self.assertEqual(
            response.context["repair_result_form"]["content"].value(),
            "已填写的结果",
        )
        self.assertFalse(RepairResult.objects.exists())

    def test_fake_actor_or_state_fields_rejected(self):
        for field, value in (
            ("actor_id", self.users["manager"].pk),
            ("status", "completed"),
            ("ticket_id", self.first.pk),
        ):
            with self.subTest(field=field):
                response = self.post(
                    "worker_a", "start", **{field: value}
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.ticket.status, "pending_work")

    def test_post_login_and_csrf_required(self):
        self.assertEqual(
            self.client.post(
                self.url("start"), {"version": 0}
            ).status_code,
            302,
        )
        self.client.force_login(self.users["worker_a"])
        self.assertEqual(
            self.client.get(self.url("start")).status_code, 405
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.users["worker_a"])
        self.assertEqual(
            csrf.post(self.url("start"), {"version": 0}).status_code,
            403,
        )

    def test_old_page_rejected_without_duplicate_history(self):
        self.assertEqual(self.post("worker_a", "start").status_code, 302)
        count = self.ticket.history.count()
        response = self.post("worker_a", "start", version=0)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "工单已更新", status_code=409)
        self.assertEqual(self.ticket.history.count(), count)

    def test_frozen_confirmation_hidden_and_direct_request_rejected(self):
        result = self.pending()
        Ticket.objects.filter(pk=self.ticket.pk).update(
            cancellation_pending=True
        )
        response = self.post(
            "student_a", "confirm",
            result_id=result.pk, decision="resolved", reason="",
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "取消申请待审核", status_code=409)
        self.assertFalse(response.context["can_confirm_repair_result"])
        self.assertTrue(response.context["can_add_note"])
        self.assertFalse(RepairConfirmation.objects.exists())

    def test_history_failure_shown_without_partial_result(self):
        self.post("worker_a", "start")
        with patch.object(
            TicketHistory.objects, "create",
            side_effect=DatabaseError("failure"),
        ):
            response = self.post(
                "worker_a", "result", content="不应保存的结果"
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "数据未发生变化")
        self.assertFalse(RepairResult.objects.exists())
        self.assertEqual(self.ticket.status, "in_progress")

    def test_waiting_a_week_does_not_automatically_complete(self):
        self.pending()
        self.client.force_login(self.users["student_a"])
        future = timezone.now() + timedelta(days=7)
        with patch("django.utils.timezone.now", return_value=future):
            response = self.client.get(
                reverse("tickets:detail", args=[self.ticket.pk])
            )
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "pending_confirmation")
        self.assertFalse(RepairConfirmation.objects.exists())
