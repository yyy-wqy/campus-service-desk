from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.test import TestCase

from accounts.models import MaintenanceApplication, User

from . import test_query_history as fixtures
from .models import (
    RepairConfirmation, RepairProgress, RepairResult, Ticket, TicketHistory,
)
from .repair_services import (
    add_repair_progress, confirm_repair_result,
    start_repair, submit_repair_result,
)


class RepairWorkflowServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.ticket = Ticket.objects.get(pk=self.second.pk)
        self.worker = self.users["worker_a"]

    def start(self):
        self.ticket = start_repair(
            ticket_id=self.ticket.pk,
            actor=self.worker,
            version=self.ticket.repair_version,
        )
        return self.ticket

    def result(self):
        self.start()
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk,
            actor=self.worker,
            version=self.ticket.repair_version,
            content="第一轮结果",
        )
        return self.ticket.repair_results.get()

    def confirm(self, result, decision, reason="", actor=None, version=None):
        self.ticket = confirm_repair_result(
            ticket_id=self.ticket.pk,
            actor=actor or self.users["student_a"],
            version=self.ticket.repair_version if version is None else version,
            result_id=result.pk,
            decision=decision,
            reason=reason,
        )
        return self.ticket

    def test_progress_and_result_do_not_directly_complete_ticket(self):
        self.start()
        self.ticket = add_repair_progress(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="检查线路",
        )
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="更换线路，等待确认",
        )
        self.assertEqual(self.ticket.status, "pending_confirmation")
        self.assertEqual(self.ticket.repair_version, 3)
        result = self.ticket.repair_results.get()
        self.assertEqual((result.round_number, result.worker_id), (1, self.worker.pk))
        self.assertEqual(self.ticket.repair_progress.get().content, "检查线路")
        self.assertFalse(self.ticket.history.filter(to_status="completed").exists())

    def test_admin_and_other_users_cannot_start(self):
        for name in ("manager", "student_a", "worker_b"):
            with self.subTest(name=name), self.assertRaises(PermissionDenied):
                start_repair(
                    ticket_id=self.ticket.pk,
                    actor=self.users[name], version=0,
                )
        self.ticket.refresh_from_db()
        self.assertEqual(
            (self.ticket.status, self.ticket.repair_version), ("pending_work", 0)
        )

    def test_stale_version_rejected_even_if_status_has_not_changed(self):
        self.start()
        version = self.ticket.repair_version
        add_repair_progress(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=version, content="第一次进度",
        )
        with self.assertRaises(ValidationError):
            submit_repair_result(
                ticket_id=self.ticket.pk, actor=self.worker,
                version=version, content="旧页面结果",
            )
        self.assertFalse(RepairResult.objects.exists())

    def test_pending_cancellation_freezes_worker_actions(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=True)
        with self.assertRaises(ValidationError):
            self.start()
        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=False)
        self.start()
        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=True)
        for action in (add_repair_progress, submit_repair_result):
            with self.subTest(action=action.__name__), self.assertRaises(ValidationError):
                action(
                    ticket_id=self.ticket.pk, actor=self.worker,
                    version=self.ticket.repair_version, content="被冻结",
                )
        self.assertFalse(RepairResult.objects.exists())
        self.assertFalse(RepairProgress.objects.exists())

    def test_history_failure_rolls_back_result_and_version(self):
        self.start()
        version = self.ticket.repair_version
        with patch.object(
            TicketHistory.objects, "create", side_effect=DatabaseError("failure")
        ):
            with self.assertRaises(ValidationError):
                submit_repair_result(
                    ticket_id=self.ticket.pk, actor=self.worker,
                    version=version, content="不应保留",
                )
        self.ticket.refresh_from_db()
        self.assertEqual(
            (self.ticket.status, self.ticket.repair_version), ("in_progress", version)
        )
        self.assertFalse(RepairResult.objects.exists())

    def test_disabled_account_and_revoked_qualification_are_rejected(self):
        User.objects.filter(pk=self.worker.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.start()
        User.objects.filter(pk=self.worker.pk).update(is_active=True)
        MaintenanceApplication.objects.filter(worker=self.worker).update(
            status="needs_more"
        )
        with self.assertRaises(PermissionDenied):
            self.start()

    def test_blank_result_is_rejected(self):
        self.start()
        with self.assertRaises(ValidationError):
            submit_repair_result(
                ticket_id=self.ticket.pk, actor=self.worker,
                version=self.ticket.repair_version, content="  ",
            )
        self.assertFalse(RepairResult.objects.exists())

    def test_multiple_rounds_retain_old_results_and_confirmations(self):
        first = self.result()
        self.confirm(first, "unresolved", "仍有异响")
        original = first.confirmation
        self.ticket = add_repair_progress(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="第二轮复检",
        )
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="第二轮更换轴承",
        )
        second = self.ticket.repair_results.get(round_number=2)
        self.confirm(second, "resolved")
        self.assertEqual(self.ticket.status, "completed")
        self.assertEqual(self.ticket.repair_results.count(), 2)
        self.assertEqual(RepairConfirmation.objects.count(), 2)
        original.refresh_from_db()
        self.assertEqual(
            (original.decision, original.reason), ("unresolved", "仍有异响")
        )
        self.assertEqual(second.confirmation.reporter, self.users["student_a"])
        self.assertEqual(self.ticket.repair_progress.get().round_number, 2)

    def test_only_original_reporter_can_confirm(self):
        result = self.result()
        for name in ("manager", "worker_a", "student_b"):
            with self.subTest(name=name), self.assertRaises(PermissionDenied):
                self.confirm(result, "resolved", actor=self.users[name])
        self.assertFalse(RepairConfirmation.objects.exists())

    def test_unresolved_requires_reason(self):
        result = self.result()
        with self.assertRaises(ValidationError):
            self.confirm(result, "unresolved", "   ")
        self.assertFalse(RepairConfirmation.objects.exists())

    def test_old_version_or_old_result_cannot_confirm_new_round(self):
        first = self.result()
        old_version = self.ticket.repair_version
        self.confirm(first, "unresolved", "请继续维修")
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="第二轮结果",
        )
        second = self.ticket.repair_results.get(round_number=2)
        cases = [
            (second, old_version),
            (first, self.ticket.repair_version),
        ]
        for result, version in cases:
            with self.subTest(result=result.pk), self.assertRaises(ValidationError):
                self.confirm(result, "resolved", version=version)
        self.assertFalse(
            RepairConfirmation.objects.filter(result=second).exists()
        )

    def test_duplicate_confirmation_does_not_append_twice(self):
        result = self.result()
        old_version = self.ticket.repair_version
        self.confirm(result, "resolved")
        history_count = self.ticket.history.count()
        with self.assertRaises(ValidationError):
            self.confirm(result, "resolved", version=old_version)
        self.assertEqual(self.ticket.history.count(), history_count)
        self.assertEqual(RepairConfirmation.objects.count(), 1)

    def test_cancellation_freezes_resolution_but_allows_note(self):
        from .note_services import add_ticket_note

        result = self.result()
        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=True)
        with self.assertRaises(ValidationError):
            self.confirm(result, "resolved")
        add_ticket_note(
            ticket_id=self.ticket.pk,
            actor=self.users["student_a"],
            text="取消待审期间补充情况",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "pending_confirmation")
        self.assertFalse(RepairConfirmation.objects.exists())

    def test_confirmation_history_failure_rolls_back_everything(self):
        result = self.result()
        version = self.ticket.repair_version
        with patch.object(
            TicketHistory.objects, "create", side_effect=DatabaseError("failure")
        ):
            with self.assertRaises(ValidationError):
                self.confirm(result, "resolved")
        self.ticket.refresh_from_db()
        self.assertEqual(
            (self.ticket.status, self.ticket.repair_version),
            ("pending_confirmation", version),
        )
        self.assertFalse(RepairConfirmation.objects.exists())

    def test_saved_results_and_confirmations_cannot_be_overwritten(self):
        result = self.result()
        self.confirm(result, "resolved")
        confirmation = result.confirmation
        with self.assertRaises(ValidationError):
            result.content = "覆盖旧结果"
            result.save()
        with self.assertRaises(ValidationError):
            RepairResult.objects.filter(pk=result.pk).update(content="覆盖")
        with self.assertRaises(ValidationError):
            confirmation.delete()
        result.refresh_from_db()
        self.assertEqual(result.content, "第一轮结果")

    def test_generic_transition_cannot_bypass_repair_records(self):
        from .services import transition_ticket

        self.start()
        with self.assertRaises(ValidationError):
            transition_ticket(
                ticket=self.ticket, actor=self.worker,
                to_status=Ticket.Status.PENDING_CONFIRMATION,
            )
        self.assertFalse(RepairResult.objects.exists())
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk, actor=self.worker,
            version=self.ticket.repair_version, content="通过新接口提交",
        )
        for status in (Ticket.Status.IN_PROGRESS, Ticket.Status.COMPLETED):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                transition_ticket(
                    ticket=self.ticket, actor=self.users["student_a"],
                    to_status=status,
                )
        self.assertFalse(RepairConfirmation.objects.exists())
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "pending_confirmation")

    def test_legacy_start_uses_freeze_and_version_checks(self):
        from .services import transition_ticket

        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=True)
        with self.assertRaises(ValidationError):
            transition_ticket(
                ticket=self.ticket, actor=self.worker,
                to_status=Ticket.Status.IN_PROGRESS,
            )
        Ticket.objects.filter(pk=self.ticket.pk).update(cancellation_pending=False)
        changed = transition_ticket(
            ticket=self.ticket, actor=self.worker,
            to_status=Ticket.Status.IN_PROGRESS,
        )
        self.assertEqual((changed.status, changed.repair_version), ("in_progress", 1))
        count = changed.history.count()
        with self.assertRaises(ValidationError):
            transition_ticket(
                ticket=self.ticket, actor=self.worker,
                to_status=Ticket.Status.IN_PROGRESS,
            )
        self.assertEqual(changed.history.count(), count)
