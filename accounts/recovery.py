import hashlib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetConfirmView, PasswordResetDoneView, PasswordResetView,
)
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import User
from .recovery_forms import AccountPasswordResetForm, RecoveryEmailForm
from .contact_validation import add_contact_conflict_errors


class AccountPasswordResetView(PasswordResetView):
    form_class = AccountPasswordResetForm
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        # Throttle by account, including mismatches, without disclosing existence.
        digest = hashlib.sha256(form.cleaned_data["username"].encode()).hexdigest()
        if not cache.add(f"password-reset:{digest}", True, timeout=60):
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)


class AccountPasswordResetConfirmView(PasswordResetConfirmView):
    success_url = reverse_lazy("accounts:password_reset_complete")

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        # Re-check on both GET and POST if an issued link's account is disabled.
        if user and user.is_active and user.has_usable_password() and user.role in User.Role.values:
            return user
        return None


class AccountPasswordResetDoneView(PasswordResetDoneView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["console_mail"] = (
            settings.DEBUG and settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
        )
        return context


@login_required
@never_cache
@sensitive_post_parameters("current_password")
@require_http_methods(["GET", "POST"])
def recovery_email(request):
    form = RecoveryEmailForm(
        request.user, request.POST if request.method == "POST" else None,
        initial={"email": request.user.email},
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            if not add_contact_conflict_errors(form, request.user.pk):
                raise
        else:
            messages.success(request, "找回邮箱已保存，请确认该邮箱可正常接收邮件。")
            return redirect("accounts:recovery_email")
    return render(request, "registration/recovery_email.html", {"form": form})
