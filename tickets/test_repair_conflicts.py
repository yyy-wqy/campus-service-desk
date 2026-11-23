from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from . import test_query_history as fixtures
from .models import RepairConfirmation, Ticket
from .repair_services import (
    add_repair_progress, confirm_repair_result, start_repair,
    submit_repair_result,
)


class DraftParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.drafts = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "textarea" and attrs.get("id", "").startswith("repair-draft-"):
            self.current = attrs["id"]
            self.drafts[self.current] = {"attrs": attrs, "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.drafts[self.current]["text"] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.current = None


class RepairConflictPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.ticket = start_repair(
            ticket_id=self.second.pk, actor=self.users["worker_a"], version=0,
        )
        self.client.force_login(self.users["worker_a"])
        self.detail = reverse("tickets:detail", args=[self.ticket.pk])

    def snapshot(self):
        self.ticket.refresh_from_db()
        return (
            self.ticket.status, self.ticket.repair_version, self.ticket.updated_at,
            self.ticket.repair_results.count(), self.ticket.repair_progress.count(),
            RepairConfirmation.objects.filter(result__ticket=self.ticket).count(),
            self.ticket.history.count(),
        )

    def assert_recovery(self, response, field=None, value=None):
        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response, f'href="{self.detail}"', status_code=409,
        )
        self.assertContains(response, "重新加载工单详情", status_code=409)
        self.assertNotContains(response, "__all__", status_code=409)
        for action in ("start", "progress", "result", "confirm"):
            self.assertNotContains(
                response,
                'action="' + reverse("tickets:repair_action", args=[self.ticket.pk, action]) + '"',
                status_code=409,
            )
        if field is not None:
            parsed = DraftParser(response.content.decode())
            draft = parsed.drafts["repair-draft-" + field]
            self.assertEqual(draft["text"], value)
            self.assertIn("readonly", draft["attrs"])
            self.assertNotIn("disabled", draft["attrs"])
            self.assertNotIn("name", draft["attrs"])

    def post(self, action, **payload):
        return self.client.post(
            reverse("tickets:repair_action", args=[self.ticket.pk, action]), payload,
        )

    def test_result_conflict_after_state_change_retains_escaped_copyable_draft(self):
        version = self.ticket.repair_version
        for _ in range(2):
            page = self.client.get(self.detail)
            self.assertEqual(page.context["repair_result_form"]["version"].value(), version)
        self.assertEqual(self.post("result", version=version, content="已保存的结果").status_code, 302)
        before = self.snapshot()
        draft = '未保存的第二份结果\n</textarea><script>alert("draft")</script>'
        response = self.post("result", version=version, content=draft)
        self.assert_recovery(response, "content", draft)
        self.assertNotContains(response, '<script>alert("draft")</script>', status_code=409)
        self.assertEqual(self.snapshot(), before)

    def test_version_only_conflict_requires_get_reload_before_valid_submit(self):
        version = self.ticket.repair_version
        self.ticket = add_repair_progress(
            ticket_id=self.ticket.pk, actor=self.users["worker_a"],
            version=version, content="另一页面已经追加进度",
        )
        before = self.snapshot()
        response = self.post("result", version=version, content="仍可复制的结果草稿")
        self.assert_recovery(response, "content", "仍可复制的结果草稿")
        self.assertEqual(str(response.context["repair_result_form"]["version"].value()), str(version))
        self.assertEqual(self.snapshot(), before)
        page = self.client.get(self.detail)
        self.assertContains(page, "另一页面已经追加进度")
        self.assertEqual(page.context["repair_result_form"]["version"].value(), self.ticket.repair_version)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.post("result", version=self.ticket.repair_version, content="核对最新信息后重新提交").status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_CONFIRMATION)
        self.assertEqual(self.ticket.repair_results.count(), 1)

    def test_progress_conflict_after_result_preserves_progress_draft(self):
        version = self.ticket.repair_version
        submit_repair_result(ticket_id=self.ticket.pk, actor=self.users["worker_a"], version=version, content="已提交结果")
        before = self.snapshot()
        response = self.post("progress", version=version, content="尚未保存的现场进度")
        self.assert_recovery(response, "content", "尚未保存的现场进度")
        self.assertEqual(self.snapshot(), before)

    def test_confirmation_conflict_preserves_reason_and_decision(self):
        self.ticket = submit_repair_result(ticket_id=self.ticket.pk, actor=self.users["worker_a"], version=self.ticket.repair_version, content="已提交结果")
        result = self.ticket.repair_results.get()
        version = self.ticket.repair_version
        confirm_repair_result(ticket_id=self.ticket.pk, actor=self.users["student_a"], version=version, result_id=result.pk, decision="resolved")
        self.client.force_login(self.users["student_a"])
        before = self.snapshot()
        response = self.post("confirm", version=version, result_id=result.pk, decision="unresolved", reason="未保存的退回说明")
        self.assert_recovery(response, "reason", "未保存的退回说明")
        self.assert_recovery(response, "decision", "未解决，退回维修")
        self.assertEqual(self.snapshot(), before)

    def test_start_conflict_without_text_still_offers_recovery_link(self):
        before = self.snapshot()
        response = self.post("start", version=0)
        self.assert_recovery(response)
        self.assertEqual(self.snapshot(), before)
