from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0002_ticket_cancellation"),
        ("tickets", "0003_repair_workflow_records"),
    ]
    operations = []
