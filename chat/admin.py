from django.contrib import admin
from .models import LawWatch, LawAlert


@admin.register(LawWatch)
class LawWatchAdmin(admin.ModelAdmin):
    list_display = ("title", "checked_at", "last_error")
    readonly_fields = ("title", "metadata", "checked_at", "last_error")

    def has_add_permission(self, request):
        return False


@admin.register(LawAlert)
class LawAlertAdmin(admin.ModelAdmin):
    list_display = ("watch", "created_at", "resolved")
    list_filter = ("resolved",)
    readonly_fields = ("watch", "before", "after", "created_at")

    def has_add_permission(self, request):
        return False
