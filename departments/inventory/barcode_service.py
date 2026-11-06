import io
import barcode
import qrcode

from barcode.writer import ImageWriter


def generate_barcode_image(value):
    """
    Generate a Code128 barcode image.
    """

    if not value:
        raise ValueError("Barcode value is required.")

    barcode_class = barcode.get_barcode_class("code128")

    barcode_object = barcode_class(
        str(value),
        writer=ImageWriter()
    )

    image_buffer = io.BytesIO()

    barcode_object.write(
        image_buffer,
        options={
            "module_width": 0.3,
            "module_height": 15,
            "quiet_zone": 3,
            "write_text": True,
        }
    )

    image_buffer.seek(0)

    return image_buffer


def generate_qr_image(value):
    """
    Generate a QR code image.
    """

    if not value:
        raise ValueError("QR value is required.")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(value)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    image_buffer = io.BytesIO()

    image.save(image_buffer, format="PNG")

    image_buffer.seek(0)

    return image_buffer