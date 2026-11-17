from django import forms

from .models import Ticket


class TicketQueryForm(forms.Form):
    q = forms.CharField(
        label="关键词",
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={"placeholder": "工单编号、标题、楼栋或位置"}
        ),
    )
    status = forms.ChoiceField(
        label="工单状态",
        required=False,
        choices=[("", "全部状态")] + list(Ticket.Status.choices),
    )
    priority = forms.ChoiceField(
        label="优先级",
        required=False,
        choices=[("", "全部优先级")] + list(Ticket.Priority.choices),
    )
    cancellation = forms.ChoiceField(
        label="取消申请",
        required=False,
        choices=[
            ("", "全部"),
            ("1", "取消待审"),
            ("0", "无待审取消申请"),
        ],
    )
