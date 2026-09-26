from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AssignmentLot, ProgressChange, TaskDefinition
from .services import (
    CLIENT_TOTAL_LOTS,
    DEMO_TOTAL_LOTS,
    activate_client_account,
    assignment_summary,
    assign_task_to_eligible_users,
    create_default_lots,
    set_assignment_status,
    set_progress,
    sync_progress_from_lots,
)


class AssignmentServiceTests(TestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user(
            username="john@example.com",
            email="john@example.com",
            password="A-strong-password-2026",
            first_name="John",
            last_name="Smith",
        )
        create_default_lots(self.employee)

    def test_default_lots_match_required_assignment_sizes(self):
        self.assertEqual(
            AssignmentLot.objects.filter(assignment_type=TaskDefinition.AssignmentType.DEMO).count(),
            DEMO_TOTAL_LOTS,
        )
        self.assertEqual(
            AssignmentLot.objects.filter(assignment_type=TaskDefinition.AssignmentType.CLIENT).count(), 0,
        )

    def test_progress_marks_only_the_first_lots_and_recalculates_earnings(self):
        set_progress(self.employee, TaskDefinition.AssignmentType.DEMO, 6)

        summary = assignment_summary(self.employee, TaskDefinition.AssignmentType.DEMO)
        self.assertEqual(summary["completed"], 6)
        self.assertEqual(summary["remaining"], 9)
        self.assertEqual(summary["current_earnings"], Decimal("31.00"))
        self.assertEqual(summary["percentage"], 40.0)
        self.assertTrue(AssignmentLot.objects.get(assignment_type="demo", lot_number=6).is_completed)
        self.assertFalse(AssignmentLot.objects.get(assignment_type="demo", lot_number=7).is_completed)

        set_progress(self.employee, TaskDefinition.AssignmentType.DEMO, 5)
        self.assertFalse(AssignmentLot.objects.get(assignment_type="demo", lot_number=6).is_completed)
        self.assertEqual(
            assignment_summary(self.employee, TaskDefinition.AssignmentType.DEMO)["current_earnings"],
            Decimal("25.00"),
        )
        self.assertEqual(ProgressChange.objects.count(), 2)

    def test_client_activation_removes_demo_earnings_and_tasks(self):
        with self.assertRaisesMessage(ValueError, "All Demo lots must be completed"):
            activate_client_account(self.employee)

        set_progress(self.employee, TaskDefinition.AssignmentType.DEMO, 15)
        activated = activate_client_account(self.employee)
        self.assertTrue(activated.client_is_active)
        self.assertEqual(activated.demo_earnings, Decimal("0.00"))
        self.assertEqual(activated.total_earnings, Decimal("0.00"))
        self.assertFalse(AssignmentLot.objects.filter(employee=self.employee, assignment_type=TaskDefinition.AssignmentType.DEMO).exists())

        set_progress(self.employee, TaskDefinition.AssignmentType.CLIENT, 14)
        activated.refresh_from_db()
        self.assertGreater(activated.client_earnings, Decimal("0.00"))
        self.assertEqual(activated.total_earnings, activated.client_earnings)

    def test_staff_can_add_a_custom_task_and_mark_it_complete(self):
        task = TaskDefinition.objects.create(
            assignment_type=TaskDefinition.AssignmentType.DEMO,
            lot_number=16,
            task_name="Custom Property Review",
            task_description="A task created by an administrator.",
            task_value=Decimal("900.00"),
            employee_earning=Decimal("90.00"),
            task_link="https://example.com/task",
        )
        second_employee = get_user_model().objects.create_user(username="sara@example.com", email="sara@example.com", password="A-strong-password-2026")
        create_default_lots(second_employee)
        assign_task_to_eligible_users(task)
        AssignmentLot.objects.filter(employee=self.employee, task_definition=task).update(is_completed=True)
        sync_progress_from_lots(self.employee, TaskDefinition.AssignmentType.DEMO)

        summary = assignment_summary(self.employee, TaskDefinition.AssignmentType.DEMO)
        self.assertEqual(summary["total_lots"], 16)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["current_earnings"], Decimal("90.00"))
        self.assertEqual(AssignmentLot.objects.get(employee=self.employee, task_definition=task).task_link, "https://example.com/task")
        self.assertTrue(AssignmentLot.objects.filter(employee=second_employee, task_definition=task).exists())

        moved = set_assignment_status(self.employee, "client")
        self.assertTrue(moved.client_is_active)
        self.assertEqual(moved.demo_earnings, Decimal("0.00"))
        self.assertEqual(moved.total_earnings, Decimal("0.00"))

# Create your tests here.
