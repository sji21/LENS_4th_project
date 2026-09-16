from django.urls import path

from . import views

app_name = "cases"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("new/", views.create_case, name="create"),
    path("<uuid:case_id>/", views.dashboard, name="dashboard-detail"),
    path("<uuid:case_id>/chat/", views.open_chat, name="open-chat"),
    path("<uuid:case_id>/rename/", views.rename_case, name="rename"),
    path("<uuid:case_id>/delete/", views.delete_case, name="delete"),
    path("<uuid:case_id>/checklist/<int:item_id>/", views.toggle_checklist, name="toggle-checklist"),
    path("<uuid:case_id>/events/<uuid:event_id>/", views.update_event, name="update-event"),
    path("<uuid:case_id>/reports/new/", views.create_report, name="create-report"),
    path("<uuid:case_id>/reports/<int:report_id>/", views.report_detail, name="report"),
    path("<uuid:case_id>/reports/<int:report_id>/download/", views.download_report, name="download-report"),
]
