from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

from .models import LoginProtection


class ProtectedModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username and LoginProtection.objects.filter(username=username, locked_until__gt=timezone.now()).exists():
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)
