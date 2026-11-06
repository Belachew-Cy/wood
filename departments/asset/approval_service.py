from datetime import datetime

from extensions import db
from modules.asset_management.approval_models import AssetApproval


VALID_ACTIONS = {
    "Approved",
    "Rejected",
    "Returned for Correction",
}


def submit_for_approval(
    reference_type,
    reference_id,
    requested_by,
    approval_steps,
):
    """
    approval_steps example:

    [
        {"approver_id": 10, "level": "Department Manager"},
        {"approver_id": 20, "level": "Director"},
    ]
    """

    if not approval_steps:
        raise ValueError("At least one approver is required.")

    try:
        for step in approval_steps:
            approval = AssetApproval(
                reference_type=reference_type,
                reference_id=reference_id,
                requested_by=requested_by,
                approver_id=step["approver_id"],
                approval_level=step["level"],
                action="Pending",
            )
            db.session.add(approval)

        db.session.flush()

        first_approval = (
            AssetApproval.query.filter_by(
                reference_type=reference_type,
                reference_id=reference_id,
            )
            .order_by(AssetApproval.id.asc())
            .first()
        )

        db.session.commit()
        return first_approval

    except Exception:
        db.session.rollback()
        raise


def process_approval(approval_id, user_id, decision, remarks=None):

    if decision not in VALID_ACTIONS:
        raise ValueError("Invalid approval decision.")

    approval = (
        AssetApproval.query.filter_by(
            id=approval_id,
            approver_id=user_id,
            action="Pending",
        )
        .with_for_update()
        .first()
    )

    if not approval:
        raise ValueError(
            "Approval not found or you are not authorized."
        )

    try:
        approval.action = decision
        approval.remarks = remarks
        approval.decided_at = datetime.utcnow()

        if decision == "Approved":
            next_approval = (
                AssetApproval.query.filter(
                    AssetApproval.reference_type
                    == approval.reference_type,
                    AssetApproval.reference_id
                    == approval.reference_id,
                    AssetApproval.action == "Pending",
                    AssetApproval.id > approval.id,
                )
                .order_by(AssetApproval.id.asc())
                .first()
            )

            if next_approval:
                # Notify the next approver through the existing
                # system notification service.
                pass
            else:
                # All approval stages completed.
                # Update the related transaction's final status
                # using the transaction-specific integration.
                pass

        db.session.commit()
        return approval

    except Exception:
        db.session.rollback()
        raise