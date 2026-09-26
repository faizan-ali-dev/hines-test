from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.db import models


class ClientUser(AbstractUser):
    """The account used by the client signup and dashboard experience."""

    class AssignmentStatus(models.TextChoices):
        DEMO = "demo", "Demo"
        CLIENT = "client", "Client"

    email = models.EmailField(unique=True)
    referral_code = models.CharField(max_length=100, blank=True)
    demo_progress = models.PositiveSmallIntegerField(default=0)
    client_progress = models.PositiveSmallIntegerField(default=0)
    demo_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        verbose_name="Demo earnings",
        help_text="Demo earnings (auto-calculated from completed demo tasks, but editable)."
    )
    client_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        verbose_name="Client earnings",
        help_text="Client earnings (auto-calculated from completed client tasks, but editable)."
    )
    total_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        verbose_name="Total earnings",
        help_text="Total earnings (auto-calculated, but editable by admin)."
    )
    carried_demo_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        verbose_name="Carried demo earnings"
    )
    client_activated_at = models.DateTimeField(blank=True, null=True)
    assignment_status = models.CharField(max_length=10, choices=AssignmentStatus.choices, default=AssignmentStatus.DEMO)

    @property
    def full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    @property
    def client_is_active(self):
        return self.assignment_status == self.AssignmentStatus.CLIENT


class DemoUser(ClientUser):
    class Meta:
        proxy = True
        verbose_name = "Demo User"
        verbose_name_plural = "Demo Users"


class ActiveClientUser(ClientUser):
    class Meta:
        proxy = True
        verbose_name = "Client User"
        verbose_name_plural = "Client Users"

class SiteSetting(models.Model):
    facebook_url = models.URLField(blank=True, default="")
    twitter_url = models.URLField(blank=True, default="")
    linkedin_url = models.URLField(blank=True, default="")
    instagram_url = models.URLField(blank=True, default="")
    youtube_url = models.URLField(blank=True, default="")
    
    class Meta:
        verbose_name = "Site Setting"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"
