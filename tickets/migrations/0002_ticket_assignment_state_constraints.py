from django.db import migrations, models


def repair_legacy_unassigned_acceptances(apps, schema_editor):
    """Return records produced by the old two-step flow to a recoverable state."""

    Ticket = apps.get_model("tickets", "Ticket")
    Ticket.objects.using(schema_editor.connection.alias).filter(
        status="pending_work",
        current_assignee__isnull=True,
    ).update(status="pending_acceptance")


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            repair_legacy_unassigned_acceptances,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(
                        status__in=(
                            "pending_work",
                            "in_progress",
                            "pending_confirmation",
                            "completed",
                        )
                    )
                    | models.Q(current_assignee__isnull=False)
                ),
                name="ticket_processing_status_has_assignee",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=("pending_acceptance", "rejected"))
                    | models.Q(current_assignee__isnull=True)
                ),
                name="ticket_preassignment_status_has_no_assignee",
            ),
        ),
    ]
