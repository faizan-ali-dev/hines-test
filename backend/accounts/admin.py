from decimal import Decimal
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.urls import reverse

from assignments.models import TaskDefinition, AssignmentLot
from assignments.services import (
    activate_client_account,
    assignment_summary,
    create_default_lots,
    set_assignment_status,
    set_progress,
    sync_progress_from_lots,
)
from .models import ClientUser, SiteSetting, DemoUser, ActiveClientUser


class AssignmentLotInline(admin.TabularInline):
    model = AssignmentLot
    extra = 0
    fields = ("assignment_type", "lot_number", "task_name", "task_description", "task_value", "employee_earning", "task_link", "is_completed")


admin.site.site_header = "Hines Assignment Control Panel"
admin.site.site_title = "Hines Control Panel"
admin.site.index_title = "Employee assignments"


class AssignmentStatusFilter(admin.SimpleListFilter):
    title = "assignment status"
    parameter_name = "assignment_status"

    def lookups(self, request, model_admin):
        return (
            ("demo-active", "Demo active"),
            ("demo-completed", "Demo completed"),
            ("client-active", "Client active"),
            ("assignment-completed", "Assignment completed"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "demo-active":
            return queryset.filter(assignment_status=ClientUser.AssignmentStatus.DEMO)
        if value == "demo-completed":
            return queryset.filter(client_activated_at__isnull=False)
        if value == "client-active":
            return queryset.filter(assignment_status=ClientUser.AssignmentStatus.CLIENT)
        if value == "assignment-completed":
            return queryset.filter(assignment_status=ClientUser.AssignmentStatus.CLIENT, client_progress__gt=0)
        return queryset


@admin.register(ClientUser)
class ClientUserAdmin(UserAdmin):
    fieldsets = (
        ("Employee identity", {"fields": ("username", "first_name", "last_name", "email", "referral_code", "is_active")}),
        (
            "Assignment control",
            {
                "fields": (
                    ("assignment_status", "demo_progress", "client_progress"),
                    "client_activated_at",
                    "view_tasks_link",
                    ("demo_earnings", "client_earnings", "total_earnings"),
                )
            },
        ),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    inlines = [AssignmentLotInline]
    add_fieldsets = UserAdmin.add_fieldsets + (("Employee identity", {"fields": ("email", "first_name", "last_name", "referral_code")}),)
    readonly_fields = (
        "view_tasks_link",
        "last_login",
        "date_joined",
    )
    list_display = (
        "employee_name",
        "email",
        "referral_code",
        "assignment_status",
        "demo_progress",
        "client_progress",
        "demo_earnings",
        "client_earnings",
        "total_earnings",
    )
    list_display_links = ("employee_name",)
    list_editable = ("referral_code", "assignment_status", "demo_progress", "client_progress", "demo_earnings", "client_earnings", "total_earnings")
    list_filter = (AssignmentStatusFilter, "is_active", "is_staff")
    search_fields = ("first_name", "last_name", "email", "referral_code")
    ordering = ("first_name", "last_name", "email")
    list_per_page = 25
    actions = ("activate_client_accounts",)

    class Media:
        css = {"all": ("admin/control_panel.css",)}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("assignment_lots")

    @admin.display(description="Name", ordering="first_name")
    def employee_name(self, obj):
        return obj.full_name or obj.username

    @admin.display(description="Manage Assigned Tasks")
    def view_tasks_link(self, obj):
        if not obj.pk:
            return "-"
        url = reverse('admin:assignments_assignmentlot_changelist') + f"?employee__id__exact={obj.pk}"
        return format_html('<a href="{}" class="button" style="padding: 5px 10px; background: #417690; color: white; border-radius: 4px; text-decoration: none;">View and Edit All Assigned Tasks</a>', url)

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            previous = ClientUser.objects.filter(pk=obj.pk).values(
                "demo_progress", "client_progress", "assignment_status", "referral_code",
                "demo_earnings", "client_earnings", "total_earnings"
            ).first()
        
        # Keep username in sync with email if needed
        if obj.email and not obj.username:
            obj.username = obj.email
            
        # If referral code changed or user created, sync password to referral code
        if obj.referral_code and (not change or (previous and previous.get("referral_code") != obj.referral_code)):
            obj.set_password(obj.referral_code)
            
        super().save_model(request, obj, form, change)
        if not change:
            create_default_lots(obj)
            return
            
        if previous and previous["assignment_status"] != obj.assignment_status:
            set_assignment_status(obj, obj.assignment_status, changed_by=request.user)
            return

        # If in DEMO:
        if obj.assignment_status == ClientUser.AssignmentStatus.DEMO:
            if previous and previous["demo_progress"] != obj.demo_progress:
                set_progress(
                    obj,
                    TaskDefinition.AssignmentType.DEMO,
                    obj.demo_progress,
                    changed_by=request.user,
                    previous_progress=previous["demo_progress"],
                )
                if form and "demo_earnings" in form.changed_data:
                    custom_val = form.cleaned_data.get("demo_earnings")
                    if custom_val is not None:
                        obj.demo_earnings = custom_val
                if form and "total_earnings" in form.changed_data:
                    custom_tot = form.cleaned_data.get("total_earnings")
                    if custom_tot is not None:
                        obj.total_earnings = custom_tot
                obj.save(update_fields=["demo_earnings", "total_earnings"])
            else:
                update_fields = []
                if form and "demo_earnings" in form.changed_data:
                    update_fields.append("demo_earnings")
                if form and "total_earnings" in form.changed_data:
                    update_fields.append("total_earnings")
                if update_fields:
                    obj.save(update_fields=update_fields)

        # If in CLIENT:
        elif obj.assignment_status == ClientUser.AssignmentStatus.CLIENT:
            if previous and previous["client_progress"] != obj.client_progress:
                set_progress(
                    obj,
                    TaskDefinition.AssignmentType.CLIENT,
                    obj.client_progress,
                    changed_by=request.user,
                    previous_progress=previous["client_progress"],
                )
                if form and "client_earnings" in form.changed_data:
                    custom_val = form.cleaned_data.get("client_earnings")
                    if custom_val is not None:
                        obj.client_earnings = custom_val
                if form and "total_earnings" in form.changed_data:
                    custom_tot = form.cleaned_data.get("total_earnings")
                    if custom_tot is not None:
                        obj.total_earnings = custom_tot
                obj.save(update_fields=["client_earnings", "total_earnings"])
            else:
                update_fields = []
                if form and "client_earnings" in form.changed_data:
                    update_fields.append("client_earnings")
                if form and "total_earnings" in form.changed_data:
                    update_fields.append("total_earnings")
                if update_fields:
                    obj.save(update_fields=update_fields)

    @admin.action(description="Activate selected client accounts")
    def activate_client_accounts(self, request, queryset):
        activated = 0
        unavailable = 0
        for employee in queryset:
            try:
                activate_client_account(employee, changed_by=request.user, require_completed=False)
                activated += 1
            except ValueError:
                unavailable += 1
        if activated:
            self.message_user(request, f"Activated {activated} client account(s).", messages.SUCCESS)
        if unavailable:
            self.message_user(
                request,
                f"{unavailable} account(s) could not be activated.",
                messages.WARNING,
            )

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model == AssignmentLot:
            obj = form.instance
            sync_progress_from_lots(obj, "demo", changed_by=request.user)
            sync_progress_from_lots(obj, "client", changed_by=request.user)


@admin.register(DemoUser)
class DemoUserAdmin(ClientUserAdmin):
    list_display = (
        "employee_name",
        "email",
        "referral_code",
        "demo_progress",
        "demo_earnings",
        "total_earnings",
    )
    list_editable = ("referral_code", "demo_progress", "demo_earnings", "total_earnings")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(assignment_status=ClientUser.AssignmentStatus.DEMO)


@admin.register(ActiveClientUser)
class ActiveClientUserAdmin(ClientUserAdmin):
    list_display = (
        "employee_name",
        "email",
        "referral_code",
        "client_progress",
        "client_earnings",
        "total_earnings",
    )
    list_editable = ("referral_code", "client_progress", "client_earnings", "total_earnings")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(assignment_status=ClientUser.AssignmentStatus.CLIENT)


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteSetting.objects.exists()
