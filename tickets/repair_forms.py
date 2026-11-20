from django import forms

from .models import RepairConfirmation


class RepairVersionForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class RepairProgressForm(RepairVersionForm):
    content = forms.CharField(
        label="维修进度", max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class RepairResultForm(RepairVersionForm):
    content = forms.CharField(
        label="本轮维修结果", max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="提交后等待报修人确认，不会直接完成工单。",
    )


class RepairConfirmationForm(RepairVersionForm):
    result_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    decision = forms.ChoiceField(
        label="确认结果",
        choices=[("", "请选择确认结果")] + list(RepairConfirmation.Decision.choices),
    )
    reason = forms.CharField(
        label="未解决原因", required=False, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="选择未解决时必须填写原因。",
    )

    def clean(self):
        data = super().clean()
        if data.get("decision") == "unresolved" and not data.get("reason"):
            self.add_error("reason", "未解决时必须填写原因。")
        return data


REPAIR_FORMS = {
    "start": RepairVersionForm,
    "progress": RepairProgressForm,
    "result": RepairResultForm,
    "confirm": RepairConfirmationForm,
}
