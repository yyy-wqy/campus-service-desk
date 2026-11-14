from django.contrib.auth import SESSION_KEY, logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from .models import User


class BusinessAccessMiddleware(MiddlewareMixin):
    """Check approval on every business request, including existing sessions.

    Authentication endpoints stay accessible so users can log out or sign in again.
    New views in the business namespaces inherit this check automatically.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match.view_name in {
            "accounts:login", "accounts:register", "accounts:logout", "core:login_preview",
            "accounts:password_reset", "accounts:password_reset_done",
            "accounts:password_reset_confirm", "accounts:password_reset_complete",
        }:
            return None
        if match.namespace not in {"core", "accounts", "tickets", "locations", "chat", "admin"}:
            return None
        user = request.user
        session_user_id = request.session.get(SESSION_KEY)
        # ModelBackend returns AnonymousUser for inactive users even while the
        # session still carries their account ID.
        if (user.is_authenticated and not user.is_active) or (
            not user.is_authenticated
            and session_user_id is not None
            and User.objects.filter(pk=session_user_id, is_active=False).exists()
        ):
            logout(request)
            return HttpResponseForbidden("账号已停用，请联系管理员。")
        if not user.is_authenticated:
            if match.namespace in {"accounts", "tickets", "locations", "chat"}:
                return redirect_to_login(request.get_full_path())
            return None
        if user.role == User.Role.MAINTENANCE and not user.can_handle_maintenance:
            if match.view_name in {
                "accounts:profile", "accounts:recovery_email", "accounts:qualifications",
                "accounts:qualification_upload", "accounts:qualification_submit",
                "accounts:material_retire", "accounts:material_download",
            }:
                return None
            return render(request, "registration/access_denied.html", status=403)
        if user.role not in User.Role.values or user.approval_status != User.ApprovalStatus.APPROVED:
            return render(request, "registration/access_denied.html", status=403)
        return None
