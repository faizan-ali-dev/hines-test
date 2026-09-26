from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import AssignmentLot, ProgressChange, TaskDefinition

DEMO_TOTAL_LOTS = 15
CLIENT_TOTAL_LOTS = 35
DEMO_TASK_VALUES = (60, 45, 50, 55, 40, 60, 50, 40, 35, 45, 50, 30, 40, 25, 25)
CLIENT_TASK_VALUES = (250, 275, 200, 300, 225) * 6 + (250, 275, 200, 300, 475)


def _task_values(assignment_type):
    if assignment_type == TaskDefinition.AssignmentType.DEMO:
        return DEMO_TASK_VALUES
    if assignment_type == TaskDefinition.AssignmentType.CLIENT:
        return CLIENT_TASK_VALUES
    raise ValueError("Unknown assignment type.")


@transaction.atomic
def ensure_default_task_definitions():
    """Seed the standard catalogue once, so all users share the same tasks."""
    for assignment_type in TaskDefinition.AssignmentType.values:
        if TaskDefinition.objects.filter(assignment_type=assignment_type).exists():
            continue
        definitions = []
        for number, value in enumerate(_task_values(assignment_type), start=1):
            task_value = Decimal(str(value)).quantize(Decimal("0.01"))
            definitions.append(TaskDefinition(
                assignment_type=assignment_type,
                lot_number=number,
                task_name="Property Task" if assignment_type == TaskDefinition.AssignmentType.DEMO else "Client Task",
                task_description="Complete the assigned property review task.",
                task_value=task_value,
                employee_earning=(task_value * Decimal("0.10")).quantize(Decimal("0.01")),
                task_link="https://www.hines.com/",
            ))
        TaskDefinition.objects.bulk_create(definitions, ignore_conflicts=True)


def _assignment_for(employee, task):
    return AssignmentLot(
        employee=employee, task_definition=task, assignment_type=task.assignment_type,
        lot_number=task.lot_number, task_name=task.task_name,
        task_description=task.task_description, task_value=task.task_value,
        employee_earning=task.employee_earning, task_link=task.task_link,
    )


@transaction.atomic
def ensure_assignments(employee, assignment_type):
    """Give an eligible employee every task in the shared catalogue."""
    if assignment_type == TaskDefinition.AssignmentType.DEMO and employee.client_is_active:
        return
    if assignment_type == TaskDefinition.AssignmentType.CLIENT and not employee.client_is_active:
        return
    ensure_default_task_definitions()
    definitions = list(TaskDefinition.objects.filter(assignment_type=assignment_type).order_by("lot_number"))
    existing_ids = set(AssignmentLot.objects.filter(employee=employee, task_definition__in=definitions).values_list("task_definition_id", flat=True))
    AssignmentLot.objects.bulk_create(
        [_assignment_for(employee, task) for task in definitions if task.pk not in existing_ids], ignore_conflicts=True,
    )



@transaction.atomic
def assign_task_to_eligible_users(task):
    """Assign an administrator-created catalogue task to every user in its stage."""
    from accounts.models import ClientUser

    users = ClientUser.objects.filter(assignment_status=task.assignment_type)
    existing_user_ids = set(AssignmentLot.objects.filter(task_definition=task).values_list("employee_id", flat=True))
    assignments = [_assignment_for(employee, task) for employee in users if employee.pk not in existing_user_ids]
    AssignmentLot.objects.bulk_create(assignments, ignore_conflicts=True)
    return len(assignments)


def create_default_lots(employee):
    """Ensure lots exist for employee based on current status and sync progress."""
    ensure_assignments(employee, employee.assignment_status)
    if employee.assignment_status == TaskDefinition.AssignmentType.DEMO:
        set_progress(employee, TaskDefinition.AssignmentType.DEMO, getattr(employee, "demo_progress", 0))
    elif employee.assignment_status == TaskDefinition.AssignmentType.CLIENT:
        set_progress(employee, TaskDefinition.AssignmentType.CLIENT, getattr(employee, "client_progress", 0))




def assignment_summary(employee, assignment_type):
    ensure_assignments(employee, assignment_type)
    lots = list(employee.assignment_lots.filter(assignment_type=assignment_type).order_by("lot_number"))
    completed_lots = [lot for lot in lots if lot.is_completed]
    calculated_earnings = sum((lot.employee_earning for lot in completed_lots), Decimal("0.00"))

    earnings_field = "demo_earnings" if assignment_type == TaskDefinition.AssignmentType.DEMO else "client_earnings"
    current_earnings = getattr(employee, earnings_field, None)
    if current_earnings is None:
        current_earnings = calculated_earnings
    elif current_earnings == Decimal("0.00") and calculated_earnings > Decimal("0.00"):
        current_earnings = calculated_earnings
        setattr(employee, earnings_field, calculated_earnings)
        setattr(employee, "total_earnings", calculated_earnings)
        if getattr(employee, "pk", None):
            type(employee).objects.filter(pk=employee.pk).update(**{earnings_field: calculated_earnings, "total_earnings": calculated_earnings})

    potential_earnings = sum((lot.employee_earning for lot in lots), Decimal("0.00"))
    completed = len(completed_lots)
    total_earnings = getattr(employee, "total_earnings", current_earnings)
    if total_earnings is None:
        total_earnings = current_earnings
    return {
        "lots": lots, "total_lots": len(lots), "completed": completed,
        "remaining": len(lots) - completed,
        "percentage": round((completed / len(lots)) * 100, 2) if lots else 0,
        "current_earnings": current_earnings,
        "total_earnings": total_earnings,
        "potential_earnings": potential_earnings,
        "remaining_potential": max(Decimal("0.00"), potential_earnings - current_earnings),
    }


@transaction.atomic
def sync_progress_from_lots(employee, assignment_type, changed_by=None):
    db_employee = type(employee).objects.select_for_update().get(pk=employee.pk)
    progress_field = "demo_progress" if assignment_type == TaskDefinition.AssignmentType.DEMO else "client_progress"
    earnings_field = "demo_earnings" if assignment_type == TaskDefinition.AssignmentType.DEMO else "client_earnings"
    previous_progress = getattr(db_employee, progress_field)
    lots = list(AssignmentLot.objects.filter(employee=db_employee, assignment_type=assignment_type))
    completed_lots = [l for l in lots if l.is_completed]
    completed = len(completed_lots)
    calculated_earnings = sum((l.employee_earning for l in completed_lots), Decimal("0.00"))

    setattr(db_employee, progress_field, completed)
    setattr(db_employee, earnings_field, calculated_earnings)
    setattr(db_employee, "total_earnings", calculated_earnings)
    db_employee.save(update_fields=(progress_field, earnings_field, "total_earnings"))

    setattr(employee, progress_field, completed)
    setattr(employee, earnings_field, calculated_earnings)
    setattr(employee, "total_earnings", calculated_earnings)

    if previous_progress != completed:
        ProgressChange.objects.create(
            employee=db_employee, assignment_type=assignment_type, previous_progress=previous_progress,
            new_progress=completed, changed_by=changed_by if getattr(changed_by, "is_authenticated", False) else None,
        )
    return db_employee

@transaction.atomic
def set_progress(employee, assignment_type, progress, changed_by=None, previous_progress=None):
    db_employee = type(employee).objects.select_for_update().get(pk=employee.pk)
    ensure_assignments(db_employee, assignment_type)
    lots = list(AssignmentLot.objects.select_for_update().filter(employee=db_employee, assignment_type=assignment_type).order_by("lot_number"))
    expected_total = len(lots)
    if not isinstance(progress, int) or isinstance(progress, bool):
        try:
            progress = int(progress)
        except (ValueError, TypeError):
            progress = 0
    progress = max(0, min(progress, expected_total))
    progress_field = "demo_progress" if assignment_type == TaskDefinition.AssignmentType.DEMO else "client_progress"
    earnings_field = "demo_earnings" if assignment_type == TaskDefinition.AssignmentType.DEMO else "client_earnings"
    stored_progress = getattr(db_employee, progress_field)
    previous_progress = stored_progress if previous_progress is None else previous_progress
    completed_lots = lots[:progress]
    completed_ids = [lot.pk for lot in completed_lots]
    AssignmentLot.objects.filter(pk__in=completed_ids).update(is_completed=True)
    AssignmentLot.objects.filter(employee=db_employee, assignment_type=assignment_type).exclude(pk__in=completed_ids).update(is_completed=False)
    calculated_earnings = sum((lot.employee_earning for lot in completed_lots), Decimal("0.00"))

    setattr(db_employee, progress_field, progress)
    setattr(db_employee, earnings_field, calculated_earnings)
    setattr(db_employee, "total_earnings", calculated_earnings)
    db_employee.save(update_fields=(progress_field, earnings_field, "total_earnings"))

    setattr(employee, progress_field, progress)
    setattr(employee, earnings_field, calculated_earnings)
    setattr(employee, "total_earnings", calculated_earnings)

    if previous_progress != progress:
        ProgressChange.objects.create(
            employee=db_employee, assignment_type=assignment_type, previous_progress=previous_progress,
            new_progress=progress, changed_by=changed_by if getattr(changed_by, "is_authenticated", False) else None,
        )
    return db_employee


@transaction.atomic
def activate_client_account(employee, changed_by=None, require_completed=True):
    employee = type(employee).objects.select_for_update().get(pk=employee.pk)
    demo = assignment_summary(employee, TaskDefinition.AssignmentType.DEMO)
    if require_completed and demo["completed"] != demo["total_lots"]:
        raise ValueError("All Demo lots must be completed before the client account can be activated.")

    # Remove previous demo earnings and lots:
    employee.demo_earnings = Decimal("0.00")
    employee.carried_demo_earnings = Decimal("0.00")
    employee.demo_progress = 0
    employee.client_progress = 0
    employee.client_earnings = Decimal("0.00")
    employee.total_earnings = Decimal("0.00")
    if employee.client_activated_at is None:
        employee.client_activated_at = timezone.now()
    employee.assignment_status = employee.AssignmentStatus.CLIENT

    AssignmentLot.objects.filter(employee=employee, assignment_type=TaskDefinition.AssignmentType.DEMO).delete()
    ensure_assignments(employee, TaskDefinition.AssignmentType.CLIENT)

    employee.save(update_fields=(
        "demo_earnings", "carried_demo_earnings", "demo_progress",
        "client_progress", "client_earnings", "total_earnings",
        "client_activated_at", "assignment_status"
    ))
    return employee


@transaction.atomic
def set_assignment_status(employee, status, changed_by=None):
    if status == employee.AssignmentStatus.CLIENT:
        return activate_client_account(employee, changed_by=changed_by, require_completed=False)
    if status != employee.AssignmentStatus.DEMO:
        raise ValueError("Unknown assignment status.")
    employee = type(employee).objects.select_for_update().get(pk=employee.pk)
    if employee.assignment_status != employee.AssignmentStatus.DEMO:
        employee.assignment_status = employee.AssignmentStatus.DEMO
        employee.client_earnings = Decimal("0.00")
        employee.client_progress = 0
        employee.carried_demo_earnings = Decimal("0.00")
        employee.demo_earnings = Decimal("0.00")
        employee.demo_progress = 0
        employee.total_earnings = Decimal("0.00")
        employee.client_activated_at = None
        AssignmentLot.objects.filter(employee=employee, assignment_type=TaskDefinition.AssignmentType.CLIENT).delete()
        ensure_assignments(employee, TaskDefinition.AssignmentType.DEMO)
        employee.save(update_fields=(
            "assignment_status", "client_earnings", "client_progress",
            "carried_demo_earnings", "demo_earnings", "demo_progress",
            "total_earnings", "client_activated_at"
        ))
    return employee

