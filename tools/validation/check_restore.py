"""Verify recovered business relations and private-file authorization in a fresh process."""
import hashlib
import json
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tools.validation.settings")
django.setup()

from django.conf import settings
from django.test import Client
from accounts.models import MaintenanceMaterial, MaintenanceReview, User
from tickets.models import Ticket, TicketHistory


def check():
    materials = list(MaintenanceMaterial.objects.exclude(storage_name="").filter(disposed_at__isnull=True))
    for material in materials:
        path = settings.PRIVATE_DOCUMENT_ROOT / material.storage_name
        assert path.is_file(), "Recovered private attachment is missing"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == material.sha256
    admin = User.objects.filter(role="admin", is_active=True, approval_status="approved").first()
    student = User.objects.filter(role="student", is_active=True, approval_status="approved").first()
    client = Client(HTTP_HOST="localhost")
    client.force_login(admin)
    assert client.get("/accounts/maintenance-reviews/").status_code == 200
    ticket = Ticket.objects.first()
    assert client.get(f"/tickets/{ticket.pk}/").status_code == 200
    response = client.get(f"/accounts/materials/{materials[0].pk}/")
    assert response.status_code == 200
    assert b"".join(response.streaming_content)
    response.close()
    client.force_login(student)
    assert client.get(f"/accounts/materials/{materials[0].pk}/").status_code == 403
    client.logout()
    assert client.get(f"/accounts/materials/{materials[0].pk}/").status_code == 302
    print(json.dumps({"users": User.objects.count(), "tickets": Ticket.objects.count(),
        "history": TicketHistory.objects.count(), "reviews": MaintenanceReview.objects.count(),
        "material_files_verified": len(materials), "pages": "passed", "private_permissions": "passed"}))


if __name__ == "__main__":
    check()
