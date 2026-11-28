"""Create synthetic validation identities, real materials and 200 reportable tickets."""
import json
import os
from pathlib import Path
import secrets

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tools.validation.settings")
django.setup()

from django.conf import settings
from django.core.management import call_command
from accounts.models import MaintenanceApplication, MaintenanceMaterial, User
from core.private_files import private_directory
from locations.models import Building, FaultCategory
from tickets.services import accept_and_assign_ticket, create_ticket


def prepare():
    root = private_directory(settings.VALIDATION_ROOT)
    if Path(settings.DATABASES["default"]["NAME"]).exists():
        raise ValueError("Validation directory already contains a database")
    call_command("migrate", interactive=False, verbosity=0)
    password = secrets.token_urlsafe(24)
    users = []
    for index in range(20):
        role = "student" if index < 10 else "maintenance" if index < 15 else "admin"
        user = User.objects.create_user(username=f"validation-{index}", password=password,
            role=role, approval_status="approved", phone=f"1390000{index:04d}", real_name=f"验收用户{index}")
        users.append(user)
        if role == "maintenance":
            app = MaintenanceApplication.objects.create(worker=user, status="approved", version=1)
            # Real parsable attachments, retained across backup and restore.
            from PIL import Image
            settings.PRIVATE_DOCUMENT_ROOT.mkdir(exist_ok=True)
            for category in ("employment", "qualification"):
                name = f"{user.pk}-{category}.png"
                path = settings.PRIVATE_DOCUMENT_ROOT / name
                Image.new("RGB", (16, 16), "white").save(path)
                import hashlib
                MaintenanceMaterial.objects.create(application=app, category=category,
                    name="电工资质", direction="水电", storage_name=name, original_name=name,
                    content_type="image/png", size=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    building = Building.objects.create(name="验收教学楼", building_type="teach")
    category = FaultCategory.objects.create(name="验收灯具")
    tickets = []
    for index in range(200):
        ticket = create_ticket(reporter=users[index % 10], building=building, fault_category=category,
            title=f"验收工单{index}", description="灯具无法点亮", location_kind="room", room_number="A101")
        ticket = accept_and_assign_ticket(ticket=ticket, actor=users[15], worker=users[10 + index % 5])
        tickets.append({"id": ticket.pk, "student": index % 10, "worker": 10 + index % 5})
    payload = {"password": password, "users": [{"username": u.username, "role": u.role, "id": u.pk} for u in users],
               "tickets": tickets, "building": building.pk, "category": category.pk}
    (root / "fixture.json").write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps({"root": str(root), "users": len(users), "tickets": len(tickets)}, ensure_ascii=False))


if __name__ == "__main__":
    prepare()
