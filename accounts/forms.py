from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import User


class RegistrationForm(UserCreationForm):
    PHONE_ERROR = "请输入 11 位数字手机号"
    STUDENT_NUMBER_ERROR = "请输入 12 位数字学号"

    role = forms.ChoiceField(
        label="注册身份",
        choices=[choice for choice in User.Role.choices if choice[0] != User.Role.ADMIN],
    )
    real_name = forms.CharField(label="姓名", max_length=50)
    phone = forms.RegexField(
        label="手机号", regex=r"\A[0-9]{11}\Z",
        strip=False,
        error_messages=dict.fromkeys(
            ("required", "invalid", "min_length", "max_length"), PHONE_ERROR
        ),
        help_text="手机号须为 11 位半角数字（0–9）。",
        widget=forms.TextInput(attrs={
            "type": "tel", "inputmode": "numeric", "autocomplete": "tel",
            "pattern": "[0-9]{11}", "minlength": 11, "maxlength": 11,
            "placeholder": PHONE_ERROR, "title": PHONE_ERROR,
        }),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("role", "username", "real_name", "phone")
        labels = {"username": "学号 / 工号 / 账号"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        account_field = self.fields["username"]
        account_attrs = {
            "data-account-maxlength": account_field.max_length,
            "data-account-help": str(account_field.help_text),
            "placeholder": "请输入工号或账号",
        }
        role = (
            self.data.get(self.add_prefix("role")) if self.is_bound
            else self.initial.get("role", User.Role.STUDENT)
        )
        if role == User.Role.STUDENT:
            # UsernameField normalizes full-width digits and strips whitespace.
            # Validate the original student number instead, before normalization.
            self.fields["username"] = forms.RegexField(
                label="学号", regex=r"\A[0-9]{12}\Z",
                strip=False,
                error_messages=dict.fromkeys(
                    ("required", "invalid", "min_length", "max_length"),
                    self.STUDENT_NUMBER_ERROR,
                ),
                help_text="学号须为 12 位半角数字（0–9），前导零会保留。",
                widget=forms.TextInput(attrs={
                    **account_attrs, "inputmode": "numeric", "autocomplete": "username",
                    "autocapitalize": "none", "autofocus": True,
                    "pattern": "[0-9]{12}", "minlength": 12, "maxlength": 12,
                    "placeholder": "请输入 12 位学号", "title": self.STUDENT_NUMBER_ERROR,
                }),
            )
        else:
            account_field.widget.attrs.update(account_attrs)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.approval_status = (
            User.ApprovalStatus.APPROVED
            if user.role == User.Role.STUDENT
            else User.ApprovalStatus.PENDING
        )
        if commit:
            user.save()
        return user


class AccountAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role not in User.Role.values:
            raise ValidationError(
                "账号角色异常，请联系管理员。", code="invalid_role"
            )
        if user.approval_status == User.ApprovalStatus.PENDING:
            raise ValidationError(
                "账号待审核，请等待管理员审核通过后再登录。", code="pending"
            )
        if user.approval_status == User.ApprovalStatus.REJECTED:
            reason = user.approval_reason or "请联系管理员了解详情。"
            raise ValidationError(
                "账号审核未通过：%(reason)s", code="rejected", params={"reason": reason}
            )
        if user.approval_status != User.ApprovalStatus.APPROVED:
            raise ValidationError("账号状态异常，请联系管理员。", code="invalid_status")
