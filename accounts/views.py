from datetime import timedelta

from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError, transaction

from .forms import SignUpForm
from .models import User


def claim_guest_conversation(request, user):
    """Attach the still-valid guest chat in this browser to the signed-in member."""
    from cases.models import ContractCase
    from chat.models import Conversation

    conversation_id = request.session.get("lens_conversation_id")
    if not conversation_id:
        return
    conversation = Conversation.objects.filter(
        pk=conversation_id,
        user__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if conversation is None:
        return
    user_questions = [
        message.get("content", "")
        for message in conversation.state.get("messages", [])
        if message.get("role") == "user" and message.get("content", "").strip()
    ]
    with transaction.atomic():
        case = None
        if user_questions:
            from cases.services.titles import title_from_question
            case = ContractCase.objects.create(user=user, title=title_from_question(user_questions[0]))
        conversation.user = user
        conversation.case = case
        conversation.expires_at = timezone.now() + (timedelta(days=3650) if case else timedelta(days=1))
        conversation.save(update_fields=("user", "case", "expires_at", "updated_at"))
        if case:
            from cases.services.attachments import promote_pending_documents
            promote_pending_documents(conversation, case)
    if case:
        request.session["lens_case_id"] = str(case.pk)
    else:
        request.session.pop("lens_case_id", None)
    request.session["lens_conversation_id"] = str(conversation.pk)


class ChatLoginView(LoginView):
    """로그인 진입 경로와 관계없이 성공 후 채팅 홈을 연다."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse("chat:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        claim_guest_conversation(self.request, self.request.user)
        return response


def signup(request):
    if request.user.is_authenticated:
        return redirect("chat:home")
    form = SignUpForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            # The database constraint is the final guard when two requests race.
            if User.objects.filter(email__iexact=form.cleaned_data["email"]).exists():
                form.add_error("email", "이미 가입된 이메일입니다. 다른 이메일을 입력해 주세요.")
            elif User.objects.filter(username__iexact=form.cleaned_data["username"]).exists():
                form.add_error("username", "이미 사용 중인 아이디입니다. 다른 아이디를 입력해 주세요.")
            else:
                form.add_error(None, "회원가입 처리 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        else:
            login(request, user)
            claim_guest_conversation(request, user)
            return redirect("chat:home")
    return render(request, "accounts/signup.html", {"form": form})
