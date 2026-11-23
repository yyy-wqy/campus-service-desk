from .models import Ticket
from .permissions import can_confirm_ticket, can_process_ticket
from .repair_forms import REPAIR_FORMS


def repair_context(user, ticket, *, bound_form=None, action=None):
    results = list(
        ticket.repair_results.select_related("worker", "confirmation__reporter")
        .order_by("-round_number", "-pk")
    )
    latest = results[0] if results else None
    unfrozen = not ticket.cancellation_pending
    worker_allowed = can_process_ticket(user, ticket)
    reporter_allowed = can_confirm_ticket(user, ticket)
    initial = {"version": ticket.repair_version}

    context = {
        "repair_results": results,
        "repair_progress": ticket.repair_progress.select_related("worker")
        .order_by("round_number", "created_at", "pk"),
        "repair_error_form": bound_form,
        "can_start_repair": (
            worker_allowed and unfrozen
            and ticket.status == Ticket.Status.PENDING_WORK
        ),
        "can_add_repair_progress": (
            worker_allowed and unfrozen
            and ticket.status == Ticket.Status.IN_PROGRESS
        ),
        "can_submit_repair_result": (
            worker_allowed and unfrozen
            and ticket.status == Ticket.Status.IN_PROGRESS
        ),
        "can_confirm_repair_result": (
            reporter_allowed and unfrozen
            and ticket.status == Ticket.Status.PENDING_CONFIRMATION
            and latest is not None
            and getattr(latest, "confirmation", None) is None
        ),
    }

    for key, form_class in REPAIR_FORMS.items():
        values = dict(initial)
        if key == "confirm" and latest is not None:
            values["result_id"] = latest.pk
        context[f"repair_{key}_form"] = (
            bound_form if key == action and bound_form is not None
            else form_class(initial=values, auto_id=f"repair_{key}_%s")
        )
    return context
