import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import OperationalError, close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from . import test_query_history as fixtures
from .models import RepairConfirmation, RepairResult
from .repair_services import start_repair, submit_repair_result


class ConcurrentRepairTests(TransactionTestCase):
    def setUp(self):
        fixtures.TicketQueryHistoryTests.setUpTestData.__func__(type(self))
        self.ticket = start_repair(
            ticket_id=self.second.pk,
            actor=self.users["worker_a"],
            version=0,
        )

    def client_for(self, name):
        client = Client()
        client.force_login(self.users[name])
        return client

    def url(self, action):
        return reverse(
            "tickets:repair_action",
            args=[self.ticket.pk, action],
        )

    def race(self, action, user, payload):
        clients = [self.client_for(user), self.client_for(user)]
        barrier = threading.Barrier(2)
        url = self.url(action)

        def send(client):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return client.post(url, payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return sorted(pool.map(send, clients))

    def test_same_result_submitted_concurrently_only_once(self):
        statuses = self.race("result", "worker_a", {
            "version": self.ticket.repair_version,
            "content": "Concurrent result",
        })
        self.assertEqual(statuses, [302, 409])
        self.assertEqual(RepairResult.objects.count(), 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "pending_confirmation")
        self.assertEqual(self.ticket.repair_version, 2)

    def test_same_result_confirmed_concurrently_only_once(self):
        self.ticket = submit_repair_result(
            ticket_id=self.ticket.pk,
            actor=self.users["worker_a"],
            version=self.ticket.repair_version,
            content="Result awaiting confirmation",
        )
        result = self.ticket.repair_results.get()
        statuses = self.race("confirm", "student_a", {
            "version": self.ticket.repair_version,
            "result_id": result.pk,
            "decision": "resolved",
            "reason": "",
        })
        self.assertEqual(statuses, [302, 409])
        self.assertEqual(RepairConfirmation.objects.count(), 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "completed")

    def test_initial_page_read_survives_write_lock(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-specific lock regression")

        client = self.client_for("worker_a")
        attempted = threading.Event()
        other = sqlite3.connect(
            str(connection.settings_dict["NAME"]), uri=True
        )
        other.execute("BEGIN IMMEDIATE")
        other.execute(
            "UPDATE tickets_ticket SET updated_at=updated_at WHERE id=?",
            (self.ticket.pk,),
        )
        url = self.url("result")
        payload = {
            "version": self.ticket.repair_version,
            "content": "Result after releasing lock",
        }

        def send():
            close_old_connections()

            def observe(execute, sql, params, many, context):
                if (
                    sql.lstrip().upper().startswith("SELECT")
                    and '"tickets_ticket"' in sql
                ):
                    attempted.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(observe):
                    return client.post(url, payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(send)
            try:
                self.assertTrue(attempted.wait(timeout=5))
                time.sleep(0.05)
            finally:
                other.rollback()
                other.close()
            self.assertEqual(future.result(timeout=10), 302)

        self.assertEqual(RepairResult.objects.count(), 1)

    def test_exhausted_read_lock_returns_retry_feedback(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-specific lock feedback")

        client = self.client_for("worker_a")
        with patch(
            "tickets.repair_views.get_object_or_404",
            side_effect=OperationalError("database is locked"),
        ) as lookup, patch("tickets.repair_http.time.sleep"):
            response = client.post(self.url("result"), {
                "version": self.ticket.repair_version,
                "content": "Unsubmitted result",
            })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(lookup.call_count, 4)
        self.assertFalse(RepairResult.objects.exists())
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "in_progress")
        self.assertEqual(self.ticket.repair_version, 1)
