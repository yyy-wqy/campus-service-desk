from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.contrib.auth.signals import user_login_failed
        from django.db.models.signals import post_save
        from accounts.models import LoginProtection
        from .operational_logging import login_failure, login_protection_saved
        user_login_failed.connect(login_failure, dispatch_uid="operations.login_failure")
        post_save.connect(login_protection_saved, sender=LoginProtection, dispatch_uid="operations.login_lock")
