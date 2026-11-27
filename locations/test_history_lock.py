from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase

from accounts.models import User
from .housing import LOCK_RETRIES
from .models import HousingChangeLog


class HousingHistoryReadLockTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="history-lock", role="student", approval_status="approved")
        self.client.force_login(self.student)

    def test_history_read_retries_a_transient_lock(self):
        queryset = HousingChangeLog.objects.filter(student=self.student)
        with patch.object(HousingChangeLog.objects, "filter", side_effect=[OperationalError("database table is locked"), queryset]) as read, patch("locations.housing.time.sleep"):
            response = self.client.get("/locations/housing/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(read.call_count, 2)

    def test_exhausted_history_read_lock_shows_retry_message(self):
        with patch.object(HousingChangeLog.objects, "filter", side_effect=OperationalError("database table is locked")) as read, patch("locations.housing.time.sleep"):
            response = self.client.get("/locations/housing/")
        self.assertContains(response, "请稍后刷新重试")
        self.assertEqual(read.call_count, LOCK_RETRIES)
