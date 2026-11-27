"""Perform a real snapshot and isolated restore without touching the working DB."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tools.validation.settings")
django.setup()

from django.conf import settings
from django.utils import timezone
from core.backups import create_backup, digest, restore_backup, verify_backup


def drill():
    root = settings.VALIDATION_ROOT
    destination = root.parent / ("run-restored-" + str(time.time_ns()))
    live = Path(settings.DATABASES["default"]["NAME"])
    original_digest = digest(live)
    source = create_backup()
    manifest = verify_backup(source)
    restore_backup(source, destination)
    manifest_verified = verify_backup(destination) == manifest
    env = {**os.environ, "VALIDATION_ROOT": str(destination)}
    result = subprocess.run([sys.executable, "-m", "tools.validation.check_restore"], env=env,
                            check=True, capture_output=True, text=True)
    checks = json.loads(result.stdout)
    assert digest(live) == original_digest, "Source database was modified during recovery drill"
    report = {"time": timezone.now().isoformat(), "operator": os.environ.get("USERNAME", "unknown"),
              "platform": platform.platform(), "backup": str(source), "restored": str(destination),
              "manifest_verified": manifest_verified, "source_database_unchanged": True, **checks}
    (root / "restore-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    drill()
