
from flask import Blueprint

asset_bp = Blueprint(
    "asset_management",
    __name__,
    url_prefix="/asset-management"
)

