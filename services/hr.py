"""Employee records, uploads and employee page helpers."""

from core.access import account_role_keys
from core.access import current_user
from core.access import effective_permissions
from core.config import EDUCATION_LEVELS
from core.config import EMPLOYEE_CODE_PREFIX
from core.config import EMPLOYEE_DEPARTMENTS
from core.config import EMPLOYEE_DOCUMENT_TYPES
from core.config import EMPLOYEE_EMAIL_RE
from core.config import EMPLOYEE_FILE_COUNT
from core.config import EMPLOYEE_FILE_EXTENSIONS
from core.config import EMPLOYEE_FILE_LIMIT
from core.config import EMPLOYEE_PHONE_RE
from core.config import EMPLOYEE_PHOTO_EXTENSIONS
from core.config import EMPLOYEE_PHOTO_LIMIT
from core.config import EMPLOYEE_STATUS
from core.config import EMPLOYEE_UPLOAD_DIR
from core.config import EMPLOYMENT_TYPES
from core.db import get_db
from datetime import date
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from flask import render_template
from flask import request
from flask import url_for
from services.recency import stamp as stamp_record
import json
import os
import re
import secrets
import shutil
import sqlite3


EMPLOYEE_DB_FIELDS = (
    "employee_code", "full_name", "gender", "date_of_birth", "phone", "email",
    "address", "emergency_contact_name", "emergency_contact_phone",
    "department", "job_title", "employment_type", "hire_date",
    "employment_status", "manager_id", "work_location", "salary",
    "education_level", "field_of_study", "institution", "graduation_year",
    "certifications", "work_experience", "required_documents",
    "education_documents", "supporting_documents", "notes",
)


def stored_document_list(raw):
    """Read the document metadata an employee carries without trusting its shape."""
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    documents = []
    for item in data:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        documents.append({
            "file": str(item["file"]),
            "name": str(item.get("name") or item["file"])[:160],
            "size": max(size, 0),
            "uploaded": str(item.get("uploaded") or "")[:10],
        })
    return documents


def stored_document_labels(raw):
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item)[:80] for item in data if isinstance(item, str)][:20]


def employee_documents(row):
    return {
        "required": stored_document_labels(row["required_documents"]),
        "education": stored_document_list(row["education_documents"]),
        "supporting": stored_document_list(row["supporting_documents"]),
    }


def next_employee_code(db):
    """Continue the directory's numbering, whichever prefix it already uses."""
    series = {}
    for row in db.execute("""
        SELECT employee_code FROM employees
        WHERE employee_code IS NOT NULL AND employee_code <> ''
    """):
        match = re.fullmatch(r"([A-Za-z]{2,})[-_/ ]?(\d+)",
                             (row["employee_code"] or "").strip())
        if match:
            series.setdefault(match.group(1).upper(), []).append(int(match.group(2)))
    prefix = EMPLOYEE_CODE_PREFIX.upper()
    if prefix not in series and series:
        prefix = max(series, key=lambda key: (len(series[key]), key))
    number = max(series.get(prefix, [0])) + 1
    while True:
        code = "{}-{:03d}".format(prefix, number)
        taken = db.execute(
            "SELECT 1 FROM employees WHERE upper(employee_code) = ?", (code,)
        ).fetchone()
        if taken is None:
            return code
        number += 1


def hr_department_options(db, active_only=False):
    """Every department the HR module has registered, for every dropdown.

    One query every screen shares - the hierarchy screen, the employee form,
    letter routing and the budget - so a department created here shows up in
    each of them the moment it exists, and none of them keeps its own list.
    """
    where = " WHERE is_active = 1" if active_only else ""
    try:
        return db.execute(
            "SELECT * FROM departments%s ORDER BY name COLLATE NOCASE" % where
        ).fetchall()
    except sqlite3.Error:
        return []


def employee_departments(db):
    """The department choices, joining the hierarchy and HR's own past entries."""
    names = list(EMPLOYEE_DEPARTMENTS)
    seen = {name.casefold() for name in names}
    for row in hr_department_options(db):
        name = (row["name"] or "").strip()
        if name and name.casefold() not in seen:
            names.append(name)
            seen.add(name.casefold())
    for row in db.execute("""
        SELECT DISTINCT department FROM employees
        WHERE department IS NOT NULL AND department <> ''
        ORDER BY department
    """):
        name = (row["department"] or "").strip()
        if name and name.casefold() not in seen:
            names.append(name)
            seen.add(name.casefold())
    return names


def can_view_salary(user):
    """Salary is payroll data, not directory data."""
    if user is None:
        return False
    return user["role"] == "admin" or "payroll" in effective_permissions(user)


def can_view_documents(user):
    """Personal documents stay with HR itself and the administrator."""
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    return ("hr" in account_role_keys(get_db(), user)
            and "hr" in effective_permissions(user))


def employee_phone_problem(phone):
    digits = re.sub(r"\D", "", phone or "")
    if not (7 <= len(digits) <= 15) or EMPLOYEE_PHONE_RE.fullmatch(phone or "") is None:
        return "Enter a valid phone number with 7 to 15 digits."
    return None


def employee_manager_problem(db, manager_id, employee_id=None):
    if manager_id is None:
        return None
    if employee_id is not None and manager_id == employee_id:
        return "An employee cannot report to themselves."
    seen = {employee_id} if employee_id is not None else set()
    current = manager_id
    while current is not None:
        if current in seen:
            return "That reporting line would create a cycle."
        seen.add(current)
        parent = db.execute(
            "SELECT manager_id FROM employees WHERE id = ?", (current,)
        ).fetchone()
        if parent is None:
            if current == manager_id:
                return "Choose a reporting manager from the directory."
            break
        current = parent["manager_id"]
    return None


FILE_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "webp": (b"RIFF",),
    "pdf": (b"%PDF-",),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "docx": (b"PK\x03\x04",),
}


def upload_signature_ok(handle, extension):
    """Refuse a file whose first bytes contradict the extension it was given."""
    try:
        head = handle.stream.read(16)
        handle.stream.seek(0)
    except (OSError, ValueError):
        return False
    if extension == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(signature)
               for signature in FILE_SIGNATURES.get(extension, ()))


def employee_upload_dir(employee_id):
    return EMPLOYEE_UPLOAD_DIR / str(employee_id)


def employee_display_name(filename):
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:120] or "document"


def upload_size(handle):
    handle.stream.seek(0, os.SEEK_END)
    size = handle.stream.tell()
    handle.stream.seek(0)
    return size


def empty_employee_uploads():
    return {
        "photo": None, "photo_ext": "", "photo_name": "",
        "education": [], "supporting": [],
        "remove_photo": False,
        "remove_education": set(), "remove_supporting": set(),
        "errors": [], "names": [],
    }


def read_employee_uploads(files, allow_documents=True):
    """Validate every selected file without writing it to disk yet."""
    uploads = empty_employee_uploads()
    uploads["remove_photo"] = request.form.get("remove_photo") == "1"
    if allow_documents:
        uploads["remove_education"] = {
            employee_display_name(value)
            for value in request.form.getlist("remove_education") if value
        }
        uploads["remove_supporting"] = {
            employee_display_name(value)
            for value in request.form.getlist("remove_supporting") if value
        }

    photo = files.get("profile_photo")
    if photo is not None and photo.filename:
        name = employee_display_name(photo.filename)
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in EMPLOYEE_PHOTO_EXTENSIONS:
            uploads["errors"].append("Profile photo must be a PNG, JPG or WebP image.")
        elif upload_size(photo) > EMPLOYEE_PHOTO_LIMIT:
            uploads["errors"].append("Profile photo must be 3 MB or smaller.")
        elif upload_size(photo) == 0:
            uploads["errors"].append("The selected profile photo is empty.")
        elif not upload_signature_ok(photo, extension):
            uploads["errors"].append(
                "That file is not a real {} image.".format(extension.upper()))
        else:
            uploads["photo"] = photo
            uploads["photo_ext"] = "jpg" if extension == "jpeg" else extension
            uploads["photo_name"] = name
            uploads["names"].append(name)

    if not allow_documents:
        return uploads

    for field, bucket, label in (
        ("education_documents", "education", "education document"),
        ("supporting_documents", "supporting", "supporting document"),
    ):
        batch = [item for item in files.getlist(field)
                 if item is not None and item.filename]
        if len(batch) > EMPLOYEE_FILE_COUNT:
            uploads["errors"].append(
                "Attach at most {} {}s at a time.".format(EMPLOYEE_FILE_COUNT, label))
            batch = batch[:EMPLOYEE_FILE_COUNT]
        for item in batch:
            name = employee_display_name(item.filename)
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if extension not in EMPLOYEE_FILE_EXTENSIONS:
                uploads["errors"].append(
                    "{} is not an accepted {} type.".format(name, label))
                continue
            size = upload_size(item)
            if size > EMPLOYEE_FILE_LIMIT:
                uploads["errors"].append("{} is larger than 5 MB.".format(name))
                continue
            if size == 0:
                uploads["errors"].append("{} is empty.".format(name))
                continue
            if not upload_signature_ok(item, extension):
                uploads["errors"].append(
                    "{} does not match its file type.".format(name))
                continue
            uploads[bucket].append({"file": item, "ext": extension,
                                    "name": name, "size": size})
            uploads["names"].append(name)
    return uploads


def read_employee_form(db, employee=None, salary_allowed=True, documents_allowed=True):
    """Read and validate the staff form; return (values, errors, uploads)."""
    form = request.form
    errors = []
    employee_id = employee["id"] if employee is not None else None

    def text(name, label, limit, required=False):
        value = (form.get(name) or "").strip()
        if required and not value:
            errors.append("{} is required.".format(label))
        if len(value) > limit:
            errors.append("{} must be {} characters or fewer.".format(label, limit))
            value = value[:limit]
        return value

    def read_date(name, label, required=False):
        raw = (form.get(name) or "").strip()
        if not raw:
            if required:
                errors.append("{} is required.".format(label))
            return None
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            errors.append("{} must be a real date in YYYY-MM-DD form.".format(label))
            return raw

    employee_code = text("employee_code", "Employee ID", 30).upper()
    if not employee_code:
        employee_code = (employee["employee_code"] if employee is not None
                         else next_employee_code(db))
    elif re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{1,29}", employee_code) is None:
        errors.append("Employee ID may use letters, digits, dots, slashes,"
                      " hyphens and underscores.")
    clash = db.execute(
        "SELECT full_name FROM employees WHERE upper(employee_code) = ? AND id <> ?",
        (employee_code, employee_id if employee_id is not None else -1)
    ).fetchone()
    if clash is not None and employee_code:
        errors.append("Employee ID {} is already used by {}.".format(
            employee_code, clash["full_name"]))

    full_name = text("full_name", "Full name", 120, required=True)
    gender = text("gender", "Gender", 20)
    if gender and gender not in ("Male", "Female", "Other"):
        errors.append("Choose Male, Female or Other for gender.")
        gender = ""

    date_of_birth = read_date("date_of_birth", "Date of birth")
    if date_of_birth:
        try:
            born = date.fromisoformat(date_of_birth)
        except ValueError:
            born = None
        if born is not None:
            if born > date.today():
                errors.append("Date of birth cannot be in the future.")
            elif born < date(1900, 1, 1):
                errors.append("Date of birth looks too early; check the year.")

    phone = text("phone", "Phone number", 30, required=True)
    phone_problem = employee_phone_problem(phone) if phone else None
    if phone_problem:
        errors.append(phone_problem)

    email = text("email", "Email", 254).lower()
    if email and EMPLOYEE_EMAIL_RE.fullmatch(email) is None:
        errors.append("Enter a valid email address, for example name@example.com.")
    if email:
        held = db.execute(
            "SELECT employee_code FROM employees WHERE lower(email) = ? AND id <> ?",
            (email, employee_id if employee_id is not None else -1)
        ).fetchone()
        if held is not None:
            errors.append("Email {} is already on the record for {}.".format(
                email, held["employee_code"]))

    address = text("address", "Address", 300)
    emergency_name = text("emergency_contact_name", "Emergency contact name", 120)
    emergency_phone = text("emergency_contact_phone", "Emergency contact phone", 30)
    if emergency_phone:
        emergency_problem = employee_phone_problem(emergency_phone)
        if emergency_problem:
            errors.append("Emergency contact: {}".format(emergency_problem))

    department = text("department", "Department", 80, required=True)
    job_title = text("job_title", "Job title", 100, required=True)

    employment_type = text("employment_type", "Employment type", 30) or "Permanent"
    if employment_type not in EMPLOYMENT_TYPES:
        errors.append("Choose a valid employment type.")
        employment_type = "Permanent"

    hire_date = read_date("hire_date", "Date of hire", required=True)
    if hire_date:
        try:
            hired = date.fromisoformat(hire_date)
        except ValueError:
            hired = None
        if hired is not None:
            if hired > date.today() + timedelta(days=730):
                errors.append("Date of hire is too far in the future.")
            if date_of_birth:
                try:
                    born = date.fromisoformat(date_of_birth)
                except ValueError:
                    born = None
                if born is not None and hired <= born:
                    errors.append("Date of hire must be after the date of birth.")

    status_default = employee["employment_status"] if employee is not None else "Active"
    employment_status = text("employment_status", "Employment status", 20) or status_default
    if employment_status not in EMPLOYEE_STATUS:
        errors.append("Choose Active, On Leave or Inactive for employment status.")
        employment_status = status_default

    manager_raw = (form.get("manager_id") or "").strip()
    manager_id = None
    if manager_raw:
        try:
            manager_id = int(manager_raw)
        except ValueError:
            errors.append("Choose a reporting manager from the directory.")
        else:
            manager_problem = employee_manager_problem(db, manager_id, employee_id)
            if manager_problem:
                errors.append(manager_problem)

    work_location = text("work_location", "Work location", 120)
    salary = float(employee["salary"] or 0) if employee is not None else 0.0
    if salary_allowed and "salary" in form:
        raw_salary = (form.get("salary") or "").strip()
        if not raw_salary:
            salary = 0.0
        else:
            try:
                amount = Decimal(raw_salary)
            except (InvalidOperation, ValueError):
                errors.append("Enter a valid monthly salary.")
                amount = None
            if amount is not None:
                if not amount.is_finite() or amount < 0:
                    errors.append("Monthly salary cannot be negative.")
                elif amount > Decimal("1000000000"):
                    errors.append("Monthly salary is larger than this system accepts.")
                else:
                    salary = float(amount)

    education_level = text("education_level", "Highest education", 40)
    if education_level and education_level not in EDUCATION_LEVELS:
        errors.append("Choose a valid highest education level.")
        education_level = ""
    field_of_study = text("field_of_study", "Field of study", 120)
    institution = text("institution", "Institution", 160)
    graduation_raw = (form.get("graduation_year") or "").strip()
    graduation_year = None
    if graduation_raw:
        try:
            graduation_year = int(graduation_raw)
        except ValueError:
            errors.append("Graduation year must be a whole year.")
        else:
            if not 1950 <= graduation_year <= date.today().year + 1:
                errors.append("Graduation year must be between 1950 and {}.".format(
                    date.today().year + 1))
    certifications = text("certifications", "Professional certifications", 500)
    work_experience = text("work_experience", "Relevant work experience", 1000)

    if documents_allowed:
        required_documents = list(dict.fromkeys(
            label for label in form.getlist("required_documents")
            if label in EMPLOYEE_DOCUMENT_TYPES))
    elif employee is not None:
        required_documents = stored_document_labels(employee["required_documents"])
    else:
        required_documents = []

    education_documents = (stored_document_list(employee["education_documents"])
                           if employee is not None else [])
    supporting_documents = (stored_document_list(employee["supporting_documents"])
                            if employee is not None else [])
    notes = text("notes", "Employee notes", 1000)
    uploads = read_employee_uploads(request.files, allow_documents=documents_allowed)
    errors.extend(uploads["errors"])

    if full_name and phone:
        wanted_phone = re.sub(r"\D", "", phone)
        for other in db.execute("""
            SELECT employee_code, full_name, phone FROM employees WHERE id <> ?
        """, (employee_id if employee_id is not None else -1,)):
            if ((other["full_name"] or "").strip().casefold() == full_name.casefold()
                    and re.sub(r"\D", "", other["phone"] or "") == wanted_phone):
                errors.append("{} already has a record for {} ({}).".format(
                    other["full_name"], full_name, other["employee_code"]))
                break

    values = {
        "employee_code": employee_code,
        "full_name": full_name,
        "gender": gender,
        "date_of_birth": date_of_birth,
        "phone": phone,
        "email": email or None,
        "address": address,
        "emergency_contact_name": emergency_name,
        "emergency_contact_phone": emergency_phone,
        "department": department,
        "job_title": job_title,
        "employment_type": employment_type,
        "hire_date": hire_date,
        "employment_status": employment_status,
        "manager_id": manager_id,
        "work_location": work_location,
        "salary": salary,
        "education_level": education_level,
        "field_of_study": field_of_study,
        "institution": institution,
        "graduation_year": graduation_year,
        "certifications": certifications,
        "work_experience": work_experience,
        "required_documents": required_documents,
        "education_documents": education_documents,
        "supporting_documents": supporting_documents,
        "notes": notes,
        "profile_photo": employee["profile_photo"] if employee is not None else "",
    }
    return values, errors, uploads


def employee_form_defaults(db, employee=None):
    if employee is None:
        return {
            "employee_code": next_employee_code(db),
            "full_name": "", "gender": "", "date_of_birth": "", "phone": "",
            "email": "", "address": "", "emergency_contact_name": "",
            "emergency_contact_phone": "", "department": "", "job_title": "",
            "employment_type": "Permanent",
            "hire_date": date.today().isoformat(),
            "employment_status": "Active", "manager_id": None,
            "work_location": "", "salary": 0.0, "education_level": "",
            "field_of_study": "", "institution": "", "graduation_year": "",
            "certifications": "", "work_experience": "",
            "required_documents": [], "education_documents": [],
            "supporting_documents": [], "notes": "", "profile_photo": "",
        }
    data = dict(employee)
    data["date_of_birth"] = employee["date_of_birth"] or ""
    data["hire_date"] = employee["hire_date"] or ""
    data["graduation_year"] = employee["graduation_year"] or ""
    documents = employee_documents(employee)
    data["required_documents"] = documents["required"]
    data["education_documents"] = documents["education"]
    data["supporting_documents"] = documents["supporting"]
    return data


def employee_db_payload(values):
    payload = {field: values.get(field) for field in EMPLOYEE_DB_FIELDS}
    for field in ("required_documents", "education_documents", "supporting_documents"):
        payload[field] = json.dumps(values.get(field) or [], ensure_ascii=False)
    return payload


def create_employee_record(db, values):
    payload = employee_db_payload(values)
    cursor = db.execute(
        "INSERT INTO employees ({}) VALUES ({})".format(
            ", ".join(payload), ", ".join("?" for _ in payload)),
        tuple(payload.values()),
    )
    stamp_record(db, "employees", cursor.lastrowid)
    return cursor.lastrowid


def update_employee_record(db, employee_id, values):
    payload = employee_db_payload(values)
    db.execute(
        "UPDATE employees SET {} WHERE id = ?".format(
            ", ".join("{0} = ?".format(field) for field in payload)),
        (*payload.values(), employee_id),
    )
    stamp_record(db, "employees", employee_id)


def discard_employee_file(employee_id, stored):
    path = employee_upload_dir(employee_id) / employee_display_name(stored)
    try:
        path.unlink()
    except OSError:
        pass


def apply_employee_uploads(db, employee_id, values, uploads, previous=None, errors=None):
    """Move the validated files into the record's private folder and save the lists."""
    errors = errors if errors is not None else []
    folder = employee_upload_dir(employee_id)

    for category, key in (("education", "education_documents"),
                          ("supporting", "supporting_documents")):
        removed = uploads.get("remove_" + category) or set()
        kept = []
        for document in values[key]:
            if document["file"] in removed:
                discard_employee_file(employee_id, document["file"])
            else:
                kept.append(document)
        values[key] = kept

    for category, key in (("education", "education_documents"),
                          ("supporting", "supporting_documents")):
        for pending in uploads.get(category) or []:
            folder.mkdir(parents=True, exist_ok=True)
            stored = "{}-{}.{}".format(category, secrets.token_hex(8), pending["ext"])
            try:
                pending["file"].save(folder / stored)
            except OSError:
                errors.append("Could not store {}; attach it again.".format(pending["name"]))
                continue
            values[key].append({
                "file": stored,
                "name": pending["name"],
                "size": pending["size"],
                "uploaded": date.today().isoformat(),
            })

    previous_photo = (previous["profile_photo"] if previous is not None else "") or ""
    if uploads.get("photo") is not None:
        folder.mkdir(parents=True, exist_ok=True)
        stored = "photo-{}.{}".format(secrets.token_hex(8), uploads["photo_ext"])
        try:
            uploads["photo"].save(folder / stored)
        except OSError:
            errors.append("Could not store the profile photo; attach it again.")
        else:
            if previous_photo:
                discard_employee_file(employee_id, previous_photo)
            values["profile_photo"] = stored
    elif uploads.get("remove_photo"):
        if previous_photo:
            discard_employee_file(employee_id, previous_photo)
        values["profile_photo"] = ""
    else:
        values["profile_photo"] = previous_photo

    db.execute("""
        UPDATE employees
        SET profile_photo = ?, education_documents = ?, supporting_documents = ?
        WHERE id = ?
    """, (
        values["profile_photo"],
        json.dumps(values["education_documents"], ensure_ascii=False),
        json.dumps(values["supporting_documents"], ensure_ascii=False),
        employee_id,
    ))


def delete_employee_files(employee_id):
    shutil.rmtree(employee_upload_dir(employee_id), ignore_errors=True)


def employee_photo_url(row):
    photo = (row["profile_photo"] or "").strip()
    if not photo:
        return ""
    if photo.startswith(("http://", "https://", "/")):
        return photo
    return url_for("employee_photo", employee_id=row["id"])


def employee_page_records(db, salary_allowed, documents_allowed):
    names = {
        row["id"]: row["full_name"]
        for row in db.execute("SELECT id, full_name FROM employees")
    }
    records = []
    for row in db.execute("""
        SELECT *, job_title AS position FROM employees
        ORDER BY employee_code COLLATE NOCASE
    """):
        documents = employee_documents(row) if documents_allowed else {
            "required": [], "education": [], "supporting": []}
        item = dict(row)
        item["documents"] = documents
        item["document_total"] = (len(documents["education"])
                                  + len(documents["supporting"]))
        item["manager_name"] = names.get(row["manager_id"], "")
        item["photo_url"] = employee_photo_url(row)
        item["initials"] = "".join(
            part[0] for part in (row["full_name"] or "").split()[:2]).upper() or "?"
        if not salary_allowed:
            item["salary"] = None
        records.append(item)
    return records


def render_employee_page(db, editing=None, form_data=None, errors=None,
                         uploads=None, hiring_from=None):
    user = current_user()
    salary_allowed = can_view_salary(user)
    documents_allowed = can_view_documents(user)
    records = employee_page_records(db, salary_allowed, documents_allowed)
    if form_data is None:
        form_data = employee_form_defaults(db, editing)
    if not salary_allowed:
        form_data["salary"] = 0
    managers = db.execute("""
        SELECT id, full_name, job_title, department FROM employees
        WHERE employment_status <> 'Inactive' ORDER BY full_name
    """).fetchall()
    if editing is not None:
        managers = [row for row in managers if row["id"] != editing["id"]]
    photo_url = ""
    if form_data.get("profile_photo") and editing is not None:
        photo_url = employee_photo_url(editing)
    pending = (uploads or {}).get("names") or []
    return render_template(
        "HR/employees.html",
        records=records,
        employees=records,
        editing=editing,
        form_data=form_data,
        errors=errors or [],
        hiring_from=hiring_from,
        departments=employee_departments(db),
        managers=managers,
        can_view_salary=salary_allowed,
        can_view_documents=documents_allowed,
        statuses=EMPLOYEE_STATUS,
        employment_types=EMPLOYMENT_TYPES,
        education_levels=EDUCATION_LEVELS,
        document_types=EMPLOYEE_DOCUMENT_TYPES,
        today=date.today().isoformat(),
        current_year=date.today().year,
        photo_url=photo_url,
        pending_uploads=pending,
    )
