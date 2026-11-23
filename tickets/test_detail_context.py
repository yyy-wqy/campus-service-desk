from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from . import test_query_history as fixtures
from .models import TicketHistory
from .repair_services import add_repair_progress, start_repair


class TicketDetailContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.ticket = start_repair(
            ticket_id=self.second.pk, actor=self.users["worker_a"], version=0,
        )
        self.ticket = add_repair_progress(
            ticket_id=self.ticket.pk, actor=self.users["worker_a"],
            version=self.ticket.repair_version, content="已保存的现场检查记录",
        )
        self.client.force_login(self.users["worker_a"])
        self.detail_url = reverse("tickets:detail", args=[self.ticket.pk])
        self.result_url = reverse(
            "tickets:repair_action", args=[self.ticket.pk, "result"],
        )

    def assert_results_loaded_once(self, queries):
        # Count actual reads, including template rendering, rather than helper calls.
        result_reads = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and 'FROM "tickets_repairresult"' in query["sql"]
        ]
        self.assertEqual(len(result_reads), 1, result_reads)

    def test_detail_get_keeps_repair_forms_and_history(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assert_results_loaded_once(queries)
        self.assertContains(response, "已保存的现场检查记录")
        self.assertContains(response, 'action="' + self.result_url + '"')
        self.assertFalse(response.context["repair_result_form"].is_bound)
        self.assertEqual(
            response.context["repair_result_form"]["version"].value(),
            self.ticket.repair_version,
        )

    def test_invalid_repair_form_keeps_input_and_reads_results_once(self):
        draft = "已填写但缺少操作版本的结果"
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.result_url, {"content": draft})
        self.assertEqual(response.status_code, 200)
        self.assert_results_loaded_once(queries)
        form = response.context["repair_result_form"]
        self.assertIn("version", form.errors)
        self.assertEqual(form["content"].value(), draft)
        self.assertIs(response.context["repair_error_form"], form)
        self.assertContains(response, draft)
        self.assertContains(response, "已保存的现场检查记录")
        self.assertFalse(self.ticket.repair_results.exists())

    def test_stale_repair_form_keeps_copyable_draft_and_reads_results_once(self):
        draft = "旧页面中尚未保存的维修结果"
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                self.result_url, {"version": 0, "content": draft},
            )
        self.assertEqual(response.status_code, 409)
        self.assert_results_loaded_once(queries)
        self.assertContains(response, draft, status_code=409)
        self.assertContains(response, 'id="repair-draft-content"', status_code=409)
        self.assertContains(response, "重新加载工单详情", status_code=409)
        self.assertNotContains(
            response, 'action="' + self.result_url + '"', status_code=409,
        )
        self.assertFalse(self.ticket.repair_results.exists())

    def test_note_failure_keeps_note_admin_forms_and_repair_history(self):
        self.client.force_login(self.users["manager"])
        draft = "未保存的追加说明"
        url = reverse("tickets:add_note", args=[self.ticket.pk])
        # A failed write must render the bound note alongside repair context.
        with patch.object(
            TicketHistory.objects, "create", side_effect=DatabaseError("failure"),
        ), CaptureQueriesContext(connection) as queries:
            response = self.client.post(url, {"text": draft})
        self.assertEqual(response.status_code, 200)
        self.assert_results_loaded_once(queries)
        self.assertTrue(response.context["note_form"].non_field_errors())
        self.assertEqual(response.context["note_form"]["text"].value(), draft)
        self.assertContains(response, draft)
        self.assertContains(response, "已保存的现场检查记录")
        for name in ("assignment_form", "reject_form", "priority_form"):
            self.assertIn(name, response.context)
        self.assertFalse(self.ticket.history.filter(event_type="note_added").exists())
