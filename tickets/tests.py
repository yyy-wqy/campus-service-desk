from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from locations.models import Building, FaultCategory

from .models import Ticket, TicketDailySequence, TicketHistory
from .permissions import (
    can_confirm_ticket,
    can_create_ticket,
    can_manage_ticket,
    can_process_ticket,
)
from .services import TicketNumberExhausted, create_ticket, transition_ticket


class TicketFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.student = user_model.objects.create_user(
            username="student-one",
            password="test-password",
            role="student",
            approval_status="approved",
            real_name="张同学",
            phone="13800138000",
        )
        cls.teacher = user_model.objects.create_user(
            username="teacher-one",
            password="test-password",
            role="teacher",
            approval_status="approved",
            real_name="李老师",
            phone="13900139000",
        )
        cls.maintenance = user_model.objects.create_user(
            username="maintenance-one",
            password="test-password",
            role="maintenance",
            approval_status="approved",
        )
        cls.other_maintenance = user_model.objects.create_user(
            username="maintenance-two",
            password="test-password",
            role="maintenance",
            approval_status="approved",
        )
        cls.admin = user_model.objects.create_user(
            username="admin-one",
            password="test-password",
            role="admin",
            approval_status="approved",
        )
        cls.pending_student = user_model.objects.create_user(
            username="pending-student",
            password="test-password",
            role="student",
            approval_status="pending",
        )
        cls.teaching_building = Building.objects.create(
            name="第一教学楼",
            building_type="teach",
        )
        cls.dorm_building = Building.objects.create(
            name="学生一舍",
            building_type="dorm",
        )
        cls.category = FaultCategory.objects.create(name="水电设施")
        cls.teacher_category = FaultCategory.objects.create(
            name="多媒体设备",
            is_teacher_only=True,
        )

    def create_valid_ticket(self, **overrides):
        values = {
            "reporter": self.student,
            "building": self.teaching_building,
            "fault_category": self.category,
            "title": "走廊灯具损坏",
            "description": "三楼东侧灯具无法点亮。",
            "location_kind": Ticket.LocationKind.PUBLIC_AREA,
            "location_detail": "三楼东侧走廊",
            "preferred_visit_time": "工作日 14:00 后",
        }
        values.update(overrides)
        return create_ticket(**values)


class TicketCreationServiceTests(TicketFixtureMixin, TestCase):
    def test_number_increments_and_resets_by_business_date(self):
        with patch("tickets.services.timezone.localdate", return_value=date(2026, 11, 5)):
            first = self.create_valid_ticket()
            second = self.create_valid_ticket(title="第二个故障")
        with patch("tickets.services.timezone.localdate", return_value=date(2026, 11, 6)):
            next_day = self.create_valid_ticket(title="次日故障")

        self.assertEqual(first.number, "CS202611050001")
        self.assertEqual(second.number, "CS202611050002")
        self.assertEqual(next_day.number, "CS202611060001")

    def test_creation_sets_server_owned_fields_and_first_history(self):
        ticket = self.create_valid_ticket()

        self.assertEqual(ticket.reporter, self.student)
        self.assertEqual(ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertEqual(ticket.priority, Ticket.Priority.NORMAL)
        self.assertIsNone(ticket.current_assignee)
        history = ticket.history.get()
        self.assertEqual(history.event_type, TicketHistory.EventType.CREATED)
        self.assertEqual(history.actor, self.student)
        self.assertEqual(history.to_status, Ticket.Status.PENDING_ACCEPTANCE)

    def test_snapshot_does_not_change_with_source_records(self):
        ticket = self.create_valid_ticket()
        self.student.real_name = "新姓名"
        self.student.phone = "13700137000"
        self.student.save()
        self.teaching_building.name = "教学楼新名称"
        self.teaching_building.save()
        self.category.name = "新分类"
        self.category.save()
        ticket.refresh_from_db()

        self.assertEqual(ticket.contact_name_snapshot, "张同学")
        self.assertEqual(ticket.contact_phone_snapshot, "13800138000")
        self.assertEqual(ticket.building_name_snapshot, "第一教学楼")
        self.assertEqual(ticket.category_name_snapshot, "水电设施")

    def test_original_submission_fields_cannot_be_overwritten(self):
        ticket = self.create_valid_ticket()
        ticket.title = "篡改后的标题"

        with self.assertRaises(ValidationError):
            ticket.save()

        ticket.refresh_from_db()
        self.assertEqual(ticket.title, "走廊灯具损坏")

    def test_daily_sequence_exhaustion_has_no_partial_ticket(self):
        business_date = date(2026, 11, 5)
        TicketDailySequence.objects.create(
            business_date=business_date,
            last_value=9999,
        )

        with patch("tickets.services.timezone.localdate", return_value=business_date):
            with self.assertRaises(TicketNumberExhausted):
                self.create_valid_ticket()

        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(
            TicketDailySequence.objects.get(business_date=business_date).last_value,
            9999,
        )

    def test_history_failure_rolls_back_ticket_and_sequence(self):
        business_date = date(2026, 11, 5)
        with patch("tickets.services.timezone.localdate", return_value=business_date):
            with patch.object(TicketHistory.objects, "create", side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    self.create_valid_ticket()

        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

        with patch("tickets.services.timezone.localdate", return_value=business_date):
            ticket = self.create_valid_ticket()
        self.assertEqual(ticket.number, "CS202611050001")

    def test_invalid_roles_approval_and_scope_are_rejected(self):
        invalid_cases = (
            ({"reporter": self.maintenance}, PermissionDenied),
            ({"reporter": self.pending_student}, PermissionDenied),
            ({"reporter": self.student, "building": self.dorm_building}, ValidationError),
            (
                {"reporter": self.student, "fault_category": self.teacher_category},
                ValidationError,
            ),
            ({"reporter": self.teacher, "building": self.dorm_building}, ValidationError),
        )
        for overrides, expected_error in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(expected_error):
                    self.create_valid_ticket(**overrides)
        self.assertFalse(Ticket.objects.exists())

    def test_history_is_append_only(self):
        ticket = self.create_valid_ticket()
        history = ticket.history.get()
        history.description = "覆盖历史"
        with self.assertRaises(ValidationError):
            history.save()
        with self.assertRaises(ValidationError):
            history.delete()
        with self.assertRaises(TypeError):
            TicketHistory.objects.filter(pk=history.pk).update(description="覆盖")
        with self.assertRaises(TypeError):
            TicketHistory.objects.filter(pk=history.pk).delete()

    def test_status_transition_and_history_are_atomic(self):
        ticket = self.create_valid_ticket()
        changed = transition_ticket(
            ticket=ticket,
            actor=self.admin,
            to_status=Ticket.Status.PENDING_WORK,
            description="管理员受理",
        )
        self.assertEqual(changed.status, Ticket.Status.PENDING_WORK)
        self.assertEqual(changed.history.count(), 2)

        with self.assertRaises(ValidationError):
            transition_ticket(
                ticket=changed,
                actor=self.admin,
                to_status=Ticket.Status.COMPLETED,
            )

    def test_status_transition_rejects_actor_impersonation(self):
        ticket = self.create_valid_ticket()
        ticket = transition_ticket(
            ticket=ticket,
            actor=self.admin,
            to_status=Ticket.Status.PENDING_WORK,
        )

        with self.assertRaises(PermissionDenied):
            transition_ticket(
                ticket=ticket,
                actor=self.admin,
                to_status=Ticket.Status.IN_PROGRESS,
            )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.PENDING_WORK)
        self.assertEqual(ticket.history.count(), 2)

        ticket.current_assignee = self.maintenance
        ticket.save(update_fields=("current_assignee", "updated_at"))
        changed = transition_ticket(
            ticket=ticket,
            actor=self.maintenance,
            to_status=Ticket.Status.IN_PROGRESS,
        )
        self.assertEqual(changed.status, Ticket.Status.IN_PROGRESS)
        self.assertEqual(changed.history.count(), 3)


class TicketPermissionTests(TicketFixtureMixin, TestCase):
    def test_action_permissions_are_role_and_ownership_specific(self):
        ticket = self.create_valid_ticket()
        ticket.current_assignee = self.maintenance
        ticket.save(update_fields=("current_assignee", "updated_at"))

        self.assertTrue(can_create_ticket(self.student))
        self.assertFalse(can_create_ticket(self.maintenance))
        self.assertTrue(can_manage_ticket(self.admin))
        self.assertFalse(can_process_ticket(self.admin, ticket))
        self.assertTrue(can_process_ticket(self.maintenance, ticket))
        self.assertFalse(can_process_ticket(self.other_maintenance, ticket))
        self.assertTrue(can_confirm_ticket(self.student, ticket))
        self.assertFalse(can_confirm_ticket(self.admin, ticket))


class TicketViewTests(TicketFixtureMixin, TestCase):
    def valid_form_data(self):
        return {
            "building": self.teaching_building.pk,
            "fault_category": self.category.pk,
            "title": "门锁损坏",
            "description": "教室前门无法正常上锁。",
            "location_kind": Ticket.LocationKind.ROOM,
            "location_detail": "A301",
            "preferred_visit_time": "下午",
        }

    def test_create_view_ignores_tampered_server_owned_fields(self):
        self.client.force_login(self.student)
        data = self.valid_form_data()
        data.update(
            {
                "reporter": self.teacher.pk,
                "status": Ticket.Status.COMPLETED,
                "priority": Ticket.Priority.URGENT,
                "current_assignee": self.maintenance.pk,
            }
        )
        response = self.client.post(reverse("tickets:create"), data)

        ticket = Ticket.objects.get()
        self.assertRedirects(response, reverse("tickets:detail", args=(ticket.pk,)))
        self.assertEqual(ticket.reporter, self.student)
        self.assertEqual(ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertEqual(ticket.priority, Ticket.Priority.NORMAL)
        self.assertIsNone(ticket.current_assignee)

    def test_invalid_submission_creates_no_partial_data(self):
        self.client.force_login(self.student)
        data = self.valid_form_data()
        data["title"] = ""
        response = self.client.post(reverse("tickets:create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())

    def test_list_and_detail_only_show_visible_tickets(self):
        ticket = self.create_valid_ticket()
        other_student = get_user_model().objects.create_user(
            username="other-student",
            password="test-password",
            role="student",
            approval_status="approved",
        )
        for user, expected in (
            (self.student, 200),
            (other_student, 404),
            (self.admin, 200),
            (self.maintenance, 404),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("tickets:detail", args=(ticket.pk,)))
                self.assertEqual(response.status_code, expected)

        ticket.current_assignee = self.maintenance
        ticket.save(update_fields=("current_assignee", "updated_at"))
        self.client.force_login(self.maintenance)
        self.assertEqual(
            self.client.get(reverse("tickets:detail", args=(ticket.pk,))).status_code,
            200,
        )

    def test_pending_user_is_blocked_by_business_middleware(self):
        self.client.force_login(self.pending_student)
        response = self.client.get(reverse("tickets:create"))
        self.assertEqual(response.status_code, 403)

    def test_maintenance_cannot_open_create_form(self):
        self.client.force_login(self.maintenance)
        response = self.client.get(reverse("tickets:create"))
        self.assertEqual(response.status_code, 403)


class ConcurrentTicketNumberTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.reporter = user_model.objects.create_user(
            username="parallel-student",
            password="test-password",
            role="student",
            approval_status="approved",
            real_name="并发测试用户",
            phone="13600136000",
        )
        self.building = Building.objects.create(name="并发教学楼", building_type="teach")
        self.category = FaultCategory.objects.create(name="并发测试分类")

    def create_in_thread(self, index):
        close_old_connections()
        try:
            ticket = create_ticket(
                reporter=self.reporter,
                building=self.building,
                fault_category=self.category,
                title=f"并发工单 {index}",
                description="并发编号测试",
                location_kind=Ticket.LocationKind.PUBLIC_AREA,
                location_detail=f"测试位置 {index}",
            )
            return ticket.number
        finally:
            close_old_connections()

    def test_concurrent_successes_have_unique_numbers(self):
        with patch("tickets.services.timezone.localdate", return_value=date(2026, 11, 5)):
            with ThreadPoolExecutor(max_workers=4) as pool:
                numbers = list(pool.map(self.create_in_thread, range(4)))

        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(
            sorted(numbers),
            [f"CS20261105{sequence:04d}" for sequence in range(1, 5)],
        )
        self.assertEqual(Ticket.objects.count(), 4)
        self.assertEqual(TicketHistory.objects.count(), 4)
