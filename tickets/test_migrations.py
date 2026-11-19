from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from locations.models import Building, FaultCategory

from .models import Ticket


class TicketAssignmentConstraintMigrationTests(TransactionTestCase):
    migrate_from = ("tickets", "0001_initial")
    migrate_to = ("tickets", "0002_ticket_assignment_state_constraints")

    def test_legacy_accepted_ticket_without_assignee_is_recoverable(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])

        reporter = get_user_model().objects.create_user(
            username="legacy-ticket-reporter",
            password="test-password",
            role="student",
            approval_status="approved",
        )
        building = Building.objects.create(
            name="旧数据迁移测试楼",
            building_type="teach",
        )
        category = FaultCategory.objects.create(name="旧数据迁移测试类别")
        ticket = Ticket.objects.create(
            number="CS202611140001",
            reporter=reporter,
            building=building,
            fault_category=category,
            title="旧版已受理但未分派工单",
            description="验证升级时不会因新增约束而失败。",
            location_kind=Ticket.LocationKind.ROOM,
            location_detail="A101",
            contact_name_snapshot="迁移测试用户",
            contact_phone_snapshot="13800138000",
            reporter_role_snapshot="student",
            building_name_snapshot=building.name,
            building_type_snapshot=building.building_type,
            category_name_snapshot=category.name,
            category_teacher_only_snapshot=False,
            status=Ticket.Status.PENDING_WORK,
            current_assignee=None,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.PENDING_ACCEPTANCE)
        self.assertIsNone(ticket.current_assignee)
