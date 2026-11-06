from copy import deepcopy

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .contact_validation import add_contact_conflict_errors, validate_contact
from .forms import RegistrationForm
from .models import TeacherReview, User


class ProfileForm(forms.ModelForm):
    phone = deepcopy(RegistrationForm.base_fields["phone"])

    class Meta:
        model = User
        fields = ("real_name", "phone", "department", "organization_number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["real_name"].required = True
        self.fields["department"].required = self.instance.role == User.Role.TEACHER
        if self.instance.role != User.Role.MAINTENANCE:
            self.fields.pop("organization_number")

    def clean_phone(self):
        return validate_contact("phone", self.cleaned_data["phone"], self.instance.pk)

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            # Do not overwrite concurrent review or credential changes with a
            # stale request.user instance loaded before the profile submission.
            user.save(update_fields=list(self.fields))
        return user


class ReviewForm(forms.Form):
    result = forms.ChoiceField(label="审核结果", choices=[("approved", "通过"), ("rejected", "拒绝")])
    reason = forms.CharField(label="审核说明（拒绝必填）", required=False, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get("result") == "rejected" and not data.get("reason"):
            self.add_error("reason", "拒绝审核必须填写原因。")
        return data


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            if not add_contact_conflict_errors(form, request.user.pk):
                raise
        else:
            messages.success(request, "个人资料已更新。")
            return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def reviews(request, teacher_id=None):
    if request.user.role != User.Role.ADMIN:
        raise PermissionDenied
    teacher = get_object_or_404(User, pk=teacher_id, role=User.Role.TEACHER) if teacher_id else None
    form = ReviewForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and teacher and form.is_valid():
        with transaction.atomic():
            User.objects.filter(pk=teacher.pk).update(approval_status=teacher.approval_status)
            teacher.approval_status = form.cleaned_data["result"]
            teacher.approval_reason = form.cleaned_data["reason"]
            teacher.save(update_fields=["approval_status", "approval_reason"])
            TeacherReview.objects.create(teacher=teacher, reviewer=request.user, result=teacher.approval_status, reason=teacher.approval_reason)
        messages.success(request, "审核结果已保存。")
        return redirect("accounts:teacher_review", teacher_id=teacher.pk)
    return render(request, "accounts/reviews.html", {
        "teacher": teacher, "form": form,
        "teachers": User.objects.filter(role=User.Role.TEACHER).order_by("id"),
        "history": teacher.review_history.select_related("reviewer").order_by("-created_at", "-id") if teacher else [],
    })
