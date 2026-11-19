from django.contrib.auth.views import PasswordResetCompleteView
from django.urls import path

from . import management, qualification_views, recovery, views

app_name = "accounts"

urlpatterns = [
    path("qualifications/", qualification_views.qualifications, name="qualifications"),
    path("qualifications/upload/", qualification_views.upload, name="qualification_upload"),
    path("qualifications/submit/", qualification_views.submit, name="qualification_submit"),
    path("materials/<int:material_id>/", qualification_views.download, name="material_download"),
    path("materials/<int:material_id>/retire/", qualification_views.retire, name="material_retire"),
    path("maintenance-reviews/", qualification_views.review, name="maintenance_reviews"),
    path("maintenance-reviews/<int:application_id>/", qualification_views.review, name="maintenance_review"),
    path("maintenance/<int:worker_id>/deactivate/", qualification_views.deactivate, name="maintenance_deactivate"),
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
