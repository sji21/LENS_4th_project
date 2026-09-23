from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    """Authenticate existing accounts by their unique email address."""

    username = forms.EmailField(
        label="이메일",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )

    def clean(self):
        email = self.cleaned_data.get("username", "").strip().lower()
        password = self.cleaned_data.get("password")

        if email and password:
            user = User.objects.filter(email__iexact=email).only("username").first()
            self.user_cache = authenticate(
                self.request,
                username=user.username if user is not None else email,
                password=password,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        label="이메일", required=True,
        error_messages={
            "required": "이메일을 입력해 주세요.",
            "invalid": "이메일 형식이 올바르지 않습니다. 예: name@example.com",
        },
    )

    error_messages = {
        **UserCreationForm.error_messages,
        "password_mismatch": "비밀번호와 비밀번호 확인이 일치하지 않습니다.",
    }

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("이미 가입된 이메일입니다. 다른 이메일을 입력해 주세요.")
        return email

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise forms.ValidationError("아이디를 입력해 주세요.")
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("이미 사용 중인 아이디입니다. 다른 아이디를 입력해 주세요.")
        return username
