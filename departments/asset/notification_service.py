from datetime import date

from modules.asset_management.models import (
    Asset,
    AssetInsurance,
    AssetWarranty,
    AssetDocument,
    AssetTransfer,
)


ALERT_DAYS = [90, 60, 30, 7]


def send_asset_notification(
    recipient_id,
    title,
    message,
    reference_type,
    reference_id,
    dedupe_key,
):
    """
    Connect this function to your EXISTING system notification service.
    Do not create a new Notification model or table.

    Example integration (uncomment and adjust):

    from modules.notifications.services import create_notification

    create_notification(
        user_id=recipient_id,
        title=title,
        message=message,
        category="asset_management",
        reference_type=reference_type,
        reference_id=reference_id,
        dedupe_key=dedupe_key,
    )
    """

    raise NotImplementedError(
        "Connect send_asset_notification() to the existing "
        "notification module."
    )


def send_expiry_alert(
    recipient_id,
    item_name,
    expiry_date,
    reference_type,
    reference_id,
):
    if not expiry_date:
        return

    days_left = (expiry_date - date.today()).days
    if days_left not in ALERT_DAYS:
        return

    title = f"{item_name} Expiry Alert"
    message = (
        f"{item_name} will expire in {days_left} days. "
        f"Expiry date: {expiry_date.strftime('%Y-%m-%d')}."
    )
    dedupe_key = (
        f"asset-{reference_type}-expiry:"
        f"{reference_id}:{days_left}"
    )

    send_asset_notification(
        recipient_id=recipient_id,
        title=title,
        message=message,
        reference_type=reference_type,
        reference_id=reference_id,
        dedupe_key=dedupe_key,
    )


def get_asset_manager_id(asset):
    """
    TODO: Integrate with your existing user, role,
    department, and permission management.
    """
    return None


def get_department_manager_id(department_id):
    """
    TODO: Retrieve the authorized manager from
    your existing HR and organizational hierarchy.
    """
    return None


def run_asset_alerts():
    today = date.today()

    # 1. Insurance expiry alerts
    for policy in AssetInsurance.query.filter(
        AssetInsurance.status == "Active",
        AssetInsurance.expiry_date.isnot(None),
    ).all():
        asset = Asset.query.get(policy.asset_id)
        if not asset:
            continue
        recipient_id = get_asset_manager_id(asset)
        if recipient_id:
            send_expiry_alert(
                recipient_id=recipient_id,
                item_name=f"Insurance policy for {asset.name}",
                expiry_date=policy.expiry_date,
                reference_type="insurance",
                reference_id=policy.id,
            )

    # 2. Warranty expiry alerts
    for warranty in AssetWarranty.query.filter(
        AssetWarranty.status == "Active",
        AssetWarranty.expiry_date.isnot(None),
    ).all():
        asset = Asset.query.get(warranty.asset_id)
        if not asset:
            continue
        recipient_id = get_asset_manager_id(asset)
        if recipient_id:
            send_expiry_alert(
                recipient_id=recipient_id,
                item_name=f"Warranty for {asset.name}",
                expiry_date=warranty.expiry_date,
                reference_type="warranty",
                reference_id=warranty.id,
            )

    # 3. Asset document expiry alerts
    for document in AssetDocument.query.filter(
        AssetDocument.expiry_date.isnot(None),
        AssetDocument.status == "Active",
    ).all():
        asset = Asset.query.get(document.asset_id)
        if not asset:
            continue
        recipient_id = get_asset_manager_id(asset)
        if recipient_id:
            send_expiry_alert(
                recipient_id=recipient_id,
                item_name=f"Document: {document.document_name}",
                expiry_date=document.expiry_date,
                reference_type="document",
                reference_id=document.id,
            )

    # 4. Pending asset transfers
    for transfer in AssetTransfer.query.filter(
        AssetTransfer.status == "Pending"
    ).all():
        manager_id = get_department_manager_id(
            transfer.to_department_id
        )
        if manager_id:
            send_asset_notification(
                recipient_id=manager_id,
                title="Asset Transfer Awaiting Approval",
                message=(
                    f"Asset transfer #{transfer.id} "
                    "requires your review."
                ),
                reference_type="asset_transfer",
                reference_id=transfer.id,
                dedupe_key=f"asset-transfer-pending:{transfer.id}",
            )

    return {
        "status": "completed",
        "message": "Asset alert processing completed.",
    }