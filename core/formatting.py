"""Date, currency and greeting formatting shared by screens."""

from core.config import MONTH_SHORT
from datetime import date
from datetime import datetime


def recent_month_keys(count=6):
    today = date.today()
    keys = []
    for offset in range(count - 1, -1, -1):
        index = today.year * 12 + today.month - 1 - offset
        keys.append("{:04d}-{:02d}".format(index // 12, index % 12 + 1))
    return keys


def month_short(key):
    return MONTH_SHORT[int(key[5:7]) - 1]


def money_etb(value):
    return "{:,.2f}".format(float(value or 0))


def greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good Morning"
    if hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def today_name():
    return datetime.now().strftime("%B %d, %Y")
