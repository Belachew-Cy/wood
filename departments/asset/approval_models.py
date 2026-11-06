from datetime import datetime

from extensions import db


class AssetApproval(db.Model):
    __tablename__ = "asset_approvals"

    id = db.Column(db.Integer, primary_key=True)

    reference_type = db.Column(
        db.String(50), nullable=False, index=True
    )

    reference_id = db.Column(
        db.Integer, nullable=False, index=True
    )

    requested_by = db.Column(
        db.Integer, nullable=False, index=True
    )

    approver_id = db.Column(
        db.Integer, nullable=False, index=True
    )

    approval_level = db.Column(
        db.String(50), nullable=False
    )

    action = db.Column(
        db.String(30), default="Pending", nullable=False
    )

    remarks = db.Column(db.Text)

    submitted_at = db.Column(
        db.DateTime, default=datetime.utcnow
    )

    decided_at = db.Column(db.DateTime)

    created_at = db.Column(
        db.DateTime, default=datetime.utcnow
    )