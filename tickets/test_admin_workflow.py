from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import MaintenanceApplication, MaintenanceMaterial
from locations.models import Building, FaultCategory

from .models import Ticket, TicketHistory
from .permissions import visible_tickets
from .services import (
    TicketOperationFailed,
    accept_and_assign_ticket,
    reject_ticket,
    set_ticket_priority,
    transition_ticket,
)
from .services import create_ticket
from .workflow_forms import TicketAssignmentForm


class AdminTicketWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_user(
            username="workflow-admin",
            password="test-password",
            role="admin",
            approval_status="approved",
            is_staff=True,
        )
        cls.student = user_model.objects.create_user(
            username="workflow-student",
            password="test-password",
            role="student",
            approval_status="approved",
            real_name="报修学生",
            phone="13600136000",
        )
        cls.building = Building.objects.create(
            name="流程测试教学楼",
            building_type="teach",
        )
        cls.category = FaultCategory.objects.create(name="流程测试电气设备")

    def setUp(self):
        self.ticket = self.create_ticket()

    def create_ticket(self, title="流程测试工单"):
        return create_ticket(
            reporter=self.student,
            building=self.building,
            fault_category=self.category,
            title=title,
            description="用于管理员受理与分派测试。",
            location_kind=Ticket.LocationKind.ROOM,
            room_number="A101",
        )

    def create_worker(
        self,
        username,
        *,
        application_status=MaintenanceApplication.Status.APPROVED,
        account_status="approved",
        active=True,
        role="maintenance",
        direction="给排水",
        qualification_name="设备维修证",
    ):
        worker = get_user_model().objects.create_user(
            username=username,
            password="test-password",
            role=role,
            approval_status=account_status,
            is_active=active,
            real_name=f"{username}姓名",
        )
        application = MaintenanceApplication.objects.create(
            worker=worker,
            status=application_status,
            version=1,
        )
        if application_status == MaintenanceApplication.Status.APPROVED:
            MaintenanceMaterial.objects.create(
                application=application,
                category=MaintenanceMaterial.Category.QUALIFICATION,
                name=qualification_name,
                direction=direction,
                storage_name=f"{username}-qualification.pdf",
                original_name="qualification.pdf",
                content_type="application/pdf",
                size=100,
                sha256="a" * 64,
            )
        return worker

    def test_admin_atomically_accepts_and_assigns_while_ordinary_user_is_denied(self):
        worker = self.create_worker("atomic-worker")
        original = (self.ticket.title, self.ticket.description, self.ticket.location_detail)

        with self.assertRaises(PermissionDenied):
            accept_and_assign_ticket(
                ticket=self.ticket,
                actor=self.student,
                worker=worker,
            )

        accepted = accept_and_assign_ticket(
            ticket=self.ticket,
            actor=self.admin,
            worker=worker,
        )

        self.assertEqual(accepted.status, Ticket.Status.PENDING_WORK)
        self.assertEqual(accepted.current_assignee, worker)
        self.assertEqual(
            (accepted.title, accepted.description, accepted.location_detail),
            original,
        )
        event = accepted.history.filter(
            event_type=TicketHistory.EventType.STATUS_CHANGED
        ).get()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.from_status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertEqual(event.to_status, Ticket.Status.PENDING_WORK)
        self.assertEqual(event.metadata["action"], "accepted")
        assignment = accepted.history.filter(
            event_type=TicketHistory.EventType.ASSIGNED
        ).get()
        self.assertEqual(assignment.metadata["assignee_id"], worker.pk)

    def test_rejection_requires_reason_source_state_and_is_irreversible(self):
        with self.assertRaises(ValidationError):
            reject_ticket(ticket=self.ticket, actor=self.admin, reason="   ")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)

        rejected = reject_ticket(
            ticket=self.ticket,
            actor=self.admin,
            reason="报修内容不属于校园设施范围",
        )

        self.assertEqual(rejected.status, Ticket.Status.REJECTED)
        event = rejected.history.filter(
            event_type=TicketHistory.EventType.STATUS_CHANGED
        ).get()
        self.assertEqual(event.metadata["reason"], "报修内容不属于校园设施范围")
        self.assertIn("不予受理", event.description)
        worker = self.create_worker("rejected-ticket-worker")
        with self.assertRaises(ValidationError):
            accept_and_assign_ticket(
                ticket=rejected,
                actor=self.admin,
                worker=worker,
            )
        with self.assertRaises(ValidationError):
            reject_ticket(ticket=rejected, actor=self.admin, reason="再次拒绝")
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Ticket.Status.REJECTED)
        event.description = "试图覆盖原因"
        with self.assertRaises(ValidationError):
            event.save()

        accepted_ticket = self.create_ticket(title="已受理工单")
        accepted_ticket = accept_and_assign_ticket(
            ticket=accepted_ticket,
            actor=self.admin,
            worker=worker,
        )
        with self.assertRaises(ValidationError):
            reject_ticket(ticket=accepted_ticket, actor=self.admin, reason="来源状态错误")

    def test_priority_validation_permission_status_and_history(self):
        with self.assertRaises(PermissionDenied):
            set_ticket_priority(
                ticket=self.ticket,
                actor=self.student,
                priority=Ticket.Priority.URGENT,
            )
        with self.assertRaises(ValidationError):
            set_ticket_priority(
                ticket=self.ticket,
                actor=self.admin,
                priority="critical",
            )

        changed = set_ticket_priority(
            ticket=self.ticket,
            actor=self.admin,
            priority=Ticket.Priority.URGENT,
        )

        self.assertEqual(changed.priority, Ticket.Priority.URGENT)
        event = changed.history.filter(
            event_type=TicketHistory.EventType.PRIORITY_CHANGED
        ).get()
        self.assertEqual(event.metadata["from_priority"], Ticket.Priority.NORMAL)
        self.assertEqual(event.metadata["to_priority"], Ticket.Priority.URGENT)
        with self.assertRaises(ValidationError):
            set_ticket_priority(
                ticket=changed,
                actor=self.admin,
                priority=Ticket.Priority.URGENT,
            )

        worker = self.create_worker("priority-status-worker")
        changed = accept_and_assign_ticket(
            ticket=changed,
            actor=self.admin,
            worker=worker,
        )
        changed = transition_ticket(
            ticket=changed,
            actor=worker,
            to_status=Ticket.Status.IN_PROGRESS,
        )
        with self.assertRaises(ValidationError):
            set_ticket_priority(
                ticket=changed,
                actor=self.admin,
                priority=Ticket.Priority.NORMAL,
            )
        changed.refresh_from_db()
        self.assertEqual(changed.priority, Ticket.Priority.URGENT)

    def test_database_constraints_reject_assignment_state_bypasses(self):
        with self.assertRaises(ValidationError):
            transition_ticket(
                ticket=self.ticket,
                actor=self.admin,
                to_status=Ticket.Status.PENDING_WORK,
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.filter(pk=self.ticket.pk).update(
                status=Ticket.Status.PENDING_WORK
            )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(self.ticket.current_assignee)

        worker = self.create_worker("constraint-worker")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.filter(pk=self.ticket.pk).update(
                current_assignee=worker
            )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(self.ticket.current_assignee)

    def test_eligible_worker_with_different_direction_can_be_assigned_once(self):
        worker = self.create_worker(
            "eligible-worker",
            direction="园林维护",
            qualification_name="园林专业证",
        )
        other_worker = self.create_worker("other-eligible-worker")
        assigned = accept_and_assign_ticket(
            ticket=self.ticket,
            actor=self.admin,
            worker=worker,
        )

        self.assertEqual(assigned.current_assignee, worker)
        self.assertTrue(visible_tickets(worker).filter(pk=assigned.pk).exists())
        event = assigned.history.filter(event_type=TicketHistory.EventType.ASSIGNED).get()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.metadata["assignee_id"], worker.pk)
        self.assertIn(worker.username, event.description)
        with self.assertRaises(ValidationError):
            accept_and_assign_ticket(
                ticket=assigned,
                actor=self.admin,
                worker=other_worker,
            )
        assigned.refresh_from_db()
        self.assertEqual(assigned.current_assignee, worker)

    def test_unqualified_inactive_or_nonmaintenance_accounts_cannot_be_assigned(self):
        invalid_workers = (
            self.create_worker(
                "draft-worker",
                application_status=MaintenanceApplication.Status.DRAFT,
                account_status="pending",
            ),
            self.create_worker(
                "pending-worker",
                application_status=MaintenanceApplication.Status.PENDING,
                account_status="pending",
            ),
            self.create_worker(
                "needs-more-worker",
                application_status=MaintenanceApplication.Status.NEEDS_MORE,
                account_status="pending",
            ),
            self.create_worker(
                "rejected-worker",
                application_status=MaintenanceApplication.Status.REJECTED,
                account_status="rejected",
            ),
            self.create_worker("inactive-worker", active=False),
            self.create_worker("not-maintenance", role="teacher"),
        )

        for index, worker in enumerate(invalid_workers):
            with self.subTest(worker=worker.username):
                ticket = self.create_ticket(title=f"无效分派 {index}")
                with self.assertRaises(ValidationError):
                    accept_and_assign_ticket(
                        ticket=ticket,
                        actor=self.admin,
                        worker=worker,
                    )
                ticket.refresh_from_db()
                self.assertEqual(ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
                self.assertIsNone(ticket.current_assignee)
                self.assertFalse(
                    ticket.history.filter(
                        event_type=TicketHistory.EventType.ASSIGNED
                    ).exists()
                )

    def test_assignment_form_shows_only_eligible_worker_and_qualification_summary(self):
        eligible = self.create_worker(
            "listed-worker",
            direction="制冷与空调",
            qualification_name="制冷设备维修证",
        )
        invalid = self.create_worker(
            "hidden-worker",
            application_status=MaintenanceApplication.Status.NEEDS_MORE,
            account_status="pending",
        )

        form = TicketAssignmentForm()
        candidate_ids = set(form.fields["worker"].queryset.values_list("pk", flat=True))
        candidate = form.fields["worker"].queryset.get(pk=eligible.pk)
        label = form.fields["worker"].label_from_instance(candidate)

        self.assertIn(eligible.pk, candidate_ids)
        self.assertNotIn(invalid.pk, candidate_ids)
        self.assertIn("制冷与空调", label)
        self.assertIn("制冷设备维修证", label)

    def test_second_history_failure_rolls_back_acceptance_and_page_returns_feedback(self):
        worker = self.create_worker("rollback-worker")
        original_create = TicketHistory.objects.create
        call_count = 0

        def fail_second_history(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("history write failed")
            return original_create(*args, **kwargs)

        with patch.object(
            TicketHistory.objects,
            "create",
            side_effect=fail_second_history,
        ):
            with self.assertRaises(TicketOperationFailed):
                accept_and_assign_ticket(
                    ticket=self.ticket,
                    actor=self.admin,
                    worker=worker,
                )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(self.ticket.current_assignee)
        self.assertFalse(
            self.ticket.history.filter(
                event_type__in=(
                    TicketHistory.EventType.STATUS_CHANGED,
                    TicketHistory.EventType.ASSIGNED,
                )
            ).exists()
        )

        self.client.force_login(self.admin)
        call_count = 0
        with patch.object(
            TicketHistory.objects,
            "create",
            side_effect=fail_second_history,
        ):
            response = self.client.post(
                reverse("tickets:accept", args=(self.ticket.pk,)),
                {"worker": worker.pk},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "工单操作暂时无法完成，数据未发生变化")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(self.ticket.current_assignee)

    def test_database_save_failure_returns_feedback_without_partial_changes(self):
        worker = self.create_worker("database-failure-worker")
        self.client.force_login(self.admin)

        with patch.object(Ticket, "save", side_effect=DatabaseError("write failed")):
            response = self.client.post(
                reverse("tickets:accept", args=(self.ticket.pk,)),
                {"worker": worker.pk},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "数据未发生变化，请稍后重试")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(self.ticket.current_assignee)
        self.assertFalse(
            self.ticket.history.filter(
                event_type__in=(
                    TicketHistory.EventType.STATUS_CHANGED,
                    TicketHistory.EventType.ASSIGNED,
                )
            ).exists()
        )

    def test_temporary_database_lock_is_retried_without_duplicate_history(self):
        worker = self.create_worker("lock-retry-worker")
        original_save = Ticket.save
        attempts = 0

        def save_with_one_lock(instance, *args, **kwargs):
            nonlocal attempts
            if instance.pk == self.ticket.pk and "status" in kwargs.get(
                "update_fields", ()
            ):
                attempts += 1
                if attempts == 1:
                    raise OperationalError("database is locked")
            return original_save(instance, *args, **kwargs)

        self.client.force_login(self.admin)
        with patch.object(Ticket, "save", new=save_with_one_lock):
            response = self.client.post(
                reverse("tickets:accept", args=(self.ticket.pk,)),
                {"worker": worker.pk},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "工单已受理并完成首次分派")
        self.assertEqual(attempts, 2)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.current_assignee, worker)
        self.assertEqual(
            self.ticket.history.filter(
                event_type=TicketHistory.EventType.STATUS_CHANGED
            ).count(),
            1,
        )
        self.assertEqual(
            self.ticket.history.filter(
                event_type=TicketHistory.EventType.ASSIGNED
            ).count(),
            1,
        )

    def test_admin_pages_show_actions_feedback_and_worker_task_visibility(self):
        worker = self.create_worker(
            "view-worker",
            direction="弱电维修",
            qualification_name="低压电工证",
        )
        self.client.force_login(self.admin)
        detail_url = reverse("tickets:detail", args=(self.ticket.pk,))
        response = self.client.get(detail_url)
        self.assertContains(response, "确认受理并分派")
        self.assertContains(response, "不予受理原因")
        self.assertContains(response, "弱电维修")
        self.assertContains(response, "低压电工证")

        response = self.client.post(
            reverse("tickets:accept", args=(self.ticket.pk,)),
            {"worker": worker.pk},
            follow=True,
        )
        self.assertContains(response, "工单已受理并完成首次分派")

        self.client.force_login(worker)
        task_response = self.client.get(reverse("tickets:tasks"))
        self.assertContains(task_response, self.ticket.number)

        self.client.force_login(self.student)
        denied = self.client.post(reverse("tickets:set_priority", args=(self.ticket.pk,)))
        self.assertEqual(denied.status_code, 403)
