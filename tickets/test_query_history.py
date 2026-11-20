from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import MaintenanceApplication, User
from locations.models import Building, FaultCategory

from .models import Ticket, TicketHistory
from .services import create_ticket


class TicketQueryHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        roles = {
            "student_a": "student",
            "student_b": "student",
            "teacher": "teacher",
            "worker_a": "maintenance",
            "worker_b": "maintenance",
            "manager": "admin",
        }
        for index, (name, role) in enumerate(roles.items(), start=1):
            cls.users[name] = User.objects.create_user(
                username=name,
                real_name=name,
                role=role,
                approval_status="approved",
                phone=f"1390000000{index}",
            )
            if role == User.Role.MAINTENANCE:
                MaintenanceApplication.objects.create(
                    worker=cls.users[name],
                    status=MaintenanceApplication.Status.APPROVED,
                )

        building = Building.objects.create(
            name="查询测试教学楼",
            building_type="teach",
        )
        category = FaultCategory.objects.create(name="查询测试类别")

        def make_ticket(reporter, title):
            return create_ticket(
                reporter=cls.users[reporter],
                building=building,
                fault_category=category,
                title=title,
                description="原始故障描述，不应被历史说明覆盖。",
                location_kind="room",
                room_number="A101",
            )

        cls.first = make_ticket("student_a", "学生A灯具故障")
        cls.second = make_ticket("student_a", "学生A门锁故障")
        cls.other = make_ticket("student_b", "学生B窗户故障")
        cls.teacher_ticket = make_ticket("teacher", "教师设备故障")

        Ticket.objects.filter(pk=cls.first.pk).update(
            priority="urgent",
            cancellation_pending=True,
        )
        Ticket.objects.filter(pk=cls.second.pk).update(
            status="pending_work",
            current_assignee=cls.users["worker_a"],
        )
        Ticket.objects.filter(pk=cls.other.pk).update(
            status="pending_work",
            current_assignee=cls.users["worker_b"],
        )

    def list_ids(self, response):
        self.assertEqual(response.status_code, 200)
        return {ticket.pk for ticket in response.context["tickets"]}

    def test_each_role_sees_only_its_own_scope_on_all_list_entries(self):
        expected = {
            "student_a": {self.first.pk, self.second.pk},
            "student_b": {self.other.pk},
            "teacher": {self.teacher_ticket.pk},
            "worker_a": {self.second.pk},
            "worker_b": {self.other.pk},
            "manager": {
                self.first.pk,
                self.second.pk,
                self.other.pk,
                self.teacher_ticket.pk,
            },
        }
        for name, ticket_ids in expected.items():
            self.client.force_login(self.users[name])
            for entry in ("list", "tasks", "manage"):
                with self.subTest(user=name, entry=entry):
                    response = self.client.get(reverse(f"tickets:{entry}"))
                    self.assertEqual(self.list_ids(response), ticket_ids)

    def test_direct_detail_id_cannot_bypass_permissions(self):
        cases = [
            ("student_a", self.other),
            ("student_b", self.first),
            ("teacher", self.first),
            ("worker_a", self.other),
            ("worker_b", self.second),
        ]
        for name, ticket in cases:
            with self.subTest(user=name):
                self.client.force_login(self.users[name])
                response = self.client.get(
                    reverse("tickets:detail", args=[ticket.pk])
                )
                self.assertEqual(response.status_code, 404)

    def test_search_cannot_find_another_reporters_ticket(self):
        self.client.force_login(self.users["student_a"])
        response = self.client.get(
            reverse("tickets:list"),
            {
                "q": self.other.number,
                "reporter_id": self.users["student_b"].pk,
            },
        )
        self.assertEqual(self.list_ids(response), set())

    def test_keyword_and_combined_filters(self):
        self.client.force_login(self.users["manager"])
        cases = [
            ({"q": self.first.number}, {self.first.pk}),
            ({"q": "门锁"}, {self.second.pk}),
            (
                {"status": "pending_work"},
                {self.second.pk, self.other.pk},
            ),
            ({"priority": "urgent"}, {self.first.pk}),
            ({"cancellation": "1"}, {self.first.pk}),
            (
                {
                    "status": "pending_acceptance",
                    "priority": "urgent",
                    "cancellation": "1",
                },
                {self.first.pk},
            ),
            (
                {"cancellation": "0"},
                {self.second.pk, self.other.pk, self.teacher_ticket.pk},
            ),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                response = self.client.get(reverse("tickets:list"), params)
                self.assertEqual(self.list_ids(response), expected)

    def test_invalid_filters_show_errors_and_no_results(self):
        self.client.force_login(self.users["manager"])
        for params in (
            {"status": "fake"},
            {"priority": "fake"},
            {"cancellation": "fake"},
            {"q": "x" * 101},
        ):
            with self.subTest(params=params):
                response = self.client.get(reverse("tickets:list"), params)
                self.assertEqual(self.list_ids(response), set())
                self.assertTrue(response.context["query_form"].errors)

    def test_history_shows_events_in_order_with_actor_and_time(self):
        descriptions = [
            "维修人员首次提交处理结果",
            "报修人退回继续处理",
            "管理员记录取消审核意见",
            "报修人追加现场情况",
        ]
        actors = ["worker_a", "student_a", "manager", "student_a"]
        events = [
            "status_changed",
            "status_changed",
            "cancellation_changed",
            "note_added",
        ]
        records = []
        for description, actor, event_type in zip(
            descriptions, actors, events
        ):
            records.append(
                TicketHistory.objects.create(
                    ticket=self.first,
                    event_type=event_type,
                    actor=self.users[actor],
                    description=description,
                )
            )

        self.client.force_login(self.users["student_a"])
        response = self.client.get(
            reverse("tickets:detail", args=[self.first.pk])
        )
        html = response.content.decode("utf-8")
        positions = []
        for description in descriptions:
            self.assertContains(response, description)
            positions.append(html.index(description))
        self.assertEqual(positions, sorted(positions))

        for record in records:
            self.assertContains(response, record.actor.username)
            self.assertContains(
                response,
                timezone.localtime(record.created_at).isoformat(),
            )
            self.assertContains(
                response,
                timezone.localtime(record.created_at).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            )

        self.assertContains(response, "原始故障描述，不应被历史说明覆盖。")
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, "pending_acceptance")

    def test_cancellation_marker_and_main_status_are_both_visible(self):
        self.client.force_login(self.users["student_a"])
        for url in (
            reverse("tickets:list"),
            reverse("tickets:detail", args=[self.first.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "取消申请待审核")
                self.assertContains(response, "待受理")

    def test_history_text_is_escaped(self):
        TicketHistory.objects.create(
            ticket=self.first,
            event_type="note_added",
            actor=self.users["student_a"],
            description="<script>alert(1)</script>",
        )
        self.client.force_login(self.users["student_a"])
        response = self.client.get(
            reverse("tickets:detail", args=[self.first.pk])
        )
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_real_repair_flow_displays_all_round_transitions(self):
        from .repair_services import (
            confirm_repair_result, start_repair, submit_repair_result,
        )

        ticket = start_repair(
            ticket_id=self.second.pk, actor=self.users["worker_a"],
            version=self.second.repair_version,
        )
        for round_number in (1, 2):
            ticket = submit_repair_result(
                ticket_id=ticket.pk, actor=self.users["worker_a"],
                version=ticket.repair_version,
                content=f"第{round_number}轮维修结果",
            )
            result = ticket.repair_results.get(round_number=round_number)
            ticket = confirm_repair_result(
                ticket_id=ticket.pk, actor=self.users["student_a"],
                version=ticket.repair_version, result_id=result.pk,
                decision="unresolved" if round_number == 1 else "resolved",
                reason="仍有异响" if round_number == 1 else "",
            )

        records = list(ticket.history.filter(event_type="status_changed"))
        self.assertEqual(
            [(record.from_status, record.to_status) for record in records],
            [
                ("pending_work", "in_progress"),
                ("in_progress", "pending_confirmation"),
                ("pending_confirmation", "in_progress"),
                ("in_progress", "pending_confirmation"),
                ("pending_confirmation", "completed"),
            ],
        )
        self.assertEqual(
            [record.actor_id for record in records],
            [self.users[name].pk for name in (
                "worker_a", "worker_a", "student_a", "worker_a", "student_a"
            )],
        )
        self.client.force_login(self.users["student_a"])
        response = self.client.get(reverse("tickets:detail", args=[ticket.pk]))
        for text, count in (
            ("待处理 → 处理中", 1),
            ("处理中 → 待报修人确认", 2),
            ("待报修人确认 → 处理中", 1),
            ("待报修人确认 → 已完成", 1),
        ):
            self.assertContains(
                response, f"<p>状态：{text}</p>", count=count, html=True
            )
        self.assertContains(response, "第1轮维修结果")
        self.assertContains(response, "第2轮维修结果")
        self.assertEqual(ticket.status, Ticket.Status.COMPLETED)

    def test_legacy_history_without_description_still_shows_statuses(self):
        TicketHistory.objects.create(
            ticket=self.second, actor=self.users["worker_a"],
            event_type="status_changed", description="",
            from_status="in_progress", to_status="pending_confirmation",
        )
        self.client.force_login(self.users["student_a"])
        response = self.client.get(
            reverse("tickets:detail", args=[self.second.pk])
        )
        self.assertContains(response, "无补充说明")
        self.assertContains(
            response, "<p>状态：处理中 → 待报修人确认</p>", html=True
        )
