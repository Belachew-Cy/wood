
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime


def calculate_overtime_hours(
    start_time,
    end_time,
    break_minutes=0
):
    start = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")

    if end <= start:
        raise ValueError(
            "End time must be later than start time."
        )

    if break_minutes < 0:
        raise ValueError("Break minutes cannot be negative.")

    total_minutes = (
        (end - start).total_seconds() / 60
    )

    worked_minutes = total_minutes - break_minutes

    if worked_minutes <= 0:
        raise ValueError("Overtime duration must be positive.")

    return (
        Decimal(str(worked_minutes)) / Decimal("60")
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def calculate_overtime_amount(
    basic_salary,
    standard_monthly_hours,
    approved_hours,
    multiplier
):
    salary = Decimal(str(basic_salary))
    monthly_hours = Decimal(str(standard_monthly_hours))
    hours = Decimal(str(approved_hours))
    rate_multiplier = Decimal(str(multiplier))

    if salary < 0 or hours < 0:
        raise ValueError("Salary and hours cannot be negative.")

    if monthly_hours <= 0:
        raise ValueError("Monthly working hours must be positive.")

    if rate_multiplier <= 0:
        raise ValueError("Overtime multiplier must be positive.")

    hourly_rate = salary / monthly_hours

    overtime_amount = (
        hourly_rate * hours * rate_multiplier
    )

    return {
        "hourly_rate": hourly_rate.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        ),
        "approved_hours": hours,
        "multiplier": rate_multiplier,
        "overtime_amount": overtime_amount.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    }
