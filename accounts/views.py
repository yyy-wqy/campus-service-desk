from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import AccountAuthenticationForm, RegistrationForm
from .models import User
from .contact_validation import add_contact_conflict_errors


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            conflict = add_contact_conflict_errors(form)
            username = form.cleaned_data.get("username")
            if username and User.objects.filter(username__iexact=username).exists():
                form.add_error("username", "该账号已注册，请登录已有账号。")
                conflict = True
            if not conflict:
                raise
        else:
            if user.approval_status == User.ApprovalStatus.APPROVED:
                messages.success(request, "注册成功，请使用账号和密码登录。")
            else:
                messages.success(request, "注册成功，账号待审核。管理员审核通过后即可登录。")
            return redirect("accounts:login")
    return render(request, "registration/register.html", {"form": form})


class AccountLoginView(LoginView):
    authentication_form = AccountAuthenticationForm
    template_name = "registration/login.html"


class AccountLogoutView(LogoutView):
    next_page = "accounts:login"
