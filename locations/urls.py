from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("housing/", views.housing, name="housing"),
    path("manage/", views.manage, name="manage"),
    path("manage/buildings/new/", views.building_form, name="building_create"),
    path("manage/buildings/<int:pk>/edit/", views.building_form, name="building_edit"),
    path("manage/categories/new/", views.category_form, name="category_create"),
    path("manage/categories/<int:pk>/edit/", views.category_form, name="category_edit"),
    path("manage/public-areas/new/", views.public_area_form, name="public_area_create"),
    path("manage/public-areas/<int:pk>/edit/", views.public_area_form, name="public_area_edit"),
    path("manage/<str:kind>/<int:pk>/toggle/", views.manage_toggle, name="manage_toggle"),
]
