from django.contrib.auth.views import PasswordResetCompleteView
from django.urls import path

from . import management, recovery, views

app_name = "accounts"

urlpatterns = [
    path("profile/", management.profile, name="profile"),
    path("reviews/", management.reviews, name="reviews"),
    path("reviews/<int:teacher_id>/", management.reviews, name="teacher_review"),
    path("register/", views.register, name="register"),
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", views.AccountLogoutView.as_view(), name="logout"),
    path("recovery-email/", recovery.recovery_email, name="recovery_email"),
    path("password-reset/", recovery.AccountPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", recovery.AccountPasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", recovery.AccountPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
