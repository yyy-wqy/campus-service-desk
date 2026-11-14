from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import MaintenanceApplication
from locations.models import (
    Building,
    FaultCategory,
    PublicAreaOption,
    StudentHousing,
)

from .models import Ticket, TicketDailySequence, TicketHistory
from .permissions import (
    can_confirm_ticket,
    can_create_ticket,
    can_manage_ticket,
    can_process_ticket,
)
from .services import (
    TicketCreationFailed,
    TicketNumberExhausted,
    create_ticket,
    transition_ticket,
)


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
        for worker in (cls.maintenance, cls.other_maintenance):
            MaintenanceApplication.objects.create(worker=worker, status=MaintenanceApplication.Status.APPROVED)
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
        cls.corridor, _ = PublicAreaOption.objects.get_or_create(
            name="走廊",
            defaults={"sort_order": 1},
        )

    def create_valid_ticket(self, **overrides):
        values = {
            "reporter": self.student,
            "building": self.teaching_building,
            "fault_category": self.category,
            "title": "走廊灯具损坏",
            "description": "三楼东侧灯具无法点亮。",
            "location_kind": Ticket.LocationKind.ROOM,
            "room_number": "A301",
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

    def test_creation_uses_fresh_source_records_for_snapshots(self):
        stale_teacher = type(self.teacher).objects.get(pk=self.teacher.pk)
        stale_building = Building.objects.get(pk=self.teaching_building.pk)
        stale_category = FaultCategory.objects.get(pk=self.category.pk)
        type(self.teacher).objects.filter(pk=self.teacher.pk).update(
            real_name="更新后的李老师",
            phone="13700137001",
        )
        Building.objects.filter(pk=self.teaching_building.pk).update(
            name="更新后的教学楼",
        )
        FaultCategory.objects.filter(pk=self.category.pk).update(
            name="更新后的设备分类",
            is_teacher_only=True,
        )

        ticket = self.create_valid_ticket(
            reporter=stale_teacher,
            building=stale_building,
            fault_category=stale_category,
        )

        self.assertEqual(ticket.contact_name_snapshot, "更新后的李老师")
        self.assertEqual(ticket.contact_phone_snapshot, "13700137001")
        self.assertEqual(ticket.building_name_snapshot, "更新后的教学楼")
        self.assertEqual(ticket.category_name_snapshot, "更新后的设备分类")
        self.assertTrue(ticket.category_teacher_only_snapshot)

    def test_creation_rechecks_current_phone_instead_of_cached_value(self):
        stale_student = type(self.student).objects.get(pk=self.student.pk)
        type(self.student).objects.filter(pk=self.student.pk).update(phone="")

        with self.assertRaises(ValidationError) as context:
            self.create_valid_ticket(reporter=stale_student)

        self.assertIn("补齐手机号", context.exception.message_dict["reporter"][0])
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

    def test_original_submission_fields_cannot_be_overwritten(self):
        ticket = self.create_valid_ticket()
        ticket.title = "篡改后的标题"

        with self.assertRaises(ValidationError):
            ticket.save()

        ticket.refresh_from_db()
        self.assertEqual(ticket.title, "走廊灯具损坏")

    def test_queryset_update_rejects_original_and_snapshot_fields(self):
        ticket = self.create_valid_ticket()

        for field, value in (
            ("title", "批量篡改后的标题"),
            ("contact_phone_snapshot", "13900139000"),
            ("building_id", self.dorm_building.pk),
            ("building", self.dorm_building),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Ticket.objects.filter(pk=ticket.pk).update(**{field: value})

        ticket.refresh_from_db()
        self.assertEqual(ticket.title, "走廊灯具损坏")
        self.assertEqual(ticket.contact_phone_snapshot, "13800138000")
        self.assertEqual(ticket.building, self.teaching_building)

    def test_queryset_update_allows_mutable_workflow_fields(self):
        ticket = self.create_valid_ticket()

        updated = Ticket.objects.filter(pk=ticket.pk).update(
            status=Ticket.Status.PENDING_WORK,
            priority=Ticket.Priority.URGENT,
        )

        self.assertEqual(updated, 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.PENDING_WORK)
        self.assertEqual(ticket.priority, Ticket.Priority.URGENT)

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
                with self.assertRaises(TicketCreationFailed):
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

    def test_creation_service_enforces_current_student_housing(self):
        StudentHousing.objects.create(
            student=self.student,
            building=self.dorm_building,
        )
        own_dorm_ticket = self.create_valid_ticket(building=self.dorm_building)
        other_dorm = Building.objects.create(
            name="服务层越界宿舍",
            building_type="dorm",
        )

        with self.assertRaises(ValidationError) as context:
            self.create_valid_ticket(building=other_dorm)

        self.assertIn("building", context.exception.message_dict)
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(TicketHistory.objects.count(), 1)
        self.assertEqual(own_dorm_ticket.building, self.dorm_building)

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

    def test_assignment_does_not_grant_access_without_approved_qualification(self):
        ticket = self.create_valid_ticket()
        ticket.current_assignee = self.maintenance
        ticket.save(update_fields=("current_assignee", "updated_at"))
        for status in ("draft", "pending", "needs_more", "rejected"):
            with self.subTest(status=status):
                MaintenanceApplication.objects.filter(worker=self.maintenance).update(status=status)
                self.assertFalse(can_process_ticket(self.maintenance, ticket))
        MaintenanceApplication.objects.filter(worker=self.maintenance).update(status="approved")
        self.assertTrue(can_process_ticket(self.maintenance, ticket))
        get_user_model().objects.filter(pk=self.maintenance.pk).update(is_active=False)
        self.assertFalse(can_process_ticket(self.maintenance, ticket))


class TicketViewTests(TicketFixtureMixin, TestCase):
    def valid_form_data(self):
        return {
            "building": self.teaching_building.pk,
            "fault_category": self.category.pk,
            "title": "门锁损坏",
            "description": "教室前门无法正常上锁。",
            "location_kind": Ticket.LocationKind.ROOM,
            "room_number": "A301",
            "public_area": "",
            "public_area_other": "",
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

    def test_empty_submission_shows_required_errors_without_creating_ticket(self):
        self.client.force_login(self.student)

        response = self.client.post(reverse("tickets:create"), {})

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        for field in (
            "building",
            "fault_category",
            "title",
            "description",
            "location_kind",
        ):
            with self.subTest(field=field):
                self.assertIn(field, form.errors)
                self.assertIn("这个字段是必填项。", form.errors[field])
        self.assertContains(response, "这个字段是必填项。")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())

    def test_student_can_create_ticket_for_own_dorm_room(self):
        StudentHousing.objects.create(
            student=self.student,
            building=self.dorm_building,
        )
        self.client.force_login(self.student)
        data = self.valid_form_data()
        data["building"] = self.dorm_building.pk

        response = self.client.post(reverse("tickets:create"), data)

        ticket = Ticket.objects.get()
        self.assertRedirects(response, reverse("tickets:detail", args=(ticket.pk,)))
        self.assertEqual(ticket.building, self.dorm_building)
        self.assertEqual(ticket.location_kind, Ticket.LocationKind.ROOM)
        self.assertEqual(ticket.location_detail, "A301")

    def test_student_cannot_forge_another_dorm(self):
        StudentHousing.objects.create(
            student=self.student,
            building=self.dorm_building,
        )
        other_dorm = Building.objects.create(
            name="学生二舍",
            building_type="dorm",
        )
        self.client.force_login(self.student)
        data = self.valid_form_data()
        data["building"] = other_dorm.pk

        response = self.client.post(reverse("tickets:create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "选择一个有效的选项")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

    def test_invalid_public_area_option_and_forged_text_are_rejected(self):
        self.client.force_login(self.student)
        data = self.valid_form_data()
        data.update(
            {
                "location_kind": Ticket.LocationKind.PUBLIC_AREA,
                "room_number": "",
                "public_area": 10**9,
            }
        )

        response = self.client.post(reverse("tickets:create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "选择一个有效的选项")
        self.assertFalse(Ticket.objects.exists())

        data["public_area"] = self.corridor.pk
        data["public_area_other"] = "伪造的公共区域文本"
        response = self.client.post(reverse("tickets:create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "该公共区域不是“其他”选项")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

    def test_missing_phone_shows_actionable_error_without_allocating_number(self):
        user_model = get_user_model()
        legacy_student = user_model.objects.create_user(
            username="legacy-student",
            password="test-password",
            role="student",
            approval_status="approved",
            real_name="旧账号学生",
            phone="",
        )
        self.client.force_login(legacy_student)

        response = self.client.post(reverse("tickets:create"), self.valid_form_data())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请先在个人资料中补齐手机号后再提交报修")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

    def test_history_write_failure_shows_retry_error_and_rolls_back_number(self):
        self.client.force_login(self.student)
        with patch.object(TicketHistory.objects, "create", side_effect=RuntimeError):
            response = self.client.post(
                reverse("tickets:create"),
                self.valid_form_data(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "工单暂时无法提交，请稍后重试")
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketHistory.objects.exists())
        self.assertFalse(TicketDailySequence.objects.exists())

        response = self.client.post(reverse("tickets:create"), self.valid_form_data())
        ticket = Ticket.objects.get()
        self.assertRedirects(response, reverse("tickets:detail", args=(ticket.pk,)))
        self.assertTrue(ticket.number.endswith("0001"))

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


class TicketAdminPermissionTests(TicketFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        user_model = get_user_model()
        cls.other_student = user_model.objects.create_user(
            username="other-admin-student",
            password="test-password",
            role="student",
            approval_status="approved",
            real_name="其他学生",
            phone="13500135000",
        )
        for user in (cls.teacher, cls.admin):
            user.is_staff = True
            user.save(update_fields=("is_staff",))
        permissions = Permission.objects.filter(
            content_type__app_label="tickets",
            codename__in=("view_ticket", "view_tickethistory"),
        )
        cls.teacher.user_permissions.add(*permissions)
        cls.admin.user_permissions.add(*permissions)

    def setUp(self):
        self.own_ticket = self.create_valid_ticket(
            reporter=self.teacher,
            title="教师自己的工单",
        )
        self.other_ticket = self.create_valid_ticket(
            reporter=self.other_student,
            title="其他学生的工单",
        )

    def test_teacher_admin_list_only_contains_own_ticket_and_history(self):
        self.client.force_login(self.teacher)

        ticket_response = self.client.get(reverse("admin:tickets_ticket_changelist"))
        history_response = self.client.get(
            reverse("admin:tickets_tickethistory_changelist")
        )

        self.assertEqual(ticket_response.status_code, 200)
        self.assertContains(ticket_response, self.own_ticket.number)
        self.assertNotContains(ticket_response, self.other_ticket.number)
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, self.own_ticket.number)
        self.assertNotContains(history_response, self.other_ticket.number)

    def test_teacher_cannot_open_other_users_ticket_or_history_directly(self):
        self.client.force_login(self.teacher)
        other_history = self.other_ticket.history.get()

        ticket_response = self.client.get(
            reverse("admin:tickets_ticket_change", args=(self.other_ticket.pk,))
        )
        history_response = self.client.get(
            reverse("admin:tickets_tickethistory_change", args=(other_history.pk,))
        )

        self.assertNotEqual(ticket_response.status_code, 200)
        self.assertNotEqual(history_response.status_code, 200)

    def test_business_admin_can_view_all_tickets_and_history(self):
        self.client.force_login(self.admin)

        ticket_response = self.client.get(reverse("admin:tickets_ticket_changelist"))
        history_response = self.client.get(
            reverse("admin:tickets_tickethistory_changelist")
        )

        self.assertContains(ticket_response, self.own_ticket.number)
        self.assertContains(ticket_response, self.other_ticket.number)
        self.assertContains(history_response, self.own_ticket.number)
        self.assertContains(history_response, self.other_ticket.number)


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
                location_kind=Ticket.LocationKind.ROOM,
                room_number=f"A{index + 1:03d}",
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
