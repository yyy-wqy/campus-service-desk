from django.core.exceptions import ValidationError
from django.db.models.functions import Lower, Trim


PHONE_IN_USE = "该手机号已注册，请登录已有账号或使用其他手机号。"
EMAIL_IN_USE = "该邮箱已被使用，请使用其他邮箱。"


def normalize_phone(value):
    return (value or "").strip()


def normalize_email(value):
    return (value or "").strip().lower()


def validate_contact(field, value, exclude_pk=None):
    from .models import User

    value = normalize_phone(value) if field == "phone" else normalize_email(value)
    if value:
        expression = Trim("phone") if field == "phone" else Lower(Trim("email"))
        users = User.objects.annotate(contact_value=expression).filter(contact_value=value)
        if exclude_pk is not None:
            users = users.exclude(pk=exclude_pk)
        if users.exists():
            raise ValidationError(
                PHONE_IN_USE if field == "phone" else EMAIL_IN_USE,
                code=f"duplicate_{field}",
            )
    return value


def add_contact_conflict_errors(form, exclude_pk=None):
    """Recheck after a database race, outside the failed savepoint."""
    found = False
    for field in ("phone", "email"):
        if field not in form.cleaned_data:
            continue
        try:
            validate_contact(field, form.cleaned_data[field], exclude_pk)
        except ValidationError as error:
            form.add_error(field, error)
            found = True
    return found
