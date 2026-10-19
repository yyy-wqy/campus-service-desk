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
    error_messages = {
        **AuthenticationForm.error_messages,
        "account_not_found": "账号不存在，请检查账号或先注册",
        "incorrect_password": "密码错误，请重新输入",
    }

    def get_invalid_login_error(self):
        try:
            user = User._default_manager.get_by_natural_key(self.cleaned_data["username"])
        except User.DoesNotExist:
            return ValidationError(
                self.error_messages["account_not_found"], code="account_not_found"
            )

        # The default backend also rejects inactive accounts. Verify the password
        # before showing their status, just as for pending/rejected accounts.
        if not user.check_password(self.cleaned_data["password"]):
            return ValidationError(
                self.error_messages["incorrect_password"], code="incorrect_password"
            )
        if not user.is_active:
            return ValidationError(self.error_messages["inactive"], code="inactive")
        return super().get_invalid_login_error()

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
