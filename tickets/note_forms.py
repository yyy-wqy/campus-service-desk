from django import forms

from .note_services import MAX_NOTE_LENGTH


class TicketNoteForm(forms.Form):
    text = forms.CharField(
        label="追加说明",
        max_length=MAX_NOTE_LENGTH,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "补充现场情况或更正信息，最多2000字。",
            }
        ),
        help_text="说明只追加到历史，不修改原始报修内容或工单状态。",
    )
