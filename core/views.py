from django.shortcuts import render


def home(request):
    return render(request, "core/home.html")


def login_preview(request):
    """Render the shared login UI until the accounts app connects authentication."""
    return render(request, "registration/login.html", {"preview_mode": True})
