from django.urls import path
from . import views

app_name = "chat"
urlpatterns = [
    path("", views.home, name="home"),
    path("api/state/", views.state, name="state"),
    path("api/readiness/", views.readiness, name="readiness"),
    path("api/readiness/retry/", views.retry_readiness, name="retry-readiness"),
    path("api/chat/", views.send_message, name="send"),
    path("api/documents/", views.upload_document, name="upload"),
    path("api/documents/<str:document_id>/delete/", views.delete_document, name="delete-document"),
    path("api/simplify/", views.simplify_message, name="simplify"),
    path("api/reset/", views.reset, name="reset"),
]
