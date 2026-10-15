from django.shortcuts import redirect, render


def home(request):
    return render(request, "core/home.html")


def login_preview(request):
    """Keep the former preview URL working after authentication is connected."""
    return redirect("accounts:login")
