from django.urls import path

from . import note_views, photo_views, repair_views, views

app_name = "tickets"

urlpatterns = [
    path("<int:pk>/photos/", photo_views.upload, name="photo_upload"),
    path("photos/<int:photo_id>/", photo_views.read, name="photo_read"),
    path("<int:pk>/repair/<str:action>/", repair_views.repair_action, name="repair_action"),
    path("<int:pk>/notes/", note_views.ticket_note_add, name="add_note"),
    path("", views.ticket_list, name="list"),
    path("create/", views.ticket_create, name="create"),
    path("tasks/", views.ticket_list, name="tasks"),
    path("manage/", views.ticket_list, name="manage"),
    path("cancellations/", views.cancellation_list, name="cancellations"),
    path("<int:pk>/accept/", views.ticket_accept, name="accept"),
    path("<int:pk>/reject/", views.ticket_reject, name="reject"),
    path("<int:pk>/priority/", views.ticket_set_priority, name="set_priority"),
    path("cancellations/<int:application_id>/review/", views.cancellation_review, name="cancellation_review"),
    path("<int:pk>/cancel/", views.cancellation_request, name="cancellation_request"),
    path("<int:pk>/", views.ticket_detail, name="detail"),
]
