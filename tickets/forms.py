from django import forms
from django.core.exceptions import ValidationError

from locations.models import Building, FaultCategory, PublicAreaOption
from locations.scope import (
    active_public_areas,
    reportable_buildings,
    reportable_categories,
    validate_repair_scope,
)

from .models import Ticket
from .services import create_ticket


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
    room_number = forms.CharField(
        label="房间号",
        max_length=20,
        required=False,
        help_text="位置类型选择“房间”时填写。",
    )
    public_area = forms.ModelChoiceField(
        label="公共区域",
        queryset=PublicAreaOption.objects.none(),
        empty_label="请选择公共区域",
        required=False,
        help_text="位置类型选择“公共区域”时填写。",
    )
    public_area_other = forms.CharField(
        label="其他公共区域",
        max_length=50,
        required=False,
        help_text="仅在公共区域选择“其他”时填写。",
    )
    preferred_visit_time = forms.CharField(
        label="方便上门时间",
        max_length=120,
        required=False,
    )

    def __init__(self, *args, reporter, **kwargs):
        super().__init__(*args, **kwargs)
        self.reporter = reporter
        self.fields["building"].queryset = reportable_buildings(reporter).order_by(
            "name"
        )
        self.fields["fault_category"].queryset = reportable_categories(
            reporter
        ).order_by("name")
        self.fields["public_area"].queryset = active_public_areas()

    def clean(self):
        cleaned = super().clean()
        building = cleaned.get("building")
        category = cleaned.get("fault_category")
        if building and category:
            try:
                validate_repair_scope(
                    reporter=self.reporter,
                    building=building,
                    fault_category=category,
                    location_kind=cleaned.get("location_kind"),
                    room_number=cleaned.get("room_number", ""),
                    public_area=cleaned.get("public_area"),
                    public_area_other=cleaned.get("public_area_other", ""),
                )
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
            room_number=self.cleaned_data["room_number"],
            public_area=self.cleaned_data["public_area"],
            public_area_other=self.cleaned_data["public_area_other"],
            preferred_visit_time=self.cleaned_data["preferred_visit_time"],
        )
