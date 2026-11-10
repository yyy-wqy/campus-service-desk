from django import forms
from django.core.exceptions import ValidationError

from locations.models import Building, FaultCategory

from .models import Ticket
from .services import create_ticket, validate_submission_scope


class TicketCreateForm(forms.Form):
    building = forms.ModelChoiceField(
        label="楼栋",
        queryset=Building.objects.none(),
        empty_label="请选择楼栋",
    )
    fault_category = forms.ModelChoiceField(
        label="故障类别",
        queryset=FaultCategory.objects.none(),
        empty_label="请选择故障类别",
    )
    title = forms.CharField(label="故障标题", max_length=120)
    description = forms.CharField(
        label="故障描述",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    location_kind = forms.ChoiceField(
        label="位置类型",
        choices=Ticket.LocationKind.choices,
    )
    location_detail = forms.CharField(label="房间或公共区域", max_length=120)
    preferred_visit_time = forms.CharField(
        label="方便上门时间",
        max_length=120,
        required=False,
    )

    def __init__(self, *args, reporter, **kwargs):
        super().__init__(*args, **kwargs)
        self.reporter = reporter
        buildings = Building.objects.filter(is_active=True)
        categories = FaultCategory.objects.filter(is_active=True)
        if reporter.role == "teacher":
            buildings = buildings.filter(building_type="teach")
        elif reporter.role == "student":
            categories = categories.filter(is_teacher_only=False)
        self.fields["building"].queryset = buildings.order_by("name")
        self.fields["fault_category"].queryset = categories.order_by("name")

    def clean(self):
        cleaned = super().clean()
        building = cleaned.get("building")
        category = cleaned.get("fault_category")
        if building and category:
            try:
                validate_submission_scope(self.reporter, building, category)
            except ValidationError as exc:
                self.add_service_error(exc)
        return cleaned

    def add_service_error(self, error):
        """Map service validation fields without exposing internal model fields."""

        if hasattr(error, "error_dict"):
            for source_field, errors in error.error_dict.items():
                target = source_field if source_field in self.fields else None
                for item in errors:
                    self.add_error(target, item)
            return
        self.add_error(None, error)

    def save(self):
        if not self.is_valid():
            raise ValueError("只能保存已通过校验的工单表单。")
        return create_ticket(
            reporter=self.reporter,
            building=self.cleaned_data["building"],
            fault_category=self.cleaned_data["fault_category"],
            title=self.cleaned_data["title"],
            description=self.cleaned_data["description"],
            location_kind=self.cleaned_data["location_kind"],
            location_detail=self.cleaned_data["location_detail"],
            preferred_visit_time=self.cleaned_data["preferred_visit_time"],
        )
