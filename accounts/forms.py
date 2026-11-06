import uuid

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import User
from .contact_validation import validate_contact


class RegistrationForm(UserCreationForm):
    PHONE_ERROR = "请输入 11 位数字手机号"
    STUDENT_NUMBER_ERROR = "请输入 12 位数字学号"

    role = forms.ChoiceField(
        label="注册身份",
        choices=[choice for choice in User.Role.choices if choice[0] != User.Role.ADMIN],
    )
    real_name = forms.CharField(label="姓名", max_length=50)
    department = forms.CharField(label="院系/部门", max_length=100, required=False)
    organization_number = forms.CharField(label="所属单位编号（选填）", max_length=100, required=False)
    email = forms.EmailField(
        label="找回邮箱（选填）", required=False, max_length=254,
        help_text="用于接收密码重置链接，请填写本人可访问的邮箱。",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
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
        fields = ("role", "username", "real_name", "department", "organization_number", "phone", "email")
        labels = {"username": "学号 / 工号 / 账号"}

    def clean_phone(self):
        return validate_contact("phone", self.cleaned_data["phone"])

    def clean_email(self):
        return validate_contact("email", self.cleaned_data["email"])

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
        if role == User.Role.TEACHER:
            self.fields["department"].required = True
            self.fields["username"].label = "工号"
        if role == User.Role.MAINTENANCE:
            account_field.required = False
            account_field.disabled = True
            account_field.initial = "maint_" + uuid.uuid4().hex
            account_field.widget = forms.HiddenInput()
            account_field.help_text = "维修登录账号将在注册成功后由平台分配。"

    def clean_username(self):
        username = self.cleaned_data.get("username", "")
        if self.cleaned_data.get("role") != User.Role.MAINTENANCE and username.lower().startswith("maint_"):
            raise ValidationError("此账号前缀由平台保留，请使用其他工号或账号。")
        if self.cleaned_data.get("role") == User.Role.MAINTENANCE:
            for attempt in range(10):
                if not User.objects.filter(username__iexact=username).exists():
                    self.cleaned_data["username"] = username
                    return super().clean_username()
                username = "maint_" + uuid.uuid4().hex
            raise ValidationError("暂时无法生成账号，请重试。")
        return super().clean_username()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.approval_status = (
            User.ApprovalStatus.APPROVED
            if user.role == User.Role.STUDENT
            else User.ApprovalStatus.PENDING
        )
        if commit:
            for attempt in range(5):
                try:
                    with transaction.atomic():
                        user.save()
                    break
                except IntegrityError:
                    if user.role != User.Role.MAINTENANCE or not User.objects.filter(username=user.username).exists():
                        raise
                    user.username = "maint_" + uuid.uuid4().hex
            else:
                raise IntegrityError("无法分配唯一维修账号，请重试。")
        return user


class AccountAuthenticationForm(AuthenticationForm):
    def clean(self):
        from .login_protection import protected_clean
        return protected_clean(self, super().clean)

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
