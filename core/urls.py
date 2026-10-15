from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("ui/login/", views.login_preview, name="login_preview"),
]
