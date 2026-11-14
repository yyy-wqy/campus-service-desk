from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from .forms import AccountAuthenticationForm


class ProtectedAdminAuthenticationForm(AccountAuthenticationForm, AdminAuthenticationForm):
    pass


admin.site.login_form = ProtectedAdminAuthenticationForm
