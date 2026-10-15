from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import RegistrationForm, RoleAuthenticationForm
from .models import User


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        if user.approval_status == User.ApprovalStatus.APPROVED:
            messages.success(request, "注册成功，请选择学生身份登录。")
        else:
            messages.success(request, "注册成功，账号待审核。管理员审核通过后即可登录。")
        return redirect("accounts:login")
    return render(request, "registration/register.html", {"form": form})


class RoleLoginView(LoginView):
    authentication_form = RoleAuthenticationForm
    template_name = "registration/login.html"


class AccountLogoutView(LogoutView):
    next_page = "accounts:login"
