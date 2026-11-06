"""The stock desk's blueprint, kept apart so both route modules share it."""

from flask import Blueprint


inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")
