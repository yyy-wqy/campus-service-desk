from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import AccountAuthenticationForm, RegistrationForm
from .models import User
from .contact_validation import add_contact_conflict_errors
from .database_retry import retry_sqlite_lock


@require_http_methods(["GET", "POST"])
@retry_sqlite_lock
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
            if user.role == User.Role.MAINTENANCE:
                messages.success(request, "注册成功，账号待审核。请先登录提交维修资质材料，审核通过后即可处理维修任务。")
            elif user.approval_status == User.ApprovalStatus.APPROVED:
                messages.success(request, "注册成功，请使用账号和密码登录。")
            else:
                messages.success(request, "注册成功，账号待审核。管理员审核通过后即可登录。")
            if user.role == User.Role.MAINTENANCE:
                messages.success(request, f"你的维修登录账号是：{user.username}，请妥善保存。单位编号不是登录账号。")
            return redirect("accounts:login")
    return render(request, "registration/register.html", {"form": form})


class AccountLoginView(LoginView):
    authentication_form = AccountAuthenticationForm
    template_name = "registration/login.html"

    def get_success_url(self):
        user = self.request.user
        if user.role == User.Role.MAINTENANCE and not user.can_handle_maintenance:
            from django.urls import reverse
            return reverse("accounts:qualifications")
        return super().get_success_url()


class AccountLogoutView(LogoutView):
    next_page = "accounts:login"
