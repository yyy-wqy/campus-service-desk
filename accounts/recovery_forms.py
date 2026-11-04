from django import forms
from django.contrib.auth.forms import PasswordResetForm, UsernameField
from django.core.exceptions import ValidationError

from .models import User
from .contact_validation import validate_contact


class AccountPasswordResetForm(PasswordResetForm):
    username = UsernameField(label="账号", max_length=150)
    field_order = ("username", "email")

    def get_users(self, email):
        # Keep Django's active-account, usable-password and email checks.
        return (
            user for user in super().get_users(email)
            if user.get_username() == self.cleaned_data["username"]
            and user.role in User.Role.values
        )


class RecoveryEmailForm(forms.Form):
    email = forms.EmailField(label="找回邮箱", max_length=254)
    current_password = forms.CharField(
        label="当前密码", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        return validate_contact("email", self.cleaned_data["email"], self.user.pk)

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise ValidationError("当前密码错误，请重新输入。", code="incorrect_password")
        return password

    def save(self):
        self.user.email = self.cleaned_data["email"]
        self.user.save(update_fields=["email"])
        return self.user
