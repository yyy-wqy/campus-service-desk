from datetime import date, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from .backups import create_backup, restore_backup, sqlite_connection, verify_backup
from .operational_logging import DailyJSONHandler
from .private_files import private_directory


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / "live.sqlite3"
        with sqlite_connection(self.db) as connection:
            connection.execute("CREATE TABLE history (value TEXT)")
            connection.execute("INSERT INTO history VALUES ('business history')")
            connection.execute(
                "CREATE TABLE accounts_maintenancematerial "
                "(storage_name TEXT, size INTEGER, sha256 TEXT, disposed_at TEXT)"
            )
            connection.executemany(
                "INSERT INTO accounts_maintenancematerial VALUES (?, ?, ?, ?)",
                [
                    ("proof.pdf", len(b"private-proof"),
                     hashlib.sha256(b"private-proof").hexdigest(), None),
                    ("disposed.pdf", 12, "unused", "2026-01-01"),
                ],
            )
        self.media, self.private = self.root / "media", self.root / "private"
        self.media.mkdir(); self.private.mkdir()
        (self.private / "proof.pdf").write_bytes(b"private-proof")
        (self.media / "photo.png").write_bytes(b"public-image")
        self.backups = self.root / "backups"
        context = override_settings(
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(self.db)}},
            MEDIA_ROOT=self.media, PRIVATE_DOCUMENT_ROOT=self.private,
            BACKUP_ROOT=self.backups, LOG_ROOT=self.root / "logs",
        )
        context.enable(); self.addCleanup(context.disable)

    def test_snapshot_and_isolated_restore_preserve_database_files_and_live_data(self):
        backup = create_backup()
        manifest = verify_backup(backup)
        self.assertEqual(set(manifest["files"]), {"db.sqlite3", "media/photo.png", "private_documents/proof.pdf"})
        destination = restore_backup(backup, self.root / "restored")
        self.assertEqual((destination / "private_documents/proof.pdf").read_bytes(), b"private-proof")
        with sqlite_connection(destination / "db.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM history").fetchone()[0], "business history")
            connection.execute("DELETE FROM history")
        with sqlite_connection(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            restore_backup(backup, self.root)

    def test_eight_days_keep_seven_distinct_dates_even_with_extra_same_day_runs(self):
        start = date(2026, 1, 1)
        for offset in range(8):
            with patch("core.backups.timezone.localdate", return_value=start + timedelta(days=offset)):
                create_backup()
        for _ in range(2):
            with patch("core.backups.timezone.localdate", return_value=start + timedelta(days=7)):
                create_backup()
        self.assertEqual(sorted(p.name for p in self.backups.iterdir() if p.is_dir()),
                         [(start + timedelta(days=i)).isoformat() for i in range(1, 8)])
        self.assertEqual(len((self.backups / "executions.jsonl").read_text().splitlines()), 10)

    def test_tampered_backup_cannot_restore(self):
        backup = create_backup()
        (backup / "private_documents/proof.pdf").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            restore_backup(backup, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_missing_private_directory_does_not_replace_same_day_backup(self):
        backup = create_backup()
        old_manifest = (backup / "manifest.json").read_bytes()
        self.private.rename(self.root / "temporarily-unavailable")

        with self.assertRaisesMessage(ValueError, "有应保留的私有附件缺失"):
            create_backup()

        self.assertEqual((backup / "manifest.json").read_bytes(), old_manifest)
        self.assertEqual((backup / "private_documents/proof.pdf").read_bytes(), b"private-proof")
        verify_backup(backup)
        self.assertFalse(list(self.backups.glob(".staging-*")))

    def test_individual_missing_or_changed_material_rejects_backup(self):
        proof = self.private / "proof.pdf"
        proof.unlink()
        with self.assertRaisesMessage(ValueError, "有应保留的私有附件缺失"):
            create_backup()
        # Same size as the expected attachment; checksum verification must still fail.
        proof.write_bytes(b"changed-proof")
        with self.assertRaisesMessage(ValueError, "私有附件与数据库记录不一致"):
            create_backup()

    def test_failed_copy_keeps_previous_backup_and_records_failure(self):
        backup = create_backup()
        with patch("core.backups.copy_tree", side_effect=OSError("sensitive details")):
            with self.assertRaises(OSError):
                create_backup()
        verify_backup(backup)
        events = [json.loads(line) for line in (self.backups / "executions.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "backup_failed")
        self.assertNotIn("sensitive details", (self.backups / "executions.jsonl").read_text())
        self.assertFalse(list(self.backups.glob(".staging-*")))

    def test_backup_inside_public_uploads_is_rejected(self):
        with override_settings(BACKUP_ROOT=self.media / "backups"):
            with self.assertRaises(ValueError):
                create_backup()


class LogRetentionTests(SimpleTestCase):
    def test_90_day_boundary_and_sensitive_fields(self):
        with TemporaryDirectory() as name:
            root = Path(name) / "logs"
            root.mkdir()
            today = date(2026, 11, 26)
            kept = root / f"security-{today - timedelta(days=90)}.jsonl"
            expired = root / f"application-{today - timedelta(days=91)}.jsonl"
            history = root / "business-history.json"
            for item in (kept, expired, history): item.write_text("retained")
            handler = DailyJSONHandler("application")
            record = logging.LogRecord("django.request", logging.ERROR, "", 0,
                                       "secret-password /accounts/reset/user/private-token/?secret=123", (), None)
            record.request = {"POST": "private-upload-contents"}
            with override_settings(LOG_ROOT=root), patch("core.operational_logging.timezone.localdate", return_value=today):
                handler.emit(record)
            self.assertTrue(kept.exists())
            self.assertFalse(expired.exists())
            self.assertEqual(history.read_text(), "retained")
            payload = (root / f"application-{today}.jsonl").read_text()
            for secret in ("secret-password", "private-token", "private-upload-contents", "secret=123"):
                self.assertNotIn(secret, payload)
            self.assertEqual(json.loads(payload)["level"], "ERROR")


@skipUnless(os.name == "nt", "Windows DACL regression")
class PrivateWindowsDirectoryTests(SimpleTestCase):
    def test_existing_directory_and_file_lose_explicit_everyone_permissions(self):
        from .windows_acl import SecurityAPI

        with TemporaryDirectory() as folder:
            root = Path(folder) / "验收目录"
            root.mkdir()
            file = root / "backup.txt"
            file.write_text("synthetic", encoding="utf-8")
            api = SecurityAPI()
            self.assertRegex(api.user_sid(), r"^S-1-\d+(?:-\d+)+$")
            for path, kind in ((root, "OICI"), (file, "")):
                api.replace_dacl(
                    path, f"D:P(A;{kind};FA;;;{api.user_sid()})(A;{kind};FA;;;SY)(A;{kind};FR;;;WD)"
                )
                self.assertIn(";;;WD)", api.file_dacl(path))

            with patch("subprocess.check_output", side_effect=AssertionError("No account-name decoding")):
                self.assertEqual(private_directory(root), root.resolve())

            for path in (root, file):
                dacl = api.file_dacl(path)
                self.assertTrue(dacl.startswith("D:P"))
                self.assertNotIn(";;;WD)", dacl)
                self.assertIn(";;;SY)", dacl)
            self.assertEqual(file.read_text(encoding="utf-8"), "synthetic")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SecurityLogTests(TestCase):
    def test_failed_login_lock_and_forbidden_request_are_queryable_without_credentials(self):
        with TemporaryDirectory() as name, override_settings(LOG_ROOT=Path(name) / "logs"):
            user = User.objects.create_user(username="log-test", password="valid-password", role="student", approval_status="approved")
            for _ in range(5):
                self.client.post("/accounts/login/", {"username": user.username, "password": "wrong-private-password"})
            self.client.force_login(user)
            self.assertEqual(self.client.get("/tickets/cancellations/").status_code, 403)
            files = list((Path(name) / "logs").glob("security-*.jsonl"))
            content = "".join(p.read_text(encoding="utf-8") for p in files)
            events = [json.loads(line)["event"] for line in content.splitlines()]
            for event in ("login_failed", "login_locked", "access_denied"):
                self.assertIn(event, events)
            for secret in ("valid-password", "wrong-private-password", user.username):
                self.assertNotIn(secret, content)
