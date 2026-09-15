from django.contrib import admin

from .models import Attachment, CaseFact, ChecklistItem, ContractCase, Report, ScheduleEvent

admin.site.register((ContractCase, Attachment, CaseFact, ChecklistItem, ScheduleEvent, Report))
