from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import ChecklistItem, ContractCase, Report, ScheduleEvent
from .services.report_pdf import render_report_pdf, report_filename
from .services.reports import generate_report, normalize_report_content


def owned_case(request, case_id):
    return get_object_or_404(ContractCase, pk=case_id, user=request.user)


@login_required
def dashboard(request, case_id=None):
    cases = list(request.user.contract_cases.order_by("created_at", "pk").prefetch_related(
        "schedule_events", "checklist_items", "reports",
    ))
    if case_id:
        selected_case = owned_case(request, case_id)
        current_id = str(selected_case.pk)
        request.session["lens_case_id"] = current_id
    else:
        current_id = request.session.get("lens_case_id")
    selected = next((case for case in cases if str(case.pk) == current_id), None)
    if selected is None and cases:
        selected = cases[0]

    rooms = []
    calendar_events = []
    for index, case in enumerate(cases):
        color_index = index % 8
        visible_events = [event for event in case.schedule_events.all()
                          if event.status != ScheduleEvent.Status.DISMISSED]
        rooms.append({
            "case": case,
            "color_index": color_index,
            "event_count": len(visible_events),
        })
        calendar_events.extend({
            "event": event,
            "case": case,
            "color_index": color_index,
        } for event in visible_events)

    return render(request, "cases/dashboard.html", {
        "cases": cases,
        "selected": selected,
        "rooms": rooms,
        "calendar_events": calendar_events,
        "checklist_count": sum(
            case.checklist_items.exclude(state=ChecklistItem.State.DISMISSED).count()
            for case in cases
        ),
    })


@login_required
@require_POST
def create_case(request):
    if request.POST.get("auto_title") == "1":
        # New chat opens an empty draft. The first sent question creates the room.
        request.session.pop("lens_case_id", None)
        request.session.pop("lens_conversation_id", None)
        return redirect("chat:home")
    else:
        title = request.POST.get("title", "").strip()
    if not title or len(title) > 120:
        return render(request, "cases/dashboard.html", {
            "cases": request.user.contract_cases.all(), "selected": None,
            "case_error": "계약 이름은 1~120자로 입력해 주세요.",
        }, status=400)
    case = ContractCase.objects.create(user=request.user, title=title)
    request.session["lens_case_id"] = str(case.pk)
    request.session.pop("lens_conversation_id", None)
    return redirect("chat:home")


@login_required
@require_POST
def rename_case(request, case_id):
    case = owned_case(request, case_id)
    title = " ".join(request.POST.get("title", "").split()).strip()
    if not title or len(title) > 120:
        raise Http404
    case.title = title
    case.save(update_fields=("title", "updated_at"))
    if request.POST.get("next") == "mypage":
        return redirect("cases:dashboard-detail", case_id=case.pk)
    return redirect("chat:home")


@login_required
@require_GET
def open_chat(request, case_id):
    case = owned_case(request, case_id)
    request.session["lens_case_id"] = str(case.pk)
    request.session.pop("lens_conversation_id", None)
    return redirect("chat:home")


@login_required
@require_POST
def delete_case(request, case_id):
    case = owned_case(request, case_id)
    if request.session.get("lens_case_id") == str(case.pk):
        request.session.pop("lens_case_id", None)
        request.session.pop("lens_conversation_id", None)
    case.delete()
    return redirect("cases:dashboard")


@login_required
@require_POST
def toggle_checklist(request, case_id, item_id):
    case = owned_case(request, case_id)
    item = get_object_or_404(ChecklistItem, pk=item_id, case=case)
    item.state = ChecklistItem.State.TODO if item.state == ChecklistItem.State.DONE else ChecklistItem.State.DONE
    item.auto_completed = False
    item.save(update_fields=("state", "auto_completed", "updated_at"))
    return redirect("cases:dashboard-detail", case_id=case.pk)


@login_required
@require_POST
def update_event(request, case_id, event_id):
    case = owned_case(request, case_id)
    event = get_object_or_404(ScheduleEvent, pk=event_id, case=case)
    action = request.POST.get("action")
    if action not in {"confirm", "dismiss"}:
        raise Http404
    event.status = ScheduleEvent.Status.CONFIRMED if action == "confirm" else ScheduleEvent.Status.DISMISSED
    event.user_confirmed = action == "confirm"
    event.save(update_fields=("status", "user_confirmed"))
    return redirect("cases:dashboard-detail", case_id=case.pk)


@login_required
@require_POST
def create_report(request, case_id):
    case = owned_case(request, case_id)
    report = generate_report(case)
    return report_download_response(report)


def report_download_response(report):
    response = HttpResponse(render_report_pdf(report), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{report_filename(report)}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def download_report(request, case_id, report_id):
    case = owned_case(request, case_id)
    report = get_object_or_404(Report, pk=report_id, case=case)
    return report_download_response(report)


@login_required
@require_GET
def report_detail(request, case_id, report_id):
    case = owned_case(request, case_id)
    report = get_object_or_404(Report, pk=report_id, case=case)
    return render(request, "cases/report_detail.html", {
        "case": case,
        "report": report,
        "content": normalize_report_content(report.content_json),
    })
