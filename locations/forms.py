from django import forms

from accounts.models import User

from .models import Building, FaultCategory, PublicAreaOption


def _strip_name(value):
    name = (value or "").strip()
    if not name:
        raise forms.ValidationError("请填写名称。")
    return name


class HousingForm(forms.Form):
    building = forms.ModelChoiceField(
        label="住宿楼栋",
        queryset=Building.objects.none(),
        empty_label="请选择宿舍楼",
        help_text="只能选择启用中的宿舍楼；每 30 天可自行更换 1 次。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].queryset = Building.objects.filter(
            building_type="dorm", is_active=True
        ).order_by("name")


class BuildingForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ("name", "building_type", "is_active")
        labels = {"name": "楼栋名称", "building_type": "建筑类型", "is_active": "启用"}
        widgets = {"name": forms.TextInput(attrs={"maxlength": 100})}

    def clean_name(self):
        name = _strip_name(self.cleaned_data.get("name"))
        duplicates = Building.objects.filter(name=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("已存在同名楼栋。")
        return name


class FaultCategoryForm(forms.ModelForm):
    class Meta:
        model = FaultCategory
        fields = ("name", "is_teacher_only", "is_active")
        labels = {
            "name": "分类名称",
            "is_teacher_only": "教师专用设备",
            "is_active": "启用",
        }

    def clean_name(self):
        name = _strip_name(self.cleaned_data.get("name"))
        duplicates = FaultCategory.objects.filter(name=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("已存在同名分类。")
        return name


class PublicAreaOptionForm(forms.ModelForm):
    class Meta:
        model = PublicAreaOption
        fields = ("name", "sort_order", "is_other", "is_active")
        labels = {
            "name": "名称",
            "sort_order": "排序",
            "is_other": "作为“其他”选项",
            "is_active": "启用",
        }

    def clean_name(self):
        name = _strip_name(self.cleaned_data.get("name"))
        duplicates = PublicAreaOption.objects.filter(name=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("已存在同名公共区域。")
        return name

    def clean(self):
        data = super().clean()
        if data.get("is_other"):
            others = PublicAreaOption.objects.filter(is_other=True)
            if self.instance.pk:
                others = others.exclude(pk=self.instance.pk)
            if others.exists():
                self.add_error("is_other", "只能有一个“其他”选项。")
        return data


class AdminHousingForm(forms.Form):
    """管理员代学生修改住宿楼栋（写入时由服务层记录操作人）。"""

    student = forms.ModelChoiceField(
        label="学生",
        queryset=None,
        empty_label="请选择学生",
    )
    building = forms.ModelChoiceField(
        label="住宿楼栋",
        queryset=None,
        empty_label="请选择宿舍楼",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = User.objects.filter(
            role=User.Role.STUDENT
        ).order_by("username")
        self.fields["building"].queryset = Building.objects.filter(
            building_type="dorm", is_active=True
        ).order_by("name")
