from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import test_query_history as fixtures
from .models import RepairConfirmation, RepairResult, Ticket
from .permissions import can_transition_ticket
from .repair_services import confirm_repair_result, start_repair, submit_repair_result
from .services import accept_and_assign_ticket, create_ticket, set_ticket_priority, transition_ticket


class RepairSpecificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def pending(self):
        ticket = start_repair(
            ticket_id=self.second.pk, actor=self.users["worker_a"], version=0,
        )
        return submit_repair_result(
            ticket_id=ticket.pk, actor=self.users["worker_a"],
            version=ticket.repair_version, content="第一轮结果",
        )

    def test_task_list_shows_assignment_time_priority_and_status_filter(self):
        ticket = create_ticket(
            reporter=self.users["student_a"], building=self.first.building,
            fault_category=self.first.fault_category, title="SRS紧急任务",
            description="验收分派时间及优先级",
            location_kind="room", room_number="A101",
        )
        set_ticket_priority(
            ticket=ticket, actor=self.users["manager"], priority="urgent",
        )
        ticket = accept_and_assign_ticket(
            ticket=ticket, actor=self.users["manager"],
            worker=self.users["worker_a"],
        )
        assigned = ticket.history.get(event_type="assigned")
        self.client.force_login(self.users["worker_a"])
        page = self.client.get(
            reverse("tickets:tasks"), {"status": "pending_work"},
        )
        rows = list(page.context["tickets"])
        row = next(row for row in rows if row.pk == ticket.pk)
        self.assertEqual(row.latest_assigned_at, assigned.created_at)
        self.assertContains(page, "分派时间：")
        self.assertContains(page, 'class="ticket-priority-urgent">紧急任务')
        self.assertNotIn(self.other.pk, {row.pk for row in rows})
        filtered = self.client.get(
            reverse("tickets:tasks"), {"status": "in_progress"},
        )
        self.assertFalse(filtered.context["tickets"].exists())

    def test_result_in_wrong_state_returns_conflict_and_preserves_input(self):
        self.client.force_login(self.users["worker_a"])
        response = self.client.post(
            reverse("tickets:repair_action", args=[self.second.pk, "result"]),
            {"version": 0, "content": "尚未开始时填写的结果"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response, "工单当前状态不允许此操作", status_code=409,
        )
        self.assertEqual(
            response.context["repair_result_form"]["content"].value(),
            "尚未开始时填写的结果",
        )
        self.assertFalse(RepairResult.objects.exists())
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, "pending_work")

    def test_old_result_cannot_confirm_new_round_even_with_current_version(self):
        ticket = self.pending()
        first = ticket.repair_results.get()
        ticket = confirm_repair_result(
            ticket_id=ticket.pk, actor=self.users["student_a"],
            version=ticket.repair_version, result_id=first.pk,
            decision="unresolved", reason="还未解决",
        )
        ticket = submit_repair_result(
            ticket_id=ticket.pk, actor=self.users["worker_a"],
            version=ticket.repair_version, content="第二轮结果",
        )
        self.client.force_login(self.users["student_a"])
        response = self.client.post(
            reverse("tickets:repair_action", args=[ticket.pk, "confirm"]),
            {
                "version": ticket.repair_version, "result_id": first.pk,
                "decision": "resolved", "reason": "",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "维修结果已变化", status_code=409)
        self.assertEqual(RepairConfirmation.objects.count(), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "pending_confirmation")

    def test_pending_confirmation_cannot_be_cancelled_through_generic_entry(self):
        ticket = self.pending()
        count = ticket.history.count()
        for actor in (self.users["manager"], self.users["student_a"]):
            with self.subTest(actor=actor.username):
                self.assertFalse(
                    can_transition_ticket(actor, ticket, Ticket.Status.CANCELLED)
                )
                with self.assertRaises(ValidationError):
                    transition_ticket(
                        ticket=ticket, actor=actor,
                        to_status=Ticket.Status.CANCELLED,
                    )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "pending_confirmation")
        self.assertEqual(ticket.history.count(), count)
