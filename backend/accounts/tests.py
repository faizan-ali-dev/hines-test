from django.test import TestCase
from django.urls import reverse

from assignments.models import AssignmentLot, TaskDefinition
from assignments.services import activate_client_account, set_progress, assignment_summary

from .models import ClientUser


class ClientPageTests(TestCase):
    signup_payload = {
        "full_name": "Faizan Ali",
        "email": "faizan@example.com",
        "referral_code": "HINES-2026",
    }

    def test_signup_creates_an_authenticated_client_and_demo_dashboard(self):
        response = self.client.post(reverse("accounts:signup"), self.signup_payload)

        self.assertRedirects(response, reverse("accounts:demo-dashboard"))
        self.assertEqual(ClientUser.objects.count(), 1)
        self.assertEqual(AssignmentLot.objects.filter(employee__email="faizan@example.com").count(), 15)
        dashboard_response = self.client.get(reverse("accounts:demo-dashboard"))
        self.assertContains(dashboard_response, "Demo Assignment")
        self.assertContains(dashboard_response, "#001")
        self.assertContains(dashboard_response, "$0.00")

    def test_client_dashboard_stays_locked_until_staff_activation(self):
        self.client.post(reverse("accounts:signup"), self.signup_payload)
        employee = ClientUser.objects.get(email="faizan@example.com")
        self.assertEqual(self.client.get(reverse("accounts:client-dashboard")).status_code, 403)

        set_progress(employee, TaskDefinition.AssignmentType.DEMO, 15)
        activate_client_account(employee)
        self.assertEqual(
            AssignmentLot.objects.filter(employee=employee, assignment_type=TaskDefinition.AssignmentType.CLIENT).count(),
            35,
        )

        response = self.client.get(reverse("accounts:client-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Demo Earnings Carried Forward")
        self.assertContains(response, "Client Earnings")

    def test_client_can_log_in_and_log_out_with_browser_forms(self):
        self.client.post(reverse("accounts:signup"), self.signup_payload)
        self.client.post(reverse("accounts:logout"))

        login_response = self.client.post(
            reverse("accounts:login"),
            {"email": "faizan@example.com", "referral_code": self.signup_payload["referral_code"]},
        )
        self.assertRedirects(login_response, reverse("accounts:demo-dashboard"))
        self.assertContains(self.client.get(reverse("accounts:demo-dashboard")), "Faizan")

        logout_response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(logout_response, reverse("accounts:login"))
        self.assertEqual(self.client.get(reverse("accounts:demo-dashboard")).status_code, 302)

    def test_signup_rejects_duplicate_emails(self):
        self.client.post(reverse("accounts:signup"), self.signup_payload)
        self.client.post(reverse("accounts:logout"))

        payload = dict(self.signup_payload)
        payload["referral_code"] = "DIFFERENT-REF"
        response = self.client.post(reverse("accounts:signup"), payload)
        self.assertContains(response, "An account with this email already exists.")

    def test_signup_rejects_duplicate_referral_codes(self):
        self.client.post(reverse("accounts:signup"), self.signup_payload)
        self.client.post(reverse("accounts:logout"))

        payload = dict(self.signup_payload)
        payload["email"] = "another@example.com"
        response = self.client.post(reverse("accounts:signup"), payload)
        self.assertContains(response, "A user with this referral code already exists.")

    def test_public_frontend_pages_use_the_server_auth_routes(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        home_content = home.content.decode("utf-8")
        self.assertIn('href="/sign-up/"', home_content)
        self.assertIn('href="/client-login/"', home_content)
        self.assertEqual(self.client.get("/investment-management/").status_code, 200)

    def test_admin_control_table_exposes_progress_and_earnings_columns(self):
        admin_user = ClientUser.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="A-strong-password-2026",
        )
        self.client.force_login(admin_user)

        response = self.client.get("/admin/accounts/clientuser/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demo earnings")
        self.assertContains(response, "Client earnings")
        self.assertContains(response, "Total earnings")


class AdminProgressAndEarningsManagementTests(TestCase):
    def setUp(self):
        self.admin = ClientUser.objects.create_superuser(
            username="admin@test.com", email="admin@test.com", password="adminpassword"
        )
        self.client.force_login(self.admin)
        self.user = ClientUser.objects.create_user(
            username="client@test.com", email="client@test.com", referral_code="OLDREF123"
        )
        set_progress(self.user, TaskDefinition.AssignmentType.DEMO, 0)

    def test_changing_demo_progress_completes_lots_and_calculates_earnings(self):
        set_progress(self.user, TaskDefinition.AssignmentType.DEMO, 5)
        lots = list(AssignmentLot.objects.filter(employee=self.user, assignment_type=TaskDefinition.AssignmentType.DEMO).order_by("lot_number"))
        self.assertEqual(len(lots), 15)
        for i in range(5):
            self.assertTrue(lots[i].is_completed)
        for i in range(5, 15):
            self.assertFalse(lots[i].is_completed)

        summary = assignment_summary(self.user, TaskDefinition.AssignmentType.DEMO)
        self.assertEqual(summary["completed"], 5)
        self.assertEqual(summary["current_earnings"], sum(l.employee_earning for l in lots[:5]))

    def test_admin_editing_referral_code_syncs_user_password(self):
        from accounts.admin import ClientUserAdmin
        from django.contrib.admin.sites import AdminSite
        admin_obj = ClientUserAdmin(ClientUser, AdminSite())
        self.user.referral_code = "NEWREF999"
        admin_obj.save_model(None, self.user, None, change=True)
        self.assertTrue(self.user.check_password("NEWREF999"))

    def test_admin_can_edit_demo_earnings_directly(self):
        from decimal import Decimal
        set_progress(self.user, TaskDefinition.AssignmentType.DEMO, 5)
        self.user.refresh_from_db()
        self.assertEqual(self.user.demo_earnings, Decimal("25.00"))

        # Admin directly edits demo_earnings to $50.00
        self.user.demo_earnings = Decimal("50.00")
        self.user.save(update_fields=["demo_earnings"])

        summary = assignment_summary(self.user, TaskDefinition.AssignmentType.DEMO)
        self.assertEqual(summary["current_earnings"], Decimal("50.00"))

    def test_admin_can_edit_total_earnings_directly(self):
        from decimal import Decimal
        set_progress(self.user, TaskDefinition.AssignmentType.DEMO, 5)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_earnings, Decimal("25.00"))

        # Admin directly edits total_earnings to $200.00
        self.user.total_earnings = Decimal("200.00")
        self.user.save(update_fields=["total_earnings"])

        summary = assignment_summary(self.user, TaskDefinition.AssignmentType.DEMO)
        self.assertEqual(summary["total_earnings"], Decimal("200.00"))

    def test_switching_to_client_removes_demo_earnings_and_tasks(self):
        from decimal import Decimal
        from assignments.services import set_assignment_status
        set_progress(self.user, TaskDefinition.AssignmentType.DEMO, 15)
        self.user.refresh_from_db()
        self.assertEqual(self.user.demo_earnings, Decimal("65.00"))

        # Switch to client
        set_assignment_status(self.user, "client")
        self.user.refresh_from_db()
        self.assertEqual(self.user.assignment_status, "client")
        self.assertEqual(self.user.demo_earnings, Decimal("0.00"))
        self.assertEqual(self.user.carried_demo_earnings, Decimal("0.00"))
        self.assertEqual(self.user.total_earnings, Decimal("0.00"))
        self.assertEqual(self.user.demo_progress, 0)
        self.assertFalse(AssignmentLot.objects.filter(employee=self.user, assignment_type=TaskDefinition.AssignmentType.DEMO).exists())


