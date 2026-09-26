from hashlib import sha256
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from assignments.models import TaskDefinition
from assignments.services import assignment_summary, create_default_lots

from .forms import ClientLoginForm, ClientSignupForm
from .models import ClientUser

MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _login_attempt_cache_key(request, username):
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    raw_key = f"{remote_address}:{username.casefold()}".encode("utf-8")
    return f"client-login-attempts:{sha256(raw_key).hexdigest()}"


def _initials(user):
    parts = user.full_name.split()
    return "".join(part[:1] for part in parts[:2]).upper() or user.username[:2].upper()


def _dashboard_context(request, assignment_type):
    stats = assignment_summary(request.user, assignment_type)
    return {
        "stats": stats,
        "profile_initials": _initials(request.user),
        "support_url": "/contact/",
    }


@require_http_methods(["GET", "POST"])
def sign_up(request):
    if request.user.is_authenticated:
        return redirect("accounts:demo-dashboard")

    form = ClientSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        full_name = form.cleaned_data["full_name"].strip()
        email = form.cleaned_data["email"].strip().lower()
        referral_code = form.cleaned_data["referral_code"].strip()
        first_name, *remaining_name = full_name.split(maxsplit=1)
        with transaction.atomic():
            user = ClientUser.objects.create_user(
                username=email,
                email=email,
                password=referral_code,
                first_name=first_name,
                last_name=remaining_name[0] if remaining_name else "",
                referral_code=referral_code,
            )
            create_default_lots(user)
        login(request, user)
        return redirect("accounts:demo-dashboard")
    return render(request, "client/sign_up.html", {"form": form})


@require_http_methods(["GET", "POST"])
def client_login(request):
    if request.user.is_authenticated:
        return redirect("accounts:demo-dashboard")

    form = ClientLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        referral_code = form.cleaned_data["referral_code"].strip()
        attempt_key = _login_attempt_cache_key(request, email)
        if cache.get(attempt_key, 0) >= MAX_LOGIN_ATTEMPTS:
            form.add_error(None, "Too many unsuccessful login attempts. Try again in 15 minutes.")
        else:
            matching_user = ClientUser.objects.filter(email__iexact=email).first()
            if not matching_user:
                matching_user = ClientUser.objects.filter(username__iexact=email).first()

            user = None
            if matching_user:
                user = authenticate(request, username=matching_user.username, password=referral_code)
                if user is None and matching_user.referral_code and matching_user.referral_code.strip() == referral_code:
                    matching_user.set_password(referral_code)
                    matching_user.save(update_fields=["password"])
                    user = authenticate(request, username=matching_user.username, password=referral_code)

            if user is None:
                cache.add(attempt_key, 0, timeout=LOGIN_LOCKOUT_SECONDS)
                cache.incr(attempt_key)
                form.add_error(None, "Invalid email or referral code.")
            else:
                cache.delete(attempt_key)
                login(request, user)
                if user.client_is_active:
                    return redirect("accounts:client-dashboard")
                return redirect("accounts:demo-dashboard")
    return render(request, "client/login.html", {"form": form})


@require_POST
def client_logout(request):
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("accounts:login")


@login_required(login_url="accounts:login")
def dashboard(request):
    if request.user.client_is_active:
        return redirect("accounts:client-dashboard")
    return redirect("accounts:demo-dashboard")


@login_required(login_url="accounts:login")
def demo_dashboard(request):
    if request.user.client_is_active:
        return redirect("accounts:client-dashboard")
    context = _dashboard_context(request, TaskDefinition.AssignmentType.DEMO)
    context["client_access_available"] = request.user.client_is_active
    return render(request, "client/demo_dashboard.html", context)


@login_required(login_url="accounts:login")
def client_dashboard(request):
    if not request.user.client_is_active:
        return render(request, "client/client_access_pending.html", _dashboard_context(request, TaskDefinition.AssignmentType.DEMO), status=403)

    context = _dashboard_context(request, TaskDefinition.AssignmentType.CLIENT)
    context.update(
        {
            "client_earnings": context["stats"]["current_earnings"],
            "total_earnings": getattr(request.user, "total_earnings", context["stats"]["current_earnings"]),
        }
    )
    return render(request, "client/client_dashboard.html", context)


def public_frontend(request, frontend_path=""):
    """Serves the public frontend using Django templates."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    requested_path = frontend_path or "index.html"
    legacy_account_pages = {
        "signup/index.html": "accounts:signup",
        "client-login/index.html": "accounts:login",
        "dashboard/index.html": "accounts:demo-dashboard",
    }
    if requested_path in legacy_account_pages:
        return redirect(legacy_account_pages[requested_path])
    if requested_path.endswith("/"):
        requested_path = f"{requested_path}index.html"

    # We now serve templates from backend/templates/public/
    # If the file exists in that directory, we render it.
    root = settings.BASE_DIR / "templates" / "public"
    candidate = (root / requested_path).resolve()
    
    if root not in candidate.parents and candidate != root:
        raise Http404("Page not found.")
    if not candidate.is_file():
        raise Http404("Page not found.")

    content_type, _ = mimetypes.guess_type(candidate.name)
    
    if candidate.name.endswith(".html"):
        from .models import SiteSetting
        return render(
            request, 
            f"public/{requested_path}", 
            {"site_settings": SiteSetting.objects.first()}
        )

    # Fallback for non-HTML files that somehow slipped through (though they should be in static)
    return FileResponse(candidate.open("rb"), content_type=content_type)
