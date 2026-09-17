from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    username = None
    first_name = forms.CharField(
        label="이름", required=True, max_length=150,
        error_messages={"required": "이름을 입력해 주세요."},
    )
    email = forms.EmailField(
        label="아이디(이메일)", required=True,
        widget=forms.EmailInput(attrs={
            "autocomplete": "username",
            "placeholder": "name@example.com",
        }),
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
        fields = ("first_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("이미 가입된 이메일입니다. 다른 이메일을 입력해 주세요.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        if email:
            # Django의 기본 인증은 username을 사용한다. 새 계정에서는 이를 이메일과
            # 같은 값으로 저장해 로그인 아이디를 이메일로 일관되게 유지한다.
            self.instance.username = email
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            self.save_m2m()
        return user


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="아이디(이메일)",
        widget=forms.EmailInput(attrs={"autocomplete": "username", "placeholder": "name@example.com"}),
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()

    def clean(self):
        email = self.cleaned_data.get("username")
        if email:
            # 기존 계정의 username이 이메일이 아니어도, 사용자가 등록 이메일로
            # 로그인할 수 있게 인증 백엔드에 저장된 username을 넘긴다.
            existing_username = User.objects.filter(email__iexact=email).values_list("username", flat=True).first()
            if existing_username:
                self.cleaned_data["username"] = existing_username
        return super().clean()
