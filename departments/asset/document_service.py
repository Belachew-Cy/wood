import os
import uuid
from werkzeug.utils import secure_filename
from flask import current_app


ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "doc",
    "docx",
    "xls",
    "xlsx"
}

MAX_FILE_SIZE = 10 * 1024 * 1024


def allowed_file(filename):

    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def get_document_directory(asset_id):

    directory = os.path.join(
        current_app.instance_path,
        "asset_documents",
        str(asset_id)
    )

    os.makedirs(directory, exist_ok=True)

    return directory


def save_asset_document(file, asset_id):

    if not file or not file.filename:
        raise ValueError("Please select a document.")

    original_filename = secure_filename(file.filename)

    if not allowed_file(original_filename):
        raise ValueError("Unsupported document format.")

    file_content = file.read(MAX_FILE_SIZE + 1)

    if len(file_content) > MAX_FILE_SIZE:
        raise ValueError("Maximum file size is 10 MB.")

    if not file_content:
        raise ValueError("The selected file is empty.")

    extension = original_filename.rsplit(".", 1)[1].lower()

    stored_filename = (
        f"{uuid.uuid4().hex}.{extension}"
    )

    directory = get_document_directory(asset_id)

    filepath = os.path.join(
        directory,
        stored_filename
    )

    with open(filepath, "wb") as document_file:
        document_file.write(file_content)

    return {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "file_path": filepath,
        "file_extension": extension,
        "file_size": len(file_content)
    }