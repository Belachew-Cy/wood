"""The field rules every desk shares.

A screen, a direct URL and a forged POST are answered by the same code, so the
words a form is refused with are the words the register itself would use. Each
function raises ValueError with a sentence a flash can show as it stands.

These belong here rather than in one department's service because two desks
already need them; a third copy of a validator is how two desks end up refusing
the same bad date in two different ways.
"""

from datetime import date
from decimal import Decimal
from decimal import InvalidOperation


def clean_text(value, field, maximum=200, required=False):
    text = (value or "").strip()
    if required and not text:
        raise ValueError("{} is required.".format(field))
    if len(text) > maximum:
        raise ValueError("{} must be {} characters or fewer.".format(
            field, maximum))
    return text


def clean_choice(value, field, choices, default=""):
    """The one vocabulary word that matches, in the spelling the check wants."""
    text = (value or "").strip()
    if not text:
        return default
    for choice in choices:
        if choice.lower() == text.lower():
            return choice
    raise ValueError("Choose a valid {}.".format(field))


def clean_status(value, field, choices):
    text = (value or "").strip()
    if not text:
        raise ValueError("{} is required.".format(field))
    return clean_choice(text, field, choices)


def clean_amount(value, field="Budget"):
    text = (value or "").strip()
    if not text:
        return Decimal("0.00")
    try:
        amount = Decimal(text.replace(",", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        raise ValueError("{} must be a valid number.".format(field))
    if not amount.is_finite() or amount < 0:
        raise ValueError("{} cannot be negative.".format(field))
    if amount > Decimal("999999999999"):
        raise ValueError("{} is too large to record.".format(field))
    return amount.quantize(Decimal("0.01"))


def clean_date(value, field, required=False):
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("{} is required.".format(field))
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError("{} must be a valid date (YYYY-MM-DD).".format(field))
    if parsed.year < 1900 or parsed.year > 2200:
        raise ValueError("{} is out of range.".format(field))
    return parsed.isoformat()
