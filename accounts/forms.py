from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import User


class RegistrationForm(UserCreationForm):
    role = forms.ChoiceField(
        label="注册身份",
        choices=[choice for choice in User.Role.choices if choice[0] != User.Role.ADMIN],
    )
    real_name = forms.CharField(label="姓名", max_length=50)
    phone = forms.CharField(label="联系方式", max_length=30)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("role", "username", "real_name", "phone")
        labels = {"username": "学号 / 工号 / 账号"}

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
