from django import forms
from .models import ClientUser


class ClientSignupForm(forms.Form):
    full_name = forms.CharField(
        max_length=300,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Full name", "autocomplete": "name"})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"placeholder": "name@example.com", "autocomplete": "email"})
    )
    referral_code = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Enter referral code", "autocomplete": "off"})
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")
        if ClientUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_referral_code(self):
        code = self.cleaned_data.get("referral_code", "").strip()
        if not code:
            raise forms.ValidationError("Referral code is required.")
        if ClientUser.objects.filter(referral_code__iexact=code).exists():
            raise forms.ValidationError("A user with this referral code already exists.")
        return code


class ClientLoginForm(forms.Form):
    email = forms.EmailField(
        required=True,
        label="Email",
        widget=forms.EmailInput(attrs={"placeholder": "name@example.com", "autocomplete": "email"})
    )
    referral_code = forms.CharField(
        required=True,
        label="Referral code",
        widget=forms.PasswordInput(attrs={"placeholder": "Enter referral code", "autocomplete": "current-password"})
    )

    def clean_email(self):
        return self.cleaned_data.get("email", "").strip().lower()

    def clean_referral_code(self):
        return self.cleaned_data.get("referral_code", "").strip()
