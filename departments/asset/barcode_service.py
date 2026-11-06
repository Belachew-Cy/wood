import os
import qrcode

from barcode import Code128
from barcode.writer import ImageWriter

from flask import current_app, url_for


def get_asset_label_directory():
    directory = os.path.join(
        current_app.instance_path,
        "asset_labels"
    )

    os.makedirs(directory, exist_ok=True)

    return directory


def generate_asset_qr(asset):

    directory = get_asset_label_directory()

    filename = f"asset_qr_{asset.id}.png"
    filepath = os.path.join(directory, filename)

    asset_url = url_for(
        "asset_management.view_asset",
        asset_id=asset.id,
        _external=True
    )

    qr = qrcode.QRCode(
        version=2,
        box_size=10,
        border=4
    )

    qr.add_data(asset_url)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    image.save(filepath)

    return filepath


def generate_asset_barcode(asset):

    directory = get_asset_label_directory()

    filename = f"asset_barcode_{asset.id}"

    barcode = Code128(
        asset.barcode or asset.asset_code,
        writer=ImageWriter()
    )

    filepath = barcode.save(
        os.path.join(directory, filename)
    )

    return filepath