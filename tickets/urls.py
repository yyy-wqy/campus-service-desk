from django.urls import path

from . import note_views, views

app_name = "tickets"

urlpatterns = [
    path("<int:pk>/notes/", note_views.ticket_note_add, name="add_note"),
    path("", views.ticket_list, name="list"),
    path("create/", views.ticket_create, name="create"),
    path("tasks/", views.ticket_list, name="tasks"),
    path("manage/", views.ticket_list, name="manage"),
    path("cancellations/", views.ticket_list, name="cancellations"),
    path("<int:pk>/", views.ticket_detail, name="detail"),
]
