"""Coordinated SQLite/files snapshots and non-overwriting isolated restores."""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from django.conf import settings
from django.utils import timezone

from accounts.models import MaintenanceMaterial

from .private_files import outside_uploads, private_directory


@contextmanager
def sqlite_connection(*args, **kwargs):
    connection = sqlite3.connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(root, event, **fields):
    with (root / "executions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": timezone.now().isoformat(), "event": event, **fields}) + "\n")


@contextmanager
def backup_lock(root):
    descriptor = os.open(root / ".backup.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        (root / ".backup.lock").unlink()


def copy_tree(source, destination):
    source = Path(source)
    if not source.exists():
        destination.mkdir()
        return
    if source.is_symlink():
        raise ValueError("附件目录不能是符号链接。")
    destination.mkdir()
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError("附件中存在符号链接，备份已中止。")
        target = destination / item.relative_to(source)
        if item.is_dir():
            target.mkdir(exist_ok=True)
        else:
            shutil.copyfile(item, target)


def verify_backup(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or set(manifest.get("files", {})) == set():
        raise ValueError("备份清单格式无效。")
    expected = set(manifest["files"])
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != "manifest.json"}
    if actual != expected:
        raise ValueError("备份文件与清单不符。")
    for name, metadata in manifest["files"].items():
        path = folder / name
        if not path.resolve().is_relative_to(folder) or any(p.is_symlink() for p in [path, *path.parents] if p != folder.parent):
            raise ValueError("备份包含越界路径或符号链接。")
        if path.stat().st_size != metadata["bytes"] or digest(path) != metadata["sha256"]:
            raise ValueError("备份校验失败。")
    with sqlite_connection(f"{(folder / 'db.sqlite3').as_uri()}?mode=ro", uri=True) as database:
        if database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 完整性检查失败。")
        if database.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite 外键检查失败。")
        verify_private_materials(database, manifest["files"])
        verify_ticket_photos(database, manifest["files"])
    return manifest


def verify_private_materials(database, files):
    # Query the snapshot, not the live ORM connection. Retired files are still
    # retained for review history until they are formally disposed of.
    table = MaintenanceMaterial._meta.db_table
    for name, size, sha256 in database.execute(
        f'SELECT storage_name, size, sha256 FROM "{table}" WHERE disposed_at IS NULL'
    ):
        stored_file = files.get(f"private_documents/{name}")
        if stored_file is None:
            raise ValueError("有应保留的私有附件缺失。")
        if stored_file["bytes"] != size or stored_file["sha256"] != sha256:
            raise ValueError("私有附件与数据库记录不一致。")


def verify_ticket_photos(database, files):
    # Pre-photo backups remain restorable; new snapshots verify every retained photo.
    if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_ticketphoto'").fetchone():
        return
    for name, size, checksum in database.execute(
        "SELECT storage_name, size, sha256 FROM tickets_ticketphoto WHERE disposed_at IS NULL"
    ):
        entry = files.get(f"private_documents/ticket_photos/{name}")
        if entry is None or entry["bytes"] != size or entry["sha256"] != checksum:
            raise ValueError("应保留的工单照片缺失或与数据库不一致。")


def remove_owned_tree(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("拒绝清理备份目录之外的路径。")
    shutil.rmtree(path)


def create_backup():
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise ValueError("本工具仅支持 SQLite。")
    sources = [settings.MEDIA_ROOT, settings.PRIVATE_DOCUMENT_ROOT]
    root = outside_uploads(settings.BACKUP_ROOT, sources)
    database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()
    if not database_path.is_file() or database_path.is_relative_to(root):
        raise ValueError("源数据库不存在或位于备份目录内。")
    private_directory(root)
    day = timezone.localdate().isoformat()
    staging = root / (".staging-" + uuid.uuid4().hex)
    previous = root / (".previous-" + uuid.uuid4().hex)
    try:
        with backup_lock(root):
            staging.mkdir()
            # A separate writer reservation coordinates all application writers,
            # including material upload/disposal. The reader sees committed data.
            with sqlite_connection(f"{database_path.as_uri()}?mode=rw", uri=True, timeout=30) as gate:
                gate.execute("BEGIN IMMEDIATE")
                try:
                    with sqlite_connection(f"{database_path.as_uri()}?mode=ro", uri=True) as source:
                        with sqlite_connection(staging / "db.sqlite3") as target:
                            source.backup(target)
                    copy_tree(settings.MEDIA_ROOT, staging / "media")
                    copy_tree(settings.PRIVATE_DOCUMENT_ROOT, staging / "private_documents")
                finally:
                    gate.rollback()
            files = {
                p.relative_to(staging).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)}
                for p in staging.rglob("*") if p.is_file()
            }
            (staging / "manifest.json").write_text(json.dumps({
                "format": 1, "day": day, "created_at": timezone.now().isoformat(), "files": files,
            }, indent=2), encoding="utf-8")
            verify_backup(staging)
            daily = root / day
            if daily.exists():
                daily.rename(previous)
            try:
                staging.rename(daily)
            except Exception:
                if previous.exists():
                    previous.rename(daily)
                raise
            if previous.exists():
                remove_owned_tree(previous, root)
            # Count distinct successful calendar days, not executions.
            days = sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
            for expired in days[:-7]:
                date.fromisoformat(expired.name)
                remove_owned_tree(expired, root)
            audit(root, "backup_succeeded", day=day, files=len(files))
            return daily
    except Exception as error:
        if staging.exists():
            remove_owned_tree(staging, root)
        audit(root, "backup_failed", error_type=type(error).__name__)
        logging.getLogger("operations").error("backup_failed", extra={"event": "backup_failed"})
        raise


def restore_backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    verify_backup(source)
    if destination.exists():
        raise ValueError("恢复目标必须是尚不存在的独立目录，禁止覆盖现有数据。")
    outside_uploads(destination, [settings.MEDIA_ROOT, settings.PRIVATE_DOCUMENT_ROOT, settings.BACKUP_ROOT])
    private_directory(destination)
    try:
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                copy_tree(item, target)
            else:
                shutil.copyfile(item, target)
        verify_backup(destination)
        # Execution evidence lives outside the restored manifest-controlled tree.
        audit(source.parent, "restore_succeeded", backup_day=source.name, destination=str(destination))
        return destination
    except Exception as error:
        audit(source.parent, "restore_failed", error_type=type(error).__name__)
        raise
