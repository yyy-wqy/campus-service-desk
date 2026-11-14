from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("", views.ticket_list, name="list"),
    path("create/", views.ticket_create, name="create"),
    path("tasks/", views.ticket_list, name="tasks"),
    path("manage/", views.ticket_list, name="manage"),
    path("cancellations/", views.ticket_list, name="cancellations"),
    path("<int:pk>/accept/", views.ticket_accept, name="accept"),
    path("<int:pk>/reject/", views.ticket_reject, name="reject"),
    path("<int:pk>/priority/", views.ticket_set_priority, name="set_priority"),
    path("<int:pk>/", views.ticket_detail, name="detail"),
]
