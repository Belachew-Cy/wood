from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value or 0)).quantize(
        CENT,
        rounding=ROUND_HALF_UP
    )


def calculate_straight_line(
    purchase_cost,
    salvage_value,
    useful_life_years
):
    cost = money(purchase_cost)
    salvage = money(salvage_value)

    if useful_life_years <= 0:
        raise ValueError("Useful life must be greater than zero.")

    if salvage < 0 or salvage > cost:
        raise ValueError("Invalid salvage value.")

    annual_depreciation = (
        (cost - salvage) / Decimal(useful_life_years)
    )

    return money(annual_depreciation)


def calculate_declining_balance(
    opening_book_value,
    salvage_value,
    useful_life_years,
    depreciation_rate=None
):
    opening = money(opening_book_value)
    salvage = money(salvage_value)

    if useful_life_years <= 0:
        raise ValueError("Useful life must be greater than zero.")

    if salvage < 0 or salvage > opening:
        raise ValueError("Invalid salvage value.")

    if depreciation_rate is None:
        rate = Decimal("2") / Decimal(useful_life_years)
    else:
        rate = Decimal(str(depreciation_rate))

    if rate <= 0 or rate > 1:
        raise ValueError(
            "Depreciation rate must be greater than 0 and at most 1."
        )

    depreciation = opening * rate

    # Never depreciate below the salvage value.
    depreciation = min(depreciation, opening - salvage)

    return money(max(depreciation, Decimal("0")))