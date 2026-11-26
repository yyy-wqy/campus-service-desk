from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, OperationalError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import MaintenanceApplication, User
from locations.models import Building, FaultCategory

from .cancellations import review_cancellation, submit_cancellation
from .models import RepairConfirmation, Ticket, TicketCancellation, TicketHistory
from .note_services import add_ticket_note
from .repair_services import (
    RepairConflict, add_repair_progress, confirm_repair_result,
    start_repair, submit_repair_result,
)
from .services import TicketOperationFailed, create_ticket, set_ticket_priority, transition_ticket
from .tests import TicketFixtureMixin


FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class CancellationWorkflowTests(TicketFixtureMixin, TestCase):
    def test_detail_title_contains_only_ticket_number_and_platform_name(self):
        class TitleParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_title = False
                self.text = []

            def handle_starttag(self, tag, attrs):
                if tag == "title":
                    self.in_title = True

            def handle_endtag(self, tag):
                if tag == "title":
                    self.in_title = False

            def handle_data(self, data):
                if self.in_title:
                    self.text.append(data)

        ticket = self.assigned_ticket()
        application = None
        for stage in ("initial", "pending", "rejected", "approved"):
            if stage == "pending":
                application = submit_cancellation(
                    ticket=ticket, actor=self.student, reason="申请原因 <不能进入标题>",
                )
            elif stage == "rejected":
                review_cancellation(application=application, actor=self.admin,
                                    decision="rejected", reason="审核说明不能进入标题")
            elif stage == "approved":
                application = submit_cancellation(ticket=ticket, actor=self.student)
                review_cancellation(application=application, actor=self.admin, decision="approved")
            for actor in (self.student, self.admin):
                with self.subTest(stage=stage, role=actor.role):
                    self.client.force_login(actor)
                    response = self.client.get(reverse("tickets:detail", args=[ticket.pk]))
                    self.assertEqual(response.status_code, 200)
                    parser = TitleParser()
                    parser.feed(response.content.decode())
                    self.assertEqual("".join(parser.text), f"{ticket.number}｜校园智办")
                    if stage != "initial":
                        self.assertContains(response, 'class="surface-card cancellation-history"', count=1)
                        self.assertContains(response, "申请原因 &lt;不能进入标题&gt;")
                    if stage == "pending" and actor == self.admin:
                        self.assertContains(response, "审核这条申请", count=1)

    def assigned_ticket(self, status=Ticket.Status.PENDING_WORK):
        ticket = self.create_valid_ticket()
        ticket.status = status
        ticket.current_assignee = self.maintenance
        ticket.save(update_fields=("status", "current_assignee"))
        return ticket

    def test_reporter_cancels_pending_acceptance_with_a_reason(self):
        ticket = self.create_valid_ticket()
        url = reverse("tickets:cancellation_request", args=[ticket.pk])
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("tickets:detail", args=[ticket.pk])), url)
        self.assertContains(self.client.get(url), "确认取消")
        self.assertContains(self.client.post(url, {"reason": "  "}), "这个字段是必填项")
        self.assertFalse(ticket.cancellations.exists())

        response = self.client.post(url, {"reason": "报修问题已自行解决", "status": "completed"}, follow=True)
        self.assertContains(response, "已取消")
        self.assertContains(response, "报修问题已自行解决")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CANCELLED)
        self.assertFalse(ticket.cancellation_pending)
        record = ticket.cancellations.get()
        self.assertEqual(record.decision, TicketCancellation.Decision.DIRECT)
        self.assertEqual(record.applicant, self.student)
        self.assertIsNone(record.reviewer)
        self.assertEqual(ticket.history.last().to_status, Ticket.Status.CANCELLED)

    def test_review_requires_admin_preserves_history_and_restores_status_on_rejection(self):
        ticket = self.assigned_ticket(Ticket.Status.IN_PROGRESS)
        request_url = reverse("tickets:cancellation_request", args=[ticket.pk])
        list_url = reverse("tickets:cancellations")
        self.client.force_login(self.student)
        self.assertContains(self.client.get(request_url), "取消原因（选填）")
        response = self.client.post(request_url, {"reason": ""}, follow=True)
        self.assertContains(response, "取消申请待审核")
        self.assertNotContains(response, request_url)
        self.assertContains(self.client.get(reverse("tickets:list")), "取消申请待审核")
        application = ticket.cancellations.get()
        self.assertTrue(application.created_at)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.IN_PROGRESS)
        self.assertTrue(ticket.cancellation_pending)
        self.client.post(request_url, {"reason": "重复申请"})
        self.assertEqual(ticket.cancellations.count(), 1)
        review_url = reverse("tickets:cancellation_review", args=[application.pk])
        self.assertEqual(self.client.get(list_url).status_code, 403)
        self.assertEqual(self.client.post(review_url, {"decision": "approved"}).status_code, 403)

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(list_url), ticket.number)
        self.assertContains(self.client.get(reverse("tickets:detail", args=[ticket.pk])), review_url)
        self.assertContains(self.client.post(review_url, {"decision": "rejected", "reason": "  "}), "驳回时必须填写原因")
        self.client.post(review_url, {"decision": "rejected", "reason": "仍需维修"})
        self.client.post(review_url, {"decision": "approved"})
        application.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.IN_PROGRESS)
        self.assertFalse(ticket.cancellation_pending)
        self.assertEqual(application.decision, TicketCancellation.Decision.REJECTED)
        self.assertEqual(application.reviewer, self.admin)
        self.assertTrue(application.reviewed_at)
        self.assertEqual(application.review_reason, "仍需维修")

        second = submit_cancellation(ticket=ticket, actor=self.student, reason="另行安排")
        self.client.post(reverse("tickets:cancellation_review", args=[second.pk]), {"decision": "approved"})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CANCELLED)
        self.assertFalse(ticket.cancellation_pending)
        self.assertEqual(ticket.current_assignee, self.maintenance)
        events = ticket.history.filter(event_type=TicketHistory.EventType.CANCELLATION_CHANGED)
        self.assertEqual(list(events.values_list("metadata__decision", flat=True)), ["pending", "rejected", "pending", "approved"])

    def test_non_reporter_and_non_cancellable_states_cannot_submit(self):
        ticket = self.assigned_ticket()
        url = reverse("tickets:cancellation_request", args=[ticket.pk])
        for actor, expected in ((self.teacher, 404), (self.maintenance, 403), (self.admin, 403)):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(url, {"reason": "代申请"}).status_code, expected)
        for status in (Ticket.Status.PENDING_CONFIRMATION, Ticket.Status.COMPLETED, Ticket.Status.CANCELLED, Ticket.Status.REJECTED):
            with self.subTest(status=status):
                Ticket.objects.filter(pk=ticket.pk).update(
                    status=status,
                    current_assignee=None if status == Ticket.Status.REJECTED else self.maintenance,
                )
                with self.assertRaisesMessage(ValidationError, "当前工单状态不可申请取消"):
                    submit_cancellation(ticket=ticket, actor=self.student, reason="取消")
        self.assertFalse(ticket.cancellations.exists())

    def test_freeze_and_rejection_cover_real_start_progress_and_result_interfaces(self):
        for action, initial, service in (
            ("start", Ticket.Status.PENDING_WORK, start_repair),
            ("progress", Ticket.Status.IN_PROGRESS, add_repair_progress),
            ("result", Ticket.Status.IN_PROGRESS, submit_repair_result),
        ):
            with self.subTest(action=action):
                ticket = self.assigned_ticket(initial)
                application = submit_cancellation(ticket=ticket, actor=self.student)
                payload = {"version": ticket.repair_version}
                if action != "start":
                    payload["content"] = "仍可复制的旧页面内容"
                with self.assertRaisesMessage(RepairConflict, "取消申请待审核"):
                    service(ticket_id=ticket.pk, actor=self.maintenance, **payload)
                self.client.force_login(self.maintenance)
                detail = reverse("tickets:detail", args=[ticket.pk])
                page = self.client.get(detail)
                self.assertContains(page, "取消申请待审核")
                self.assertFalse(page.context["can_start_repair"])
                self.assertFalse(page.context["can_add_repair_progress"])
                self.assertFalse(page.context["can_submit_repair_result"])
                response = self.client.post(
                    reverse("tickets:repair_action", args=[ticket.pk, action]), payload,
                )
                self.assertContains(response, "取消申请待审核", status_code=409)
                self.assertFalse(ticket.repair_progress.exists())
                self.assertFalse(ticket.repair_results.exists())

                review_cancellation(application=application, actor=self.admin,
                                    decision="rejected", reason="继续处理")
                with self.assertRaises(RepairConflict):
                    service(ticket_id=ticket.pk, actor=self.maintenance, **payload)
                ticket.refresh_from_db()
                payload["version"] = ticket.repair_version
                self.assertEqual(self.client.post(
                    reverse("tickets:repair_action", args=[ticket.pk, action]), payload,
                ).status_code, 302)

    def test_history_failure_rolls_back_submission_and_approval(self):
        ticket = self.assigned_ticket()
        with patch.object(TicketHistory.objects, "create", side_effect=DatabaseError("test")):
            with self.assertRaises(TicketOperationFailed):
                submit_cancellation(ticket=ticket, actor=self.student)
        ticket.refresh_from_db()
        self.assertFalse(ticket.cancellation_pending)
        self.assertFalse(ticket.cancellations.exists())
        application = submit_cancellation(ticket=ticket, actor=self.student)
        with patch.object(TicketHistory.objects, "create", side_effect=DatabaseError("test")):
            with self.assertRaises(TicketOperationFailed):
                review_cancellation(application=application, actor=self.admin, decision="approved")
        ticket.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.PENDING_WORK)
        self.assertTrue(ticket.cancellation_pending)
        self.assertEqual(application.decision, TicketCancellation.Decision.PENDING)
        self.assertIsNone(application.reviewer)

    def test_confirmation_is_denied_if_an_inconsistent_pending_marker_exists(self):
        ticket = self.assigned_ticket(Ticket.Status.IN_PROGRESS)
        ticket = submit_repair_result(
            ticket_id=ticket.pk, actor=self.maintenance, version=0, content="已修复",
        )
        result = ticket.repair_results.get()
        Ticket.objects.filter(pk=ticket.pk).update(cancellation_pending=True)
        self.client.force_login(self.student)
        detail = self.client.get(reverse("tickets:detail", args=[ticket.pk]))
        self.assertFalse(detail.context["can_confirm_repair_result"])
        for decision in ("resolved", "unresolved"):
            response = self.client.post(
                reverse("tickets:repair_action", args=[ticket.pk, "confirm"]),
                {"version": ticket.repair_version, "result_id": result.pk,
                 "decision": decision, "reason": "仍有故障"},
            )
            self.assertContains(response, "取消申请待审核", status_code=409)
        self.assertFalse(RepairConfirmation.objects.filter(result=result).exists())
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.PENDING_CONFIRMATION)

    def test_notes_and_current_priority_rule_remain_available_during_review(self):
        ticket = self.assigned_ticket()
        submit_cancellation(ticket=ticket, actor=self.student)
        for actor in (self.student, self.maintenance, self.admin):
            self.client.force_login(actor)
            response = self.client.post(reverse("tickets:add_note", args=[ticket.pk]),
                                        {"text": f"补充说明 {actor.username}"})
            self.assertEqual(response.status_code, 302)
        set_ticket_priority(ticket=ticket, actor=self.admin, priority="urgent")
        ticket.refresh_from_db()
        self.assertTrue(ticket.cancellation_pending)
        self.assertEqual(ticket.status, Ticket.Status.PENDING_WORK)
        self.assertEqual(ticket.priority, Ticket.Priority.URGENT)
        self.assertEqual(ticket.history.filter(event_type="note_added").count(), 3)
        Ticket.objects.filter(pk=ticket.pk).update(status=Ticket.Status.IN_PROGRESS)
        with self.assertRaises(ValidationError):
            set_ticket_priority(ticket=ticket, actor=self.admin, priority="normal")

    def test_approval_preserves_old_rounds_and_makes_notes_and_repair_read_only(self):
        ticket = self.assigned_ticket(Ticket.Status.IN_PROGRESS)
        ticket = submit_repair_result(
            ticket_id=ticket.pk, actor=self.maintenance, version=0, content="旧轮维修结果",
        )
        result = ticket.repair_results.get()
        ticket = confirm_repair_result(
            ticket_id=ticket.pk, actor=self.student, version=ticket.repair_version,
            result_id=result.pk, decision="unresolved", reason="仍未解决",
        )
        ticket = add_repair_progress(
            ticket_id=ticket.pk, actor=self.maintenance, version=ticket.repair_version,
            content="保留本轮维修进度",
        )
        add_ticket_note(ticket_id=ticket.pk, actor=self.student, text="取消前的说明")
        application = submit_cancellation(ticket=ticket, actor=self.student)
        review_cancellation(application=application, actor=self.admin, decision="approved")
        self.client.force_login(self.student)
        response = self.client.get(reverse("tickets:detail", args=[ticket.pk]))
        for text in ("旧轮维修结果", "仍未解决", "保留本轮维修进度", "取消前的说明", "已批准"):
            self.assertContains(response, text)
        self.assertFalse(response.context["can_add_note"])
        self.assertFalse(response.context["can_request_cancellation"])
        with self.assertRaises(ValidationError):
            add_ticket_note(ticket_id=ticket.pk, actor=self.maintenance, text="终态说明")
        ticket.refresh_from_db()
        with self.assertRaises(RepairConflict):
            add_repair_progress(ticket_id=ticket.pk, actor=self.maintenance,
                                version=ticket.repair_version, content="终态进度")

    def test_permissions_are_rechecked_for_cached_reporter_and_admin(self):
        ticket = self.assigned_ticket()
        User.objects.filter(pk=self.student.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            submit_cancellation(ticket=ticket, actor=self.student)
        User.objects.filter(pk=self.student.pk).update(is_active=True)
        application = submit_cancellation(ticket=ticket, actor=self.student)
        User.objects.filter(pk=self.admin.pk).update(role="teacher")
        with self.assertRaises(PermissionDenied):
            review_cancellation(application=application, actor=self.admin, decision="approved")
        application.refresh_from_db()
        self.assertEqual(application.decision, "pending")

    def test_old_rejected_application_cannot_review_a_new_request(self):
        ticket = self.assigned_ticket()
        old = submit_cancellation(ticket=ticket, actor=self.student)
        review_cancellation(application=old, actor=self.admin, decision="rejected", reason="继续维修")
        new = submit_cancellation(ticket=ticket, actor=self.student)
        with self.assertRaises(ValidationError):
            review_cancellation(application=old, actor=self.admin, decision="approved")
        new.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(new.decision, "pending")
        self.assertTrue(ticket.cancellation_pending)

    def test_teacher_can_cancel_own_ticket_and_generic_transition_cannot_bypass_review(self):
        ticket = self.create_valid_ticket(reporter=self.teacher)
        with self.assertRaises(ValidationError):
            transition_ticket(ticket=ticket, actor=self.teacher, to_status=Ticket.Status.CANCELLED)
        record = submit_cancellation(ticket=ticket, actor=self.teacher, reason="教师自行取消")
        self.assertEqual(record.applicant_id, self.teacher.pk)
        assigned = self.assigned_ticket()
        with self.assertRaises(ValidationError):
            transition_ticket(ticket=assigned, actor=self.admin, to_status=Ticket.Status.CANCELLED)
        self.assertFalse(assigned.cancellations.exists())

    def test_invalid_inputs_and_get_requests_do_not_write(self):
        ticket = self.assigned_ticket()
        self.client.force_login(self.student)
        url = reverse("tickets:cancellation_request", args=[ticket.pk])
        for method in ("get", "put", "delete"):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 200 if method == "get" else 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        self.assertEqual(csrf_client.post(url, {"reason": "取消"}).status_code, 403)
        for reason in (None, "x" * 1001):
            with self.assertRaises(ValidationError):
                submit_cancellation(ticket=ticket, actor=self.student, reason=reason)
        self.assertFalse(ticket.cancellations.exists())
        application = submit_cancellation(ticket=ticket, actor=self.student)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tickets:cancellation_review", args=[application.pk])).status_code, 200)
        for decision, reason in (("direct", ""), ("rejected", "  "), ("approved", None)):
            with self.assertRaises(ValidationError):
                review_cancellation(application=application, actor=self.admin, decision=decision, reason=reason)
        application.refresh_from_db()
        self.assertEqual(application.decision, "pending")

    def test_read_lock_exhaustion_returns_retry_feedback(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite read-lock feedback")
        self.client.force_login(self.admin)
        with patch("tickets.views.get_object_or_404", side_effect=OperationalError("database is locked")), patch("tickets.cancellation_http.time.sleep"):
            response = self.client.get(reverse("tickets:cancellation_review", args=[999]))
        self.assertContains(response, "系统繁忙", status_code=503)
        self.assertEqual(response["Retry-After"], "1")


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class ConcurrentCancellationTests(TransactionTestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username="reporter", role="student", approval_status="approved", phone="13800138000")
        self.admin = User.objects.create_user(username="reviewer", role="admin", approval_status="approved")
        self.worker = User.objects.create_user(username="worker", role="maintenance", approval_status="approved")
        MaintenanceApplication.objects.create(worker=self.worker, status="approved")
        building = Building.objects.create(name="教学楼", building_type="teach")
        category = FaultCategory.objects.create(name="其他故障")
        self.ticket = create_ticket(
            reporter=self.reporter, building=building, fault_category=category,
            title="并发测试", description="灯坏了", location_kind="room", room_number="101",
        )
        self.ticket.status = Ticket.Status.IN_PROGRESS
        self.ticket.current_assignee = self.worker
        self.ticket.save(update_fields=("status", "current_assignee"))

    def run_pair(self, *operations):
        started = Barrier(2, timeout=10)

        def run(operation):
            close_old_connections()
            try:
                started.wait()
                operation()
            except ValidationError:
                return False
            finally:
                close_old_connections()
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            return tuple(pool.map(run, operations))

    def test_duplicate_requests_and_competing_reviews_commit_once(self):
        def submit():
            submit_cancellation(ticket=self.ticket, actor=self.reporter)

        self.assertEqual(sorted(self.run_pair(submit, submit)), [False, True])
        application = self.ticket.cancellations.get()
        self.assertEqual(sorted(self.run_pair(
            lambda: review_cancellation(application=application, actor=self.admin, decision="approved"),
            lambda: review_cancellation(application=application, actor=self.admin, decision="rejected", reason="继续处理"),
        )), [False, True])
        self.ticket.refresh_from_db()
        application.refresh_from_db()
        self.assertFalse(self.ticket.cancellation_pending)
        self.assertEqual(self.ticket.status, Ticket.Status.CANCELLED if application.decision == "approved" else Ticket.Status.IN_PROGRESS)
        self.assertEqual(self.ticket.history.filter(event_type=TicketHistory.EventType.CANCELLATION_CHANGED).count(), 2)

    def test_request_and_repair_result_cannot_both_commit(self):
        requested, transitioned = self.run_pair(
            lambda: submit_cancellation(ticket=self.ticket, actor=self.reporter),
            lambda: submit_repair_result(ticket_id=self.ticket.pk, actor=self.worker, version=0, content="维修结果"),
        )
        self.assertNotEqual(requested, transitioned)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.cancellation_pending, requested)
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_CONFIRMATION if transitioned else Ticket.Status.IN_PROGRESS)
        self.assertEqual(self.ticket.history.count(), 2)

    def test_request_and_progress_follow_the_committed_order(self):
        requested, progressed = self.run_pair(
            lambda: submit_cancellation(ticket=self.ticket, actor=self.reporter),
            lambda: add_repair_progress(ticket_id=self.ticket.pk, actor=self.worker,
                                        version=0, content="并发维修进度"),
        )
        self.assertTrue(requested)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS)
        self.assertTrue(self.ticket.cancellation_pending)
        self.assertEqual(self.ticket.repair_progress.count(), int(progressed))
        events = list(self.ticket.history.values_list("event_type", flat=True))
        self.assertEqual(events, ["created", "progress_added", "cancellation_changed"]
                         if progressed else ["created", "cancellation_changed"])

    def test_request_and_start_follow_the_committed_order(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(status=Ticket.Status.PENDING_WORK)
        requested, started = self.run_pair(
            lambda: submit_cancellation(ticket=self.ticket, actor=self.reporter),
            lambda: start_repair(ticket_id=self.ticket.pk, actor=self.worker, version=0),
        )
        self.assertTrue(requested)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.cancellation_pending)
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS
                         if started else Ticket.Status.PENDING_WORK)

    def test_cancel_cannot_race_past_resolved_confirmation(self):
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker, version=0, content="待确认结果",
        )
        result = self.ticket.repair_results.get()
        requested, confirmed = self.run_pair(
            lambda: submit_cancellation(ticket=self.ticket, actor=self.reporter),
            lambda: confirm_repair_result(
                ticket_id=self.ticket.pk, actor=self.reporter,
                version=self.ticket.repair_version, result_id=result.pk,
                decision="resolved",
            ),
        )
        self.assertFalse(requested)
        self.assertTrue(confirmed)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.COMPLETED)
        self.assertFalse(self.ticket.cancellation_pending)
        self.assertFalse(self.ticket.cancellations.exists())
        self.assertEqual(RepairConfirmation.objects.filter(result=result).count(), 1)

    def test_request_after_unresolved_confirmation_uses_the_new_state(self):
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker, version=0, content="待确认结果",
        )
        result = self.ticket.repair_results.get()
        requested, confirmed = self.run_pair(
            lambda: submit_cancellation(ticket=self.ticket, actor=self.reporter),
            lambda: confirm_repair_result(
                ticket_id=self.ticket.pk, actor=self.reporter,
                version=self.ticket.repair_version, result_id=result.pk,
                decision="unresolved", reason="仍未解决",
            ),
        )
        self.assertTrue(confirmed)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS)
        self.assertEqual(self.ticket.cancellation_pending, requested)
        self.assertEqual(self.ticket.cancellations.count(), int(requested))
        self.assertEqual(RepairConfirmation.objects.filter(result=result).count(), 1)

    def test_unique_constraint_rejects_a_second_pending_application(self):
        from django.db import IntegrityError

        submit_cancellation(ticket=self.ticket, actor=self.reporter)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketCancellation.objects.create(ticket=self.ticket, applicant=self.reporter)
        self.assertEqual(self.ticket.cancellations.count(), 1)
