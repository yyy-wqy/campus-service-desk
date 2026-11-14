from django import forms
from django.db.models import Prefetch

from accounts.models import MaintenanceApplication, MaintenanceMaterial, User

from .models import Ticket


def assignable_workers():
    qualifications = MaintenanceMaterial.objects.filter(
        category=MaintenanceMaterial.Category.QUALIFICATION,
        active=True,
        disposed_at__isnull=True,
    ).order_by("name", "pk")
    return (
        User.objects.filter(
            role=User.Role.MAINTENANCE,
            is_active=True,
            approval_status=User.ApprovalStatus.APPROVED,
            qualification_application__status=MaintenanceApplication.Status.APPROVED,
        )
        .select_related("qualification_application")
        .prefetch_related(
            Prefetch(
                "qualification_application__materials",
                queryset=qualifications,
                to_attr="assignment_qualifications",
            )
        )
        .order_by("real_name", "username")
    )


class WorkerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, worker):
        materials = worker.qualification_application.assignment_qualifications
        directions = sorted({item.direction for item in materials if item.direction})
        qualifications = [item.name for item in materials if item.name]
        name = worker.real_name or worker.username
        return (
            f"{name}（{worker.username}）｜专业方向："
            f"{('、'.join(directions) or '未填写')}｜已通过资质："
            f"{('、'.join(qualifications) or '未填写')}"
        )


class TicketRejectForm(forms.Form):
    reason = forms.CharField(
        label="不予受理原因",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class TicketPriorityForm(forms.Form):
    priority = forms.ChoiceField(label="工单优先级", choices=Ticket.Priority.choices)


class TicketAssignmentForm(forms.Form):
    worker = WorkerChoiceField(
        label="维修人员",
        queryset=User.objects.none(),
        empty_label="请选择维修人员",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["worker"].queryset = assignable_workers()
