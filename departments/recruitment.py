"""Vacancies, applicants, examinations and interviews.

Recruitment is the hiring arm of the HR module: a department head raises a request,
the approvals desk answers it, and a selected candidate is registered as staff from
the same file - the old /admin/hr/recruitment screen now redirects here. A candidate
is one row in job_applications for the whole pipeline, so an examination score, an
interview recommendation and the hire all answer to the same record. Nothing here
creates a second set of departments, users or approvals, and no screen trusts a
hidden button: every view asks the server what this account may reach and move
before it reads a record, so the direct URL, the POST and the export are refused in
the same breath as the link.
"""

from core.access import current_user
from core.access import login_required
from core.config import APPLICATION_SOURCES
from core.config import APPLICATION_STATUSES
from core.config import EXAM_STATUSES
from core.config import INTERVIEW_OUTCOMES
from core.config import RECRUITMENT_REVIEW_LEVEL
from core.config import STEP_LABEL
from core.config import VACANCY_JOB_TYPES
from core.config import VACANCY_STATUSES
from core.db import get_db
from datetime import datetime
from flask import Blueprint
from flask import Response
from flask import abort
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import send_file
from flask import url_for
import csv
import io
from services.audit import log_activity
from services.budget import budget_active_allocations
from services.budget import budget_problem_for
from services.links import linked_approval
from services.links import request_chain
from services.notifications import deliver_notification
from services.notifications import notify_permission
from services.recency import stamp as stamp_record
from services.recruitment import INTERVIEW_RECOMMENDATIONS
from services.recruitment import INTERVIEW_SCORE_MAX
from services.recruitment import age_years
from services.recruitment import attempt_for_link
from services.recruitment import can_review_vacancy
from services.recruitment import clean_job_type
from services.recruitment import clean_source
from services.recruitment import cv_path
from services.recruitment import department_row
from services.recruitment import eligibility_problems
from services.recruitment import exam_ranking
from services.recruitment import exam_window_problem
from services.recruitment import grade_attempt
from services.recruitment import interview_rollup
from services.recruitment import invite_panel
from services.recruitment import is_panel_member
from services.recruitment import issue_exam_link
from services.recruitment import new_reference
from services.recruitment import open_vacancy_request
from services.recruitment import parse_moment
from services.recruitment import pipeline
from services.recruitment import public_board_clause
from services.recruitment import record_interview_score
from services.recruitment import recruitment_scope
from services.recruitment import right_label
from services.recruitment import revoke_exam_link
from services.recruitment import save_cv
from services.recruitment import seconds_left
from services.recruitment import start_attempt
from services.recruitment import sync_hired_count
from services.recruitment import vacancy_clause
from services.workflow import approval_acted_ids
from services.workflow import can_decide_request
from services.workflow import can_view_request
from services.workflow import chain_trail


recruitment_bp = Blueprint("recruitment", __name__, url_prefix="/recruitment")

# Only the HR desk turns a shortlist into a hiring decision: the employee and payroll
# screens read that stage as the company's answer about a person.
FINAL_STAGES = ("Selected", "Hired")

# A vacancy read by every screen, with its own pipeline and the chain it travels. One
# query keeps the list, the detail page and the export answering with the same figures.
VACANCY_SELECT = """
    SELECT v.*, d.name AS registered_department,
           (SELECT COUNT(*) FROM job_applications a
            WHERE a.vacancy_id = v.id) AS applicant_count,
           (SELECT COUNT(*) FROM job_applications a
            WHERE a.vacancy_id = v.id AND a.status = 'Hired') AS filled_count,
           ar.id AS approval_id, ar.status AS approval_status,
           ar.current_approver_step AS approval_step
    FROM job_vacancies v
    LEFT JOIN departments d ON d.id = v.department_id
    LEFT JOIN approval_requests ar ON ar.source_table = 'job_vacancies'
        AND ar.source_id = v.id
"""

# The same join for candidates, with the post's own figures alongside.
APPLICATION_SELECT = """
    SELECT a.*, v.job_title, v.department, v.status AS vacancy_status,
           v.reference AS vacancy_reference, v.internal_only, v.openings,
           v.job_type, v.location, v.id AS vacancy_id,
           e.id AS exam_id, e.exam_title, e.total_marks, e.pass_mark,
           e.status AS exam_status, e.duration_minutes,
           at.id AS attempt_id, at.score AS exam_score,
           at.status AS attempt_status, at.submitted_at AS exam_submitted_at,
           emp.employee_code AS employee_code
    FROM job_applications a
    JOIN job_vacancies v ON v.id = a.vacancy_id
    LEFT JOIN recruitment_exams e ON e.id = (
        SELECT x.id FROM recruitment_exams x
        WHERE x.vacancy_id = v.id AND x.status IN ('Published', 'Closed')
        ORDER BY CASE WHEN x.status = 'Published' THEN 0 ELSE 1 END, x.id DESC
        LIMIT 1)
    LEFT JOIN exam_attempts at ON at.exam_id = e.id AND at.application_id = a.id
    LEFT JOIN employees emp ON emp.id = a.employee_id
"""


@recruitment_bp.context_processor
def recruitment_template_context():
    """The vocabulary every form offers and the reach the buttons are drawn from.

    Templates read rec_scope to decide what to show; the same object decides what the
    server accepts, so a stale page cannot smuggle an action past its own desk.
    """
    return {
        "vacancy_statuses": VACANCY_STATUSES,
        "vacancy_job_types": VACANCY_JOB_TYPES,
        "application_statuses": APPLICATION_STATUSES,
        "application_sources": APPLICATION_SOURCES,
        "exam_statuses": EXAM_STATUSES,
        "interview_outcomes": INTERVIEW_OUTCOMES,
        "interview_recommendations": INTERVIEW_RECOMMENDATIONS,
        "interview_score_max": INTERVIEW_SCORE_MAX,
        "step_label": STEP_LABEL,
        "rec_scope": recruitment_scope(get_db(), current_user()),
    }


# ---------------------------------------------------------------- refusal
def deny(reason, record_type=None, record_id=None):
    """Log a refusal, then raise the 403 a hidden button was never meant to be.

    The row names the endpoint, the path and the reason, so an administrator reading
    the trail can tell a stale page apart from somebody walking through identifiers.
    """
    log_activity("DENY", "hr", "Refused {} on {}".format(request.method, request.path),
                 outcome="DENIED", record_type=record_type, record_id=record_id,
                 details={"endpoint": request.endpoint, "reason": reason})
    abort(403)


def desk_scope(entry_right="vacancy_view"):
    """This account's reach, with the desk screens closed on everyone else.

    Two questions are answered here: whether the account holds the right that opens
    the desk at all, and whether it sits at a level with any rows to look at. A staff
    account browsing the published board, and an account whose hiring rights were all
    withdrawn, are both refused before a record is read.
    """
    scope = recruitment_scope(get_db(), current_user())
    if scope is None:
        abort(403)
    if not scope["rights"]:
        deny("No hiring rights on this account", "recruitment_scope")
    if not scope["can"](entry_right):
        deny("The {} right is needed for the hiring screens"
             .format(right_label(entry_right)), "recruitment_scope")
    if scope["level"] == "board":
        deny("A desk seat is needed for the recruitment screens", "recruitment_scope")
    return scope


def require_right(scope, right, vacancy=None):
    """Refuse an action the account does not hold, before the row is touched.

    Passing the vacancy asks the department boundary as well, so a manager holding the
    right still fails on another department's post; leaving it None gates the action
    alone, which is what a list or a screen with no row yet needs.
    """
    if not scope["can"](right, vacancy):
        deny("The {} right is needed".format(right_label(right)),
             "job_vacancy" if vacancy is not None else "recruitment_scope",
             vacancy["id"] if vacancy is not None else None)


def visible_vacancy(db, scope, vacancy_id):
    """The vacancy this account may open, read through the same clause as a list."""
    clause, params = vacancy_clause(scope, "v")
    row = db.execute("{} WHERE v.id = ? AND ({})".format(VACANCY_SELECT, clause),
                     (vacancy_id,) + tuple(params)).fetchone()
    if row is None:
        deny("Vacancy #{} is outside this account's reach".format(vacancy_id),
             "job_vacancy", vacancy_id)
    return row


def bare_vacancy(db, vacancy_id):
    """A vacancy read for a write, so the guard sees exactly the stored row."""
    row = db.execute("SELECT * FROM job_vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def guarded_application(db, scope, application_id):
    """The candidate plus the post it belongs to, both inside this account's reach."""
    application = db.execute("SELECT * FROM job_applications WHERE id = ?",
                             (application_id,)).fetchone()
    if application is None:
        abort(404)
    return application, visible_vacancy(db, scope, application["vacancy_id"])


def editable_vacancy(vacancy, chain):
    """A post is changed while it is a draft, or once a desk sent it back.

    From the moment a chain is waiting on a signature the title, the openings and the
    cost are frozen, which is what stops a figure being moved past a desk that already
    signed a different one - the rule the requisition screen uses.
    """
    if vacancy["status"] != "Draft":
        return False
    return chain is None or chain["status"] == "Returned"


def chain_note(vacancy, chain=None):
    """One line that says where a request stands, read off its chain."""
    if chain is None:
        chain = linked_approval(get_db(), "job_vacancies", vacancy["id"])
    if chain is None:
        if vacancy["status"] == "Draft":
            return ("This post is still a draft. Submitting it starts the approval "
                    "route its cost earns, and the last desk to sign publishes it.")
        return ""
    if chain["status"] == "Pending":
        return ("The route was fixed when this request was submitted, so each desk "
                "signs in turn. The {} desk answers next."
                .format(STEP_LABEL.get(chain["current_approver_step"], "first")))
    if chain["status"] == "Approved":
        return "Every desk signed, so the post is published."
    if chain["status"] == "Rejected":
        return "A desk rejected this request, so the route is closed."
    return "A desk sent this request back for revision; edit the post and submit again."


# ------------------------------------------------------------------- board
def board_vacancies(db, scope):
    """Published posts this account may read, the nearest closing date first.

    A signed-in account is one of the company's own people, so a post reserved for
    staff is theirs to see; a visitor never gets it.
    """
    if scope is None:
        clause, params = public_board_clause(), ()
    elif scope["level"] == "board":
        clause, params = "v.status = 'Open'", ()
    else:
        inner, params = vacancy_clause(scope, "v")
        clause = "({}) AND v.status = 'Open'".format(inner)
    return db.execute("{} WHERE {} ORDER BY"
                      " CASE WHEN v.closing_date IS NULL OR v.closing_date = ''"
                      " THEN 1 ELSE 0 END, v.closing_date, v.id DESC".format(
                          VACANCY_SELECT, clause), params).fetchall()


@recruitment_bp.route("/vacancies")
def vacancies():
    """The board every employee and every visitor reads."""
    db = get_db()
    rows = board_vacancies(db, recruitment_scope(db, current_user()))
    search = request.args.get("search", "").strip()
    if search:
        needle = search.lower()
        rows = [row for row in rows if any(
            needle in (row[column] or "").lower()
            for column in ("job_title", "department", "job_type", "location"))]
    return render_template("recruitment/vacancies.html", vacancies=rows,
                           search=search)


@recruitment_bp.route("/apply/<int:vacancy_id>", methods=["GET", "POST"])
def apply(vacancy_id):
    """One application form, open to a candidate with no account and to staff.

    A signed-in account is linked to its own application, which is how the employee
    reads the screening result back on My applications. An external candidate stays a
    row in the application table only - the account directory belongs to staff.
    """
    db = get_db()
    user = current_user()
    vacancy = bare_vacancy(db, vacancy_id)
    if vacancy["internal_only"] and user is None:
        flash("That vacancy is reserved for the company's own staff.", "error")
        return redirect(url_for("recruitment.vacancies"))
    if vacancy["status"] != "Open":
        flash("That vacancy is not accepting applications.", "error")
        return redirect(url_for("recruitment.vacancies"))

    if request.method == "POST":
        values = {
            "full_name": (request.form.get("full_name") or "").strip()[:120],
            "email": (request.form.get("email") or "").strip().lower()[:160],
            "phone": (request.form.get("phone") or "").strip()[:30],
            "date_of_birth": (request.form.get("date_of_birth") or "").strip()[:10],
            "gpa": (request.form.get("gpa") or "").strip()[:10],
            "education": (request.form.get("education") or "").strip()[:200],
            "experience": (request.form.get("experience") or "").strip()[:2000],
            "cover_letter": (request.form.get("cover_letter") or "").strip()[:4000],
        }
        uploaded = request.files.get("cv")
        problems = []
        if not values["full_name"] or not values["email"] or not values["phone"]:
            problems.append("Name, email and phone number are required.")
        elif "@" not in values["email"]:
            problems.append("Enter the email address the result should go to.")
        problems += eligibility_problems(db, vacancy, values)
        if problems:
            for problem in problems:
                flash(problem, "error")
            return render_template("recruitment/apply.html", vacancy=vacancy,
                                   values=values)
        try:
            reference = new_reference(db, "job_applications", "APP")
            result = db.execute("""
                INSERT INTO job_applications (
                    reference, vacancy_id, user_id, full_name, email, phone,
                    date_of_birth, age, gpa, source, education, experience,
                    resume_filename, cover_letter, status, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'Applied', ?)
            """, (reference, vacancy_id,
                  user["id"] if user is not None else None,
                  values["full_name"], values["email"], values["phone"],
                  values["date_of_birth"] or None, age_years(values["date_of_birth"]),
                  float(values["gpa"]) if values["gpa"] else None,
                  clean_source("Internal" if user is not None else "Portal"),
                  values["education"], values["experience"], values["cover_letter"],
                  datetime.now().isoformat(timespec="seconds")))
            application_id = result.lastrowid
            if uploaded is not None and uploaded.filename:
                # The file is written once the row exists, so a refused insert cannot
                # leave an orphan CV sitting in the uploads directory.
                stored, problem = save_cv(uploaded, application_id)
                if problem:
                    db.rollback()
                    flash(problem, "error")
                    return render_template("recruitment/apply.html", vacancy=vacancy,
                                           values=values)
                db.execute("UPDATE job_applications SET resume_filename = ?"
                           " WHERE id = ?", (stored, application_id))
            db.commit()
        except Exception:
            db.rollback()
            raise

        log_activity("CREATE", "hr",
                     "Application {} received for {} ({})".format(
                         reference, vacancy["job_title"], vacancy["department"]),
                     record_type="job_application", record_id=application_id,
                     details={"vacancy_id": vacancy_id})
        if vacancy["created_by_id"] and vacancy["created_by_id"] != (
                user["id"] if user is not None else None):
            deliver_notification(
                db, vacancy["created_by_id"],
                "New applicant: {}".format(values["full_name"]),
                "{} applied for {}.".format(values["email"], vacancy["job_title"]),
                url_for("recruitment.applicant_detail", application_id=application_id),
                category="recruitment", icon="user", tone="info",
                dedupe_key="application-{}".format(application_id))
            db.commit()
        flash("Your application was received. Quote reference {} if you contact us."
              .format(reference), "success")
        if user is not None:
            return redirect(url_for("recruitment.my_applications"))
        return redirect(url_for("recruitment.vacancies"))

    values = ({"full_name": user["full_name"], "email": user["email"],
               "phone": user["phone"]} if user is not None else {})
    return render_template("recruitment/apply.html", vacancy=vacancy, values=values)


@recruitment_bp.route("/my/applications")
@login_required()
def my_applications():
    """Every post this account applied for, with its stage and its exam score."""
    db = get_db()
    user = current_user()
    rows = db.execute("{} WHERE a.user_id = ? OR LOWER(a.email) = LOWER(?)"
                      " ORDER BY a.applied_at DESC, a.id DESC".format(APPLICATION_SELECT),
                      (user["id"], user["email"])).fetchall()
    return render_template("recruitment/my_applications.html", applications=rows)


@recruitment_bp.route("/my/requests")
@login_required()
def my_requests():
    """The recruitment requests this account raised, and where each one stands."""
    db = get_db()
    rows = db.execute("{} WHERE v.created_by_id = ? ORDER BY v.id DESC".format(
        VACANCY_SELECT), (current_user()["id"],)).fetchall()
    scope = recruitment_scope(db, current_user())
    return render_template("recruitment/my_requests.html", vacancies=rows,
                           can_request=scope["can_request"],
                           can_register=scope["level"] != "board")


# ---------------------------------------------------------------- desk: posts
def vacancy_form(db, form, scope, current=None):
    """Read and validate the post capture, returning (values, problems).

    The department has to be one the organisation already registers: a vacancy is
    where a hiring need is stated, not where a new branch of the company is invented,
    and a misspelling here would split a department in two across every report.
    """
    problems = []
    title = (form.get("job_title") or "").strip()[:120]
    if not title:
        problems.append("Enter the job title.")
    wanted = (form.get("department") or "").strip()
    department = department_row(db, wanted, None)
    if department is None and current and (current["department"] or "").strip().lower() \
            == wanted.lower():
        department = {"id": current["department_id"], "name": current["department"]}
    if department is None:
        problems.append("Choose a department the organisation already registers.")
    elif not scope["can_manage"] and not can_request_for_department(scope, department):
        problems.append("Only the {} desk can raise a request for that department."
                        .format(scope["department"] or "your"))
    try:
        openings = int(form.get("openings") or 0)
    except (TypeError, ValueError):
        openings = 0
    if openings < 1 or openings > 50:
        problems.append("Enter the number of openings between 1 and 50.")

    def number(field, low, high, label):
        raw = (form.get(field) or "").strip()
        if not raw:
            return 0.0, None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0, "Enter {} as a number.".format(label)
        if not low <= value <= high:
            return 0.0, "{} has to be between {} and {}.".format(label, low, high)
        return value, None

    gpa, problem = number("minimum_gpa", 0, 7, "the minimum GPA")
    if problem:
        problems.append(problem)
    minimum_age, problem = number("minimum_age", 0, 70, "the minimum age")
    if problem:
        problems.append(problem)
    maximum_age, problem = number("maximum_age", 0, 80, "the maximum age")
    if problem:
        problems.append(problem)
    if minimum_age and maximum_age and maximum_age < minimum_age:
        problems.append("The maximum age is below the minimum age.")
    cost, problem = number("estimated_cost", 0, 100000000, "the estimated cost")
    if problem:
        problems.append(problem)

    closing = (form.get("closing_date") or "").strip()[:10]
    if closing:
        try:
            datetime.strptime(closing, "%Y-%m-%d")
        except ValueError:
            problems.append("Enter the closing date as YYYY-MM-DD.")

    allocation_id = None
    allocation_value = (form.get("budget_allocation_id") or "").strip()
    if allocation_value:
        if not allocation_value.isdigit():
            problems.append("Choose a valid budget allocation.")
        else:
            allocation_id = int(allocation_value)
            if cost > 0:
                problem = budget_problem_for(
                    db, allocation_id, cost,
                    exclude_table="job_vacancies",
                    exclude_id=current["id"] if current else None)
                if problem:
                    problems.append(problem)
            elif not db.execute("SELECT 1 FROM budget_allocations WHERE id = ?",
                                (allocation_id,)).fetchone():
                problems.append("Choose a valid budget allocation.")

    return {
        "job_title": title,
        "department": department["name"] if department else wanted,
        "department_id": department["id"] if department else None,
        "job_type": clean_job_type(form.get("job_type")),
        "location": (form.get("location") or "").strip()[:80],
        "description": (form.get("description") or "").strip()[:4000],
        "requirements": (form.get("requirements") or "").strip()[:2000],
        "openings": openings,
        "minimum_gpa": gpa,
        "minimum_age": int(minimum_age),
        "maximum_age": int(maximum_age),
        "internal_only": 1 if form.get("internal_only") else 0,
        "estimated_cost": round(cost, 2),
        "budget_allocation_id": allocation_id,
        "closing_date": closing or None,
    }, problems


def can_request_for_department(scope, department):
    """A department seat speaks for its own department and nowhere else."""
    if not department:
        return False
    if scope["department_id"] and department["id"] == scope["department_id"]:
        return True
    wanted = (scope["department"] or "").strip().lower()
    return bool(wanted) and (department["name"] or "").strip().lower() == wanted


def insert_vacancy(db, values, status, user):
    reference = new_reference(db, "job_vacancies", "VAC")
    result = db.execute("""
        INSERT INTO job_vacancies (
            reference, job_title, department, department_id, job_type, location,
            description, requirements, openings, minimum_gpa, minimum_age,
            maximum_age, internal_only, estimated_cost, budget_allocation_id,
            closing_date, status, created_by_id, created_by_name, hired_count,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    """, (reference, values["job_title"], values["department"],
          values["department_id"], values["job_type"], values["location"],
          values["description"], values["requirements"], values["openings"],
          values["minimum_gpa"], values["minimum_age"], values["maximum_age"],
          values["internal_only"], values["estimated_cost"],
          values["budget_allocation_id"], values["closing_date"], status,
          user["id"], user["full_name"],
          datetime.now().isoformat(timespec="seconds")))
    return result.lastrowid


def update_vacancy(db, vacancy, values):
    db.execute("""
        UPDATE job_vacancies SET
            job_title = ?, department = ?, department_id = ?, job_type = ?,
            location = ?, description = ?, requirements = ?, openings = ?,
            minimum_gpa = ?, minimum_age = ?, maximum_age = ?, internal_only = ?,
            estimated_cost = ?, budget_allocation_id = ?, closing_date = ?
        WHERE id = ?
    """, (values["job_title"], values["department"], values["department_id"],
          values["job_type"], values["location"], values["description"],
          values["requirements"], values["openings"], values["minimum_gpa"],
          values["minimum_age"], values["maximum_age"], values["internal_only"],
          values["estimated_cost"], values["budget_allocation_id"],
          values["closing_date"], vacancy["id"]))


def desk_render(db, scope):
    """The one context every recruitment screen needs: its reach and its figures."""
    clause, params = vacancy_clause(scope, "v")
    summary = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN v.status = 'Draft' THEN 1 ELSE 0 END) AS draft,
               SUM(CASE WHEN v.status = 'Open' THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN v.status = 'Closed' THEN 1 ELSE 0 END) AS closed_count
        FROM job_vacancies v WHERE {}
    """.format(clause), params).fetchone()
    return {"scope": scope, "summary": summary,
            "stages": pipeline(db, clause, params),
            "departments": db.execute("SELECT id, name FROM departments"
                                      " WHERE is_active = 1 ORDER BY name").fetchall(),
            "allocations": budget_active_allocations(db),
            "total_posts": int(summary["total"] or 0),
            "open_posts": int(summary["open_count"] or 0),
            "draft_posts": int(summary["draft"] or 0),
            "closed_posts": int(summary["closed_count"] or 0)}


@recruitment_bp.route("/hr/vacancies")
@login_required()
def hr_vacancies():
    """The vacancy and request register for the desks that own these posts."""
    db = get_db()
    scope = desk_scope()
    clause, params = vacancy_clause(scope, "v")
    wanted_status = request.args.get("status", "").strip()
    if wanted_status not in VACANCY_STATUSES:
        wanted_status = ""
    department_filter = request.args.get("department", "").strip()
    extra_sql, extra_params = "", []
    if wanted_status:
        extra_sql += " AND v.status = ?"
        extra_params.append(wanted_status)
    if department_filter:
        extra_sql += " AND (v.department = ? COLLATE NOCASE OR v.department_id = ?)"
        extra_params.extend([department_filter,
                             department_filter if department_filter.isdigit() else 0])
    rows = db.execute("{} WHERE ({}){} ORDER BY"
                      " CASE v.status WHEN 'Draft' THEN 0 WHEN 'Open' THEN 1 ELSE 2 END,"
                      " v.id DESC".format(VACANCY_SELECT, clause, extra_sql),
                      tuple(params) + tuple(extra_params)).fetchall()
    context = desk_render(db, scope)
    return render_template("recruitment/hr_vacancies.html", vacancies=rows,
                           selected_status=wanted_status,
                           selected_department=department_filter, **context)


@recruitment_bp.route("/hr/vacancies/new", methods=["GET", "POST"])
@login_required()
def create_vacancy():
    """Open a post. The HR desk publishes; a department seat raises a draft."""
    db = get_db()
    scope = desk_scope()
    user = current_user()
    if not scope["can_request"]:
        deny("Raising a recruitment request needs a department seat",
             "job_vacancy")
    values, problems = ({}, [])
    if request.method == "POST":
        values, problems = vacancy_form(db, request.form, scope)
        publish = (request.form.get("action") == "publish"
                   and scope["can"]("vacancy_publish"))
        if problems:
            for problem in problems:
                flash(problem, "error")
        else:
            vacancy_id = insert_vacancy(db, values, "Open" if publish else "Draft",
                                        user)
            stamp_record(db, "job_vacancies", vacancy_id, user)
            db.commit()
            log_activity("CREATE", "hr",
                         "{} raised: {} ({})".format(
                             "Vacancy published" if publish else "Recruitment request",
                             values["job_title"], values["department"]),
                         record_type="job_vacancy", record_id=vacancy_id)
            flash("Vacancy {} saved.".format(values["job_title"]), "success")
            return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    return render_template("recruitment/create_vacancy.html", vacancy=None,
                           values=values, **desk_render(db, scope))


@recruitment_bp.route("/hr/vacancy/<int:vacancy_id>/edit", methods=["GET", "POST"])
@login_required()
def edit_vacancy(vacancy_id):
    """Revise a draft, or a post a desk sent back for another look."""
    db = get_db()
    scope = desk_scope()
    vacancy = visible_vacancy(db, scope, vacancy_id)
    chain = linked_approval(db, "job_vacancies", vacancy_id)
    owner = vacancy["created_by_id"] == scope["user_id"]
    if not editable_vacancy(vacancy, chain) or not (
            owner or scope["can"]("vacancy_publish", vacancy)):
        deny("Vacancy #{} is not open for editing".format(vacancy_id),
             "job_vacancy", vacancy_id)
    values, problems = dict(vacancy), []
    if request.method == "POST":
        values, problems = vacancy_form(db, request.form, scope, current=vacancy)
        if problems:
            for problem in problems:
                flash(problem, "error")
        else:
            update_vacancy(db, vacancy, values)
            stamp_record(db, "job_vacancies", vacancy_id, current_user())
            db.commit()
            log_activity("UPDATE", "hr",
                         "Draft of {} revised".format(vacancy["job_title"]),
                         record_type="job_vacancy", record_id=vacancy_id)
            flash("Changes saved.", "success")
            return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    return render_template("recruitment/create_vacancy.html", vacancy=vacancy,
                           values=values, chain=chain,
                           **desk_render(db, scope))


@recruitment_bp.route("/hr/vacancy/<int:vacancy_id>")
@login_required()
def vacancy_detail(vacancy_id):
    """One post: its request, its pipeline and the candidates standing behind it."""
    db = get_db()
    scope = desk_scope()
    vacancy = visible_vacancy(db, scope, vacancy_id)
    applicants = db.execute("{} WHERE a.vacancy_id = ? ORDER BY"
                            " CASE a.status WHEN 'Interview' THEN 0 WHEN 'Shortlisted'"
                            " THEN 1 WHEN 'Applied' THEN 2 ELSE 3 END, a.applied_at DESC"
                            .format(APPLICATION_SELECT), (vacancy_id,)).fetchall()
    exams = db.execute("SELECT e.*, (SELECT COUNT(*) FROM exam_attempts a"
                       " WHERE a.exam_id = e.id) AS attempt_count,"
                       " (SELECT COUNT(*) FROM exam_questions q WHERE q.exam_id = e.id)"
                       " AS question_count FROM recruitment_exams e"
                       " WHERE e.vacancy_id = ? ORDER BY e.id DESC",
                       (vacancy_id,)).fetchall()
    interviews = db.execute("""
        SELECT i.*, j.full_name, j.email,
               (SELECT COUNT(*) FROM interview_scores s WHERE s.interview_id = i.id)
                   AS score_count
        FROM recruitment_interviews i
        JOIN job_applications j ON j.id = i.application_id
        WHERE i.vacancy_id = ? ORDER BY i.id DESC
    """, (vacancy_id,)).fetchall()
    chain = linked_approval(db, "job_vacancies", vacancy_id)
    return render_template(
        "recruitment/vacancy_detail.html", vacancy=vacancy, applicants=applicants,
        exams=exams, interviews=interviews, chain=chain,
        trail=chain_trail(db, chain, request_chain(chain)) if chain else [],
        note=chain_note(vacancy, chain),
        can_review=can_review_vacancy(scope, vacancy),
        can_edit=editable_vacancy(vacancy, chain)
        and (scope["can"]("vacancy_publish", vacancy)
             or vacancy["created_by_id"] == scope["user_id"]),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/vacancy/<int:vacancy_id>/status", methods=["POST"])
@login_required()
def set_vacancy_status(vacancy_id):
    """Publish, close or reopen a post. The HR desk and the administrator only.

    A department seat cannot publish its own need: that is what the approval route is
    for, and the same word on the same button would let a request skip the desks it
    owes a signature to.
    """
    db = get_db()
    scope = desk_scope()
    vacancy = visible_vacancy(db, scope, vacancy_id)
    target = (request.form.get("status") or "").strip()
    if target not in VACANCY_STATUSES:
        flash("That vacancy status is not one the register uses.", "error")
        return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    require_right(scope, "vacancy_publish")
    if target == vacancy["status"]:
        flash("The post is already {}.".format(target.lower()), "error")
        return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    chain = linked_approval(db, "job_vacancies", vacancy_id)
    if target == "Open" and chain is not None and chain["status"] == "Pending":
        flash("The request is still with the {} desk; publishing now would run past it."
              .format(STEP_LABEL.get(chain["current_approver_step"], "approving desk")),
              "error")
        return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    db.execute("UPDATE job_vacancies SET status = ? WHERE id = ?", (target, vacancy_id))
    stamp_record(db, "job_vacancies", vacancy_id, current_user())
    db.commit()
    log_activity("UPDATE", "hr", "Vacancy {} marked {}".format(
        vacancy["reference"] or "#{}".format(vacancy_id), target),
        record_type="job_vacancy", record_id=vacancy_id,
        details={"previous_status": vacancy["status"]})
    if target == "Open" and vacancy["created_by_id"] != scope["user_id"]:
        deliver_notification(
            db, vacancy["created_by_id"],
            "Your post is open: {}".format(vacancy["job_title"]),
            "Applications can be received now.",
            url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id),
            category="recruitment", icon="check", tone="ok",
            dedupe_key="vacancy-{}-open".format(vacancy_id))
        db.commit()
    flash("Vacancy marked {}.".format(target.lower()), "success")
    return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))


@recruitment_bp.route("/hr/vacancy/<int:vacancy_id>/submit", methods=["POST"])
@login_required()
def submit_vacancy_request(vacancy_id):
    """Send a draft through the company's approval route, cost and all.

    The chain is the one the approvals desk already answers, so Finance sees a
    recruitment request in the same queue as a requisition and the budget rules that
    decide who signs are the ones already configured for the company.
    """
    db = get_db()
    scope = desk_scope()
    vacancy = visible_vacancy(db, scope, vacancy_id)
    if not scope["can_request"] or (vacancy["created_by_id"] != scope["user_id"]
                                    and not scope["can"]("vacancy_publish", vacancy)):
        deny("Only the desk that raised vacancy #{} can submit it".format(vacancy_id),
             "job_vacancy", vacancy_id)
    chain = linked_approval(db, "job_vacancies", vacancy_id)
    if vacancy["status"] != "Draft":
        flash("Only a draft post is submitted for approval.", "error")
    elif chain is not None and chain["status"] not in ("Returned", "Rejected"):
        flash("This request is already with the {} desk."
              .format(STEP_LABEL.get(chain["current_approver_step"], "approving desk")),
              "error")
    else:
        if float(vacancy["estimated_cost"] or 0) > 0:
            if not vacancy["budget_allocation_id"]:
                flash("Choose the budget allocation that funds this post before"
                      " submitting.", "error")
                return redirect(url_for("recruitment.vacancy_detail",
                                        vacancy_id=vacancy_id))
            problem = budget_problem_for(db, vacancy["budget_allocation_id"],
                                         float(vacancy["estimated_cost"]),
                                         exclude_table="job_vacancies",
                                         exclude_id=vacancy_id)
            if problem:
                flash(problem, "error")
                return redirect(url_for("recruitment.vacancy_detail",
                                        vacancy_id=vacancy_id))
        request_id = open_vacancy_request(db, vacancy)
        stamp_record(db, "job_vacancies", vacancy_id, current_user())
        db.commit()
        log_activity("SUBMIT", "hr",
                     "Recruitment request for {} ({}) raised chain #{}".format(
                         vacancy["job_title"], vacancy["department"], request_id),
                     record_type="job_vacancy", record_id=vacancy_id,
                     details={"approval_request_id": request_id,
                              "amount": float(vacancy["estimated_cost"] or 0)})
        notify_permission(
            db, "hr", "Recruitment request submitted: {}".format(vacancy["job_title"]),
            "{} ({}) is waiting on its approval route.".format(
                vacancy["openings"], vacancy["department"]),
            url_for("recruitment.finance_desk"), category="recruitment",
            icon="clock", tone="warn", exclude_user_ids=(scope["user_id"],),
            dedupe_prefix="vacancy-{}-submitted".format(vacancy_id))
        db.commit()
        flash("Request submitted. The approvals desk is notified.", "success")
    return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))


# ------------------------------------------------------------- desk: candidates
@recruitment_bp.route("/hr/applicants")
@login_required()
def hr_applicants():
    """Every candidate inside this account's reach, newest first."""
    db = get_db()
    scope = desk_scope("applicant_view")
    clause, params = vacancy_clause(scope, "v")
    wanted_status = request.args.get("status", "").strip()
    if wanted_status not in APPLICATION_STATUSES:
        wanted_status = ""
    extra_sql, extra_params = "", []
    if wanted_status:
        extra_sql += " AND a.status = ?"
        extra_params.append(wanted_status)
    vacancy_filter = (request.args.get("vacancy_id") or "").strip()
    if vacancy_filter.isdigit():
        extra_sql += " AND a.vacancy_id = ?"
        extra_params.append(int(vacancy_filter))
    search = request.args.get("search", "").strip()
    if search:
        pattern = "%{}%".format(search.lower())
        extra_sql += (" AND (LOWER(a.full_name) LIKE ? OR LOWER(a.email) LIKE ?"
                      " OR a.phone LIKE ? OR LOWER(v.job_title) LIKE ?)")
        extra_params.extend([pattern, pattern, pattern, pattern])
    rows = db.execute("{} WHERE ({}){} ORDER BY a.applied_at DESC, a.id DESC".format(
        APPLICATION_SELECT, clause, extra_sql),
        tuple(params) + tuple(extra_params)).fetchall()
    context = desk_render(db, scope)
    posts = db.execute("SELECT v.id, v.job_title, v.department FROM job_vacancies v"
                       " WHERE ({}) ORDER BY v.id DESC".format(clause),
                       params).fetchall()
    return render_template("recruitment/hr_applicants.html", applications=rows,
                           posts=posts, selected_status=wanted_status,
                           selected_vacancy=vacancy_filter, search=search,
                           applied_total=context["stages"]["applications"], **context)


@recruitment_bp.route("/hr/vacancy/<int:vacancy_id>/applicants/new", methods=["POST"])
@login_required()
def register_applicant(vacancy_id):
    """Register a walk-in or referred candidate at the desk.

    The walk-in form the HR screen has always offered moves here, where it runs
    through the same eligibility, duplicate and CV checks as a portal application -
    a person registered by hand must not arrive in the pipeline unchecked.
    """
    db = get_db()
    scope = desk_scope()
    vacancy = visible_vacancy(db, scope, vacancy_id)
    require_right(scope, "applicant_register", vacancy)
    values = {
        "full_name": (request.form.get("full_name") or "").strip()[:120],
        "email": (request.form.get("email") or "").strip().lower()[:160],
        "phone": (request.form.get("phone") or "").strip()[:30],
        "date_of_birth": (request.form.get("date_of_birth") or "").strip()[:10],
        "gpa": (request.form.get("gpa") or "").strip()[:10],
        "education": (request.form.get("education") or "").strip()[:200],
        "experience": (request.form.get("experience") or "").strip()[:2000],
        "cover_letter": (request.form.get("cover_letter") or "").strip()[:4000],
    }
    problems = []
    if not values["full_name"] or not values["email"] or not values["phone"]:
        problems.append("Name, email and phone number are required.")
    elif "@" not in values["email"]:
        problems.append("Enter the candidate's email address.")
    problems += eligibility_problems(db, vacancy, values)
    if problems:
        for problem in problems:
            flash(problem, "error")
        return redirect(url_for("recruitment.vacancy_detail", vacancy_id=vacancy_id))
    user = current_user()
    try:
        reference = new_reference(db, "job_applications", "APP")
        result = db.execute("""
            INSERT INTO job_applications (
                reference, vacancy_id, user_id, full_name, email, phone,
                date_of_birth, age, gpa, source, education, experience,
                resume_filename, cover_letter, status, screened_by_id,
                screened_by_name, screened_at, applied_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'Applied', ?, ?, ?, ?)
        """, (reference, vacancy_id, values["full_name"], values["email"],
              values["phone"], values["date_of_birth"] or None,
              age_years(values["date_of_birth"]),
              float(values["gpa"]) if values["gpa"] else None,
              clean_source(request.form.get("source")),
              values["education"], values["experience"], values["cover_letter"],
              user["id"], user["full_name"],
              datetime.now().isoformat(timespec="seconds"),
              datetime.now().isoformat(timespec="seconds")))
        application_id = result.lastrowid
        uploaded = request.files.get("cv")
        if uploaded is not None and uploaded.filename:
            stored, problem = save_cv(uploaded, application_id)
            if problem:
                db.rollback()
                flash(problem, "error")
                return redirect(url_for("recruitment.vacancy_detail",
                                        vacancy_id=vacancy_id))
            db.execute("UPDATE job_applications SET resume_filename = ?"
                       " WHERE id = ?", (stored, application_id))
        stamp_record(db, "job_applications", application_id, user)
        db.commit()
    except Exception:
        db.rollback()
        raise
    log_activity("CREATE", "hr", "Desk registered applicant {} for {} ({})".format(
        reference, vacancy["job_title"], vacancy["department"]),
        record_type="job_application", record_id=application_id,
        details={"vacancy_id": vacancy_id, "source": "desk"})
    if vacancy["created_by_id"] and vacancy["created_by_id"] != user["id"]:
        deliver_notification(
            db, vacancy["created_by_id"],
            "New applicant: {}".format(values["full_name"]),
            "{} was registered at the desk for {}.".format(
                values["email"], vacancy["job_title"]),
            url_for("recruitment.applicant_detail", application_id=application_id),
            category="recruitment", icon="user", tone="info",
            dedupe_key="application-{}".format(application_id))
        db.commit()
    flash("{} registered as an applicant for this post.".format(values["full_name"]),
          "success")
    return redirect(url_for("recruitment.applicant_detail",
                            application_id=application_id))


@recruitment_bp.route("/hr/applicant/<int:application_id>")
@login_required()
def applicant_detail(application_id):
    """One candidate: the CV, the examination result and the panel's answer."""
    db = get_db()
    scope = desk_scope("applicant_view")
    application, vacancy = guarded_application(db, scope, application_id)
    exam = db.execute("SELECT e.* FROM recruitment_exams e"
                      " JOIN exam_attempts at ON at.exam_id = e.id"
                      " WHERE at.application_id = ? ORDER BY e.id DESC LIMIT 1",
                      (application_id,)).fetchone()
    attempt = db.execute("SELECT * FROM exam_attempts WHERE application_id = ?"
                        " ORDER BY exam_id DESC LIMIT 1",
                        (application_id,)).fetchone()
    return render_template(
        "recruitment/applicant_detail.html", application=application,
        vacancy=vacancy, exam=exam, attempt=attempt,
        interview=interview_rollup(db, application_id),
        chain=linked_approval(db, "job_vacancies", vacancy["id"]),
        has_cv=cv_path(application["resume_filename"]) is not None,
        can_review=can_review_vacancy(scope, vacancy),
        seats=panel_candidates(db),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/applicant/<int:application_id>/status", methods=["POST"])
@login_required()
def set_application_status(application_id):
    """Move one candidate along the pipeline, with the reason recorded.

    Who may do it comes from the post's own department, so a manager shortlists for
    their own vacancies and nowhere else; and the two stages that decide a person's
    future stay with the HR desk.
    """
    db = get_db()
    scope = desk_scope()
    application, vacancy = guarded_application(db, scope, application_id)
    target = (request.form.get("status") or "").strip()
    note = (request.form.get("note") or "").strip()[:1000]
    if target not in APPLICATION_STATUSES:
        flash("That stage is not one the pipeline uses.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    require_right(scope, "applicant_screen", vacancy)
    if target in FINAL_STAGES and not scope["can"]("applicant_hire", vacancy):
        flash("Only the HR desk records a selection or a hire.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    if target == "Rejected" and not note:
        flash("Say why the candidate was not taken forward.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    if target == application["status"]:
        flash("That candidate is already at this stage.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    user = current_user()
    db.execute("UPDATE job_applications SET status = ?, screened_by_id = ?,"
               " screened_by_name = ?, screened_at = ?, decision_note = ?"
               " WHERE id = ?",
               (target, user["id"], user["full_name"],
                datetime.now().isoformat(timespec="seconds"),
                note or application["decision_note"], application_id))
    if target == "Hired":
        sync_hired_count(db, vacancy["id"])
    stamp_record(db, "job_applications", application_id, user)
    db.commit()
    log_activity("UPDATE", "hr", "Candidate {} ({}) moved to {}".format(
        application["reference"] or "#{}".format(application_id),
        application["full_name"], target),
        record_type="job_application", record_id=application_id,
        details={"previous_status": application["status"], "vacancy_id": vacancy["id"]})
    if application["user_id"] and application["user_id"] != user["id"]:
        deliver_notification(
            db, application["user_id"],
            "Application update: {}".format(vacancy["job_title"]),
            "Your application is now at the {} stage.{}".format(
                target, " {}".format(note) if note else ""),
            url_for("recruitment.my_applications"), category="recruitment",
            icon="user", tone="ok" if target in FINAL_STAGES else "info",
            dedupe_key="application-{}-{}".format(application_id, target))
        db.commit()
    flash("Candidate marked {}.".format(target.lower()), "success")
    if target == "Selected" and application["employee_id"] is None:
        flash("Next: register the staff record - the desk button below carries every"
              " detail across without retyping.", "info")
    if target == "Hired" and application["employee_id"] is None:
        # A hire without a staff record is a person the payroll will ask about, so
        # the desk is walked straight to the form, prefilled from this application.
        flash("Finish the hire by registering the staff record.", "info")
        return redirect(url_for("employees", from_application=application_id))
    return redirect(url_for("recruitment.applicant_detail",
                            application_id=application_id))


@recruitment_bp.route("/hr/applicant/<int:application_id>/cv")
@login_required()
def download_cv(application_id):
    """A CV leaves the server only through this door.

    The file sits outside the static folder under a name nobody can guess, so the
    reach check below is the whole access control - a URL typed from another desk's
    screen finds nothing.
    """
    db = get_db()
    scope = desk_scope("applicant_view")
    application, vacancy = guarded_application(db, scope, application_id)
    path = cv_path(application["resume_filename"])
    if path is None:
        abort(404)
    log_activity("READ", "hr", "CV opened for candidate {}".format(
        application["reference"] or "#{}".format(application_id)),
        record_type="job_application", record_id=application_id,
        details={"vacancy_id": vacancy["id"]})
    return send_file(str(path), as_attachment=True,
                     download_name="CV-{}{}".format(
                         application["reference"] or application_id, path.suffix))


@recruitment_bp.route("/hr/applicant/<int:application_id>/exam-link", methods=["POST"])
@login_required()
def exam_link(application_id):
    """Hand a candidate the one URL that opens their sitting, or withdraw it.

    An external applicant gets no account - the users table is the company's staff
    directory - so the link is the credential. Only its digest is stored and it is
    cleared when the paper is handed in, which is why the address can be shown once
    here and nowhere else afterwards.
    """
    db = get_db()
    scope = desk_scope()
    application, vacancy = guarded_application(db, scope, application_id)
    require_right(scope, "applicant_screen", vacancy)
    exam = db.execute("SELECT * FROM recruitment_exams WHERE vacancy_id = ?"
                      " AND status = 'Published' ORDER BY id DESC LIMIT 1",
                      (vacancy["id"],)).fetchone()
    if exam is None:
        flash("Publish the examination for this post first.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    if request.form.get("action") == "revoke":
        attempt = db.execute("SELECT * FROM exam_attempts WHERE exam_id = ?"
                             " AND application_id = ?",
                             (exam["id"], application_id)).fetchone()
        if attempt is not None:
            revoke_exam_link(db, attempt["id"])
            db.commit()
            log_activity("UPDATE", "hr", "Sitting link withdrawn for candidate {}".format(
                application["reference"] or "#{}".format(application_id)),
                record_type="job_application", record_id=application_id)
            flash("The sitting link no longer opens that paper.", "success")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    if application["status"] not in ("Shortlisted", "Interview"):
        flash("Shortlist the candidate before sending an examination.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    attempt = start_attempt(db, exam["id"], application_id)
    token = issue_exam_link(db, attempt)
    stamp_record(db, "job_applications", application_id, current_user())
    db.commit()
    log_activity("UPDATE", "hr", "Sitting link issued for candidate {}".format(
        application["reference"] or "#{}".format(application_id)),
        record_type="job_application", record_id=application_id,
        details={"exam_id": exam["id"]})
    return render_template(
        "recruitment/applicant_detail.html", application=application,
        vacancy=vacancy, exam=exam, attempt=attempt,
        interview=interview_rollup(db, application_id),
        chain=linked_approval(db, "job_vacancies", vacancy["id"]),
        has_cv=cv_path(application["resume_filename"]) is not None,
        can_review=can_review_vacancy(scope, vacancy),
        seats=panel_candidates(db),
        sitting_link=url_for("recruitment.exam_sit", token=token, _external=True),
        **desk_render(db, scope))


# ------------------------------------------------------------- examinations
# The paper belongs to a post, so an examination is never reached around its
# vacancy: every query below joins through job_vacancies and carries the same scope
# clause the vacancy register uses. A seat whose reach stops at its own department
# cannot read another department's key by typing an exam id.
EXAM_SELECT = """
    SELECT e.*, v.job_title, v.department AS vacancy_department,
           v.reference AS vacancy_reference, v.status AS vacancy_status,
           (SELECT COUNT(*) FROM exam_questions q
            WHERE q.exam_id = e.id) AS question_count,
           (SELECT COALESCE(SUM(q.marks), 0) FROM exam_questions q
            WHERE q.exam_id = e.id) AS question_marks,
           (SELECT COUNT(*) FROM exam_attempts a
            WHERE a.exam_id = e.id) AS candidate_count,
           (SELECT COUNT(*) FROM exam_attempts a
            WHERE a.exam_id = e.id AND a.status = 'Submitted') AS submitted_count,
           (SELECT COUNT(*) FROM exam_attempts a
            WHERE a.exam_id = e.id AND a.score >= e.pass_mark) AS passed_count
    FROM recruitment_exams e
    JOIN job_vacancies v ON v.id = e.vacancy_id
"""


def desk_exam(db, scope, exam_id):
    """One examination, reached only through the post it belongs to."""
    clause, params = vacancy_clause(scope, "v")
    row = db.execute("{} WHERE e.id = ? AND ({})".format(EXAM_SELECT, clause),
                     (exam_id,) + tuple(params)).fetchone()
    if row is None:
        deny("Examination #{} sits outside this account's reach".format(exam_id),
             "recruitment_exam", exam_id)
    return row


def exam_questions_of(db, exam_id):
    return db.execute("SELECT * FROM exam_questions WHERE exam_id = ? ORDER BY id",
                      (exam_id,)).fetchall()


def question_marks(db, exam_id):
    return float(db.execute("SELECT COALESCE(SUM(marks), 0) AS marks"
                            " FROM exam_questions WHERE exam_id = ?",
                            (exam_id,)).fetchone()["marks"])


# The bulk sheet carries exactly what the single-question screen asks for, in the
# same order, so a paper written in a spreadsheet reads the same as one typed here.
QUESTION_IMPORT_COLUMNS = ("question", "option_a", "option_b", "option_c",
                          "option_d", "correct_answer", "marks")
QUESTION_IMPORT_MAX_ROWS = 200
QUESTION_IMPORT_MAX_BYTES = 256 * 1024
QUESTION_HEADER_ALIASES = {
    "correct": "correct_answer",
    "answer": "correct_answer",
    "correct answer": "correct_answer",
    "correct option": "correct_answer",
    "right answer": "correct_answer",
    "key": "correct_answer",
    "marks": "marks",
    "score": "marks",
    "point": "marks",
    "points": "marks",
    "text": "question",
    "problem": "question",
}


def question_header_key(name):
    """The column a header cell asks for, matching the single-question field names."""
    cell = " ".join((name or "").strip().lower().replace("-", "_").split())
    if cell in QUESTION_IMPORT_COLUMNS:
        return cell
    key = " ".join(cell.split("_"))
    if key in ("a", "b", "c", "d"):
        return "option_" + key
    if key.startswith("option ") and key.split()[1][:1] in ("a", "b", "c", "d"):
        return "option_" + key.split()[1][:1]
    return QUESTION_HEADER_ALIASES.get(key, "")


def read_question_file(upload):
    """(rows, None) for a readable sheet, else (None, the reason it was refused)."""
    raw = upload.read(QUESTION_IMPORT_MAX_BYTES + 1)
    if not raw:
        return None, "That file is empty."
    if len(raw) > QUESTION_IMPORT_MAX_BYTES:
        return None, "Keep the file under {} KB.".format(QUESTION_IMPORT_MAX_BYTES // 1024)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "Save the file as UTF-8 CSV and upload it again."
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        return None, "That file is empty."
    positions = {}
    for index, name in enumerate(header):
        key = question_header_key(name)
        if key and key not in positions:
            positions[key] = index
    missing = [column for column in ("question", "correct_answer")
               if column not in positions]
    if missing:
        return None, ("The header row must include {}.".format(
            " and ".join(missing)))
    rows = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        rows.append((reader.line_num, {
            column: (row[index].strip() if index < len(row) else "")
            for column, index in positions.items()}))
    if not rows:
        return None, "The file has no questions under its header."
    if len(rows) > QUESTION_IMPORT_MAX_ROWS:
        return None, ("That file has {} questions; upload at most {} at a time."
                      .format(len(rows), QUESTION_IMPORT_MAX_ROWS))
    return rows, None


def question_row_problem(values):
    """Why a sheet row cannot become a question, or None when it can."""
    if not values.get("question"):
        return "No question text."
    if len(values["question"]) > 1000:
        return "The question is longer than 1000 characters."
    for letter in ("a", "b", "c", "d"):
        option = values.get("option_" + letter, "")
        if not option:
            return "Option {} is missing.".format(letter.upper())
        if len(option) > 1000:
            return "Option {} is longer than 1000 characters.".format(letter.upper())
    if values.get("correct_answer", "").strip().upper() not in ("A", "B", "C", "D"):
        return "Mark which option is correct with A, B, C or D."
    try:
        marks = float(values.get("marks") or 0)
    except (TypeError, ValueError):
        return "Marks must be a number."
    if marks <= 0:
        return "The question has to carry marks above zero."
    return None


def manageable_posts(db, scope):
    """The posts an examination may be set against, inside this account's reach."""
    clause, params = vacancy_clause(scope, "v")
    return db.execute("""
        SELECT v.id, v.job_title, v.department, v.status
        FROM job_vacancies v
        WHERE ({}) AND v.status IN ('Open', 'Draft')
        ORDER BY v.id DESC
    """.format(clause), params).fetchall()


@recruitment_bp.route("/hr/exams")
@login_required()
def hr_exams():
    """The examination register for the posts this account owns or reviews."""
    db = get_db()
    scope = desk_scope("exam_view")
    clause, params = vacancy_clause(scope, "v")
    exams = db.execute("{} WHERE ({}) ORDER BY e.id DESC".format(EXAM_SELECT, clause),
                       params).fetchall()
    return render_template("recruitment/hr_exams.html", exams=exams,
                           **desk_render(db, scope))


@recruitment_bp.route("/hr/exams/new", methods=["GET", "POST"])
@login_required()
def create_exam():
    """Set a paper against a post: how long, out of how much, and to pass at."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_author")
    if request.method == "POST":
        vacancy_id = (request.form.get("vacancy_id") or "").strip()
        title = (request.form.get("exam_title") or "").strip()[:200]
        problems = []
        if not title:
            problems.append("Enter the examination title.")
        try:
            duration = int(request.form.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration < 1 or duration > 480:
            problems.append("Enter the duration in minutes between 1 and 480.")
        try:
            total_marks = float(request.form.get("total_marks") or 0)
        except (TypeError, ValueError):
            total_marks = 0.0
        if total_marks <= 0 or total_marks > 1000:
            problems.append("Enter total marks between 1 and 1000.")
        try:
            pass_mark = float(request.form.get("pass_mark") or 0)
        except (TypeError, ValueError):
            pass_mark = -1.0
        if not 0 <= pass_mark <= total_marks:
            problems.append("The pass mark has to sit between zero and the total marks.")
        vacancy = None
        if not problems:
            if not vacancy_id.isdigit():
                problems.append("Choose the vacancy the examination is set for.")
            else:
                vacancy = visible_vacancy(db, scope, int(vacancy_id))
        if problems:
            for problem in problems:
                flash(problem, "error")
            return render_template("recruitment/create_exam.html",
                                   posts=manageable_posts(db, scope),
                                   vacancy_id=vacancy_id,
                                   **desk_render(db, scope))
        result = db.execute("""
            INSERT INTO recruitment_exams (
                vacancy_id, exam_title, description, duration_minutes,
                total_marks, pass_mark, start_datetime, end_datetime,
                status, created_by_id, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Draft', ?, ?)
        """, (vacancy["id"], title,
              (request.form.get("description") or "").strip()[:2000], duration,
              round(total_marks, 2), round(pass_mark, 2),
              (request.form.get("start_datetime") or "").strip()[:19] or None,
              (request.form.get("end_datetime") or "").strip()[:19] or None,
              scope["user_id"], scope["name"]))
        stamp_record(db, "recruitment_exams", result.lastrowid, current_user())
        db.commit()
        log_activity("CREATE", "hr", "Examination {} created for {}".format(
            title, vacancy["job_title"]),
            record_type="recruitment_exam", record_id=result.lastrowid,
            details={"vacancy_id": vacancy["id"]})
        flash("Examination created. Add its questions, then publish it.", "success")
        return redirect(url_for("recruitment.exam_questions", exam_id=result.lastrowid))
    return render_template("recruitment/create_exam.html",
                           posts=manageable_posts(db, scope),
                           **desk_render(db, scope))


@recruitment_bp.route("/hr/exam/<int:exam_id>")
@login_required()
def exam_questions(exam_id):
    """The paper and its key, with the marks each question carries."""
    db = get_db()
    scope = desk_scope("exam_view")
    exam = desk_exam(db, scope, exam_id)
    return render_template(
        "recruitment/exam_questions.html", exam=exam,
        questions=exam_questions_of(db, exam_id),
        total_question_marks=question_marks(db, exam_id),
        can_edit=scope["can"]("exam_author") and exam["status"] == "Draft",
        attempts=db.execute("""
            SELECT a.*, j.full_name, j.email, j.status AS application_status
            FROM exam_attempts a
            JOIN job_applications j ON j.id = a.application_id
            WHERE a.exam_id = ? ORDER BY a.id
        """, (exam_id,)).fetchall(),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/exam/<int:exam_id>/question/add", methods=["GET", "POST"])
@login_required()
def add_exam_question(exam_id):
    """One more question, refused when the marks would pass the paper's total."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_author")
    exam = desk_exam(db, scope, exam_id)
    if exam["status"] != "Draft":
        flash("Questions are set while the examination is a draft.", "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))
    fields = {name: (request.form.get(name) or "").strip()[:1000]
              for name in ("question", "option_a", "option_b", "option_c", "option_d")}
    fields["correct_answer"] = (request.form.get("correct_answer") or "").strip().upper()
    fields["marks"] = (request.form.get("marks") or "").strip()
    answer = fields["correct_answer"]
    try:
        marks = float(fields["marks"] or 0)
    except (TypeError, ValueError):
        marks = 0.0
    problems = []
    if request.method == "POST":
        if not fields["question"]:
            problems.append("Write the question.")
        if not all(fields[name] for name in ("option_a", "option_b",
                                             "option_c", "option_d")):
            problems.append("Every question needs all four options.")
        if answer not in ("A", "B", "C", "D"):
            problems.append("Mark which option is correct.")
        if marks <= 0:
            problems.append("The question has to carry marks above zero.")
        if question_marks(db, exam_id) + marks > float(exam["total_marks"]):
            problems.append("That takes the paper past its {} total marks."
                            .format(exam["total_marks"]))
        if problems:
            for problem in problems:
                flash(problem, "error")
        else:
            result = db.execute("""
                INSERT INTO exam_questions (exam_id, question, option_a, option_b,
                                            option_c, option_d, correct_answer, marks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (exam_id, fields["question"], fields["option_a"], fields["option_b"],
                  fields["option_c"], fields["option_d"], answer, round(marks, 2)))
            db.commit()
            log_activity("CREATE", "hr", "Question added to examination {}".format(
                exam["exam_title"]), record_type="recruitment_exam", record_id=exam_id,
                details={"question_id": result.lastrowid, "marks": marks})
            flash("Question added.", "success")
            return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))
    return render_template(
        "recruitment/add_exam_question.html", exam=exam, fields=fields,
        total_question_marks=question_marks(db, exam_id),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/question/<int:question_id>/delete", methods=["POST"])
@login_required()
def delete_exam_question(question_id):
    """Withdraw a question, while the paper is still being written."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_author")
    row = db.execute("""
        SELECT q.*, e.status AS exam_status, e.vacancy_id
        FROM exam_questions q
        JOIN recruitment_exams e ON e.id = q.exam_id
        WHERE q.id = ?
    """, (question_id,)).fetchone()
    if row is None:
        abort(404)
    desk_exam(db, scope, row["exam_id"])
    if row["exam_status"] != "Draft":
        flash("Questions are set while the examination is a draft.", "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=row["exam_id"]))
    db.execute("DELETE FROM exam_questions WHERE id = ?", (question_id,))
    db.commit()
    log_activity("DELETE", "hr", "Question removed from examination {}".format(
        row["exam_id"]), record_type="exam_question", record_id=question_id,
        details={"exam_id": row["exam_id"]})
    flash("Question removed.", "success")
    return redirect(url_for("recruitment.exam_questions", exam_id=row["exam_id"]))


@recruitment_bp.route("/hr/exam/<int:exam_id>/questions/template")
@login_required()
def exam_question_template(exam_id):
    """The blank sheet: the header plus two example rows to type over or delete."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_author")
    exam = desk_exam(db, scope, exam_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(QUESTION_IMPORT_COLUMNS)
    writer.writerow(["Which of these is a hardwood?", "Pine", "Teak", "MDF",
                     "Plywood", "B", "2"])
    writer.writerow(["How many planks fit on one rack?", "10", "12", "15",
                     "20", "C", "1.5"])
    # Excel only reads a UTF-8 file as UTF-8 when it carries the byte-order mark.
    response = Response("\ufeff" + buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        'attachment; filename="question-template-{}.csv"'.format(exam["exam_title"][:40]))
    return response


@recruitment_bp.route("/hr/exam/<int:exam_id>/questions/import", methods=["GET", "POST"])
@login_required()
def import_exam_questions(exam_id):
    """A whole paper at once: every row that stands alone is inserted, the rest
    are named so the desk can fix the sheet and upload it again.

    Nothing is written when the sheet cannot be read at all. Once it can, each row
    is checked against the paper's remaining marks in upload order, so a sheet that
    overshoots the total loses the rows past the limit and keeps the ones before it.
    """
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_author")
    exam = desk_exam(db, scope, exam_id)
    if exam["status"] != "Draft":
        flash("Questions are set while the examination is a draft.", "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))

    result = None
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose the filled-in CSV file to upload.", "error")
        else:
            filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
            rows, problem = read_question_file(upload)
            if problem:
                log_activity("CREATE", "hr",
                             "Question import rejected for examination {}: {}".format(
                                 exam_id, problem),
                             outcome="FAILURE", record_type="recruitment_exam",
                             record_id=exam_id, details={"file": filename})
                flash(problem, "error")
            else:
                result = {"file": filename, "added": 0, "rejected": []}
                running = question_marks(db, exam_id)
                for line, values in rows:
                    reason = question_row_problem(values)
                    if reason is None:
                        marks = round(float(values["marks"]), 2)
                        if running + marks > float(exam["total_marks"]):
                            reason = ("That takes the paper past its {} total marks."
                                      .format(exam["total_marks"]))
                    if reason is not None:
                        result["rejected"].append({
                            "line": line,
                            "question": values.get("question", "")[:120],
                            "reason": reason})
                        log_activity("CREATE", "hr",
                                     "Question row {} rejected in examination {}: {}"
                                     .format(line, exam_id, reason),
                                     outcome="FAILURE", record_type="recruitment_exam",
                                     record_id=exam_id,
                                     details={"file": filename, "line": line,
                                              "question": values.get("question", "")[:120]})
                        continue
                    db.execute("""
                        INSERT INTO exam_questions (exam_id, question, option_a,
                                                    option_b, option_c, option_d,
                                                    correct_answer, marks)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (exam_id, values["question"][:1000],
                          values["option_a"][:1000], values["option_b"][:1000],
                          values["option_c"][:1000], values["option_d"][:1000],
                          values["correct_answer"].strip().upper(), marks))
                    db.commit()
                    running += marks
                    result["added"] += 1
                if result["added"]:
                    log_activity("CREATE", "hr",
                                 "{} questions imported into examination {}".format(
                                     result["added"], exam["exam_title"]),
                                 record_type="recruitment_exam", record_id=exam_id,
                                 details={"file": filename,
                                          "rejected": len(result["rejected"]),
                                          "marks_total": running})
                    flash("{} question{} imported.".format(
                        result["added"], "" if result["added"] == 1 else "s"), "success")
                if not result["rejected"]:
                    return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))

    return render_template(
        "recruitment/import_exam_questions.html", exam=exam, result=result,
        total_question_marks=question_marks(db, exam_id),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/exam/<int:exam_id>/publish", methods=["POST"])
@login_required()
def publish_exam(exam_id):
    """Open the paper and put one attempt in front of every shortlisted candidate.

    Publishing is the point no key change survives: the questions freeze and the
    candidates named are fixed, so a paper cannot be rewritten underneath somebody
    already sitting it. The marks have to add up to the total first, which is what
    stops a pass mark measured against a paper that is only half written.
    """
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_publish")
    exam = desk_exam(db, scope, exam_id)
    if exam["status"] != "Draft":
        flash("Only a draft examination can be published.", "error")
        return redirect(url_for("recruitment.hr_exams"))
    if db.execute("SELECT COUNT(*) AS n FROM exam_questions WHERE exam_id = ?",
                  (exam_id,)).fetchone()["n"] == 0:
        flash("Write the questions before publishing.", "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))
    if round(question_marks(db, exam_id), 2) != round(float(exam["total_marks"]), 2):
        flash("The question marks must add up to the total marks.", "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))
    opens = parse_moment(exam["start_datetime"])
    closes = parse_moment(exam["end_datetime"])
    if opens is None or closes is None or closes <= opens:
        flash("Set an opening and a closing time, with the closing after the opening.",
              "error")
        return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))
    candidates = db.execute("""
        SELECT id, full_name, user_id FROM job_applications
        WHERE vacancy_id = ? AND status IN ('Shortlisted', 'Interview')
        ORDER BY id
    """, (exam["vacancy_id"],)).fetchall()
    if not candidates:
        flash("Shortlist candidates for this post before publishing the paper.", "error")
        return redirect(url_for("recruitment.hr_applicants"))
    for candidate in candidates:
        db.execute("INSERT OR IGNORE INTO exam_attempts (exam_id, application_id,"
                   " status) VALUES (?, ?, 'Not Started')",
                   (exam_id, candidate["id"]))
    db.execute("UPDATE recruitment_exams SET status = 'Published' WHERE id = ?",
               (exam_id,))
    stamp_record(db, "recruitment_exams", exam_id, current_user())
    db.commit()
    seated = 0
    for candidate in candidates:
        if candidate["user_id"]:
            deliver_notification(
                db, candidate["user_id"],
                "Examination: {}".format(exam["exam_title"]),
                "{} opens the sitting for the {} post.".format(
                    exam["exam_title"], exam["job_title"]),
                url_for("recruitment.my_exam", exam_id=exam_id),
                category="recruitment", icon="clock", tone="info",
                dedupe_key="exam-{}-{}".format(exam_id, candidate["user_id"]))
            seated += 1
    db.commit()
    log_activity("UPDATE", "hr", "Examination {} published for {}".format(
        exam["exam_title"], exam["job_title"]),
        record_type="recruitment_exam", record_id=exam_id,
        details={"candidates": len(candidates), "notified": seated})
    flash("Examination published for {} candidate(s).{}".format(
        len(candidates),
        " Issue a sitting link on any candidate without a company account."
        if len(candidates) > seated else ""), "success")
    return redirect(url_for("recruitment.exam_questions", exam_id=exam_id))


@recruitment_bp.route("/hr/exam/<int:exam_id>/close", methods=["POST"])
@login_required()
def close_exam(exam_id):
    """Shut the paper; whoever never sat it is recorded as missed."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "exam_publish")
    exam = desk_exam(db, scope, exam_id)
    if exam["status"] != "Published":
        flash("Only a published examination can be closed.", "error")
        return redirect(url_for("recruitment.hr_exams"))
    db.execute("UPDATE exam_attempts SET status = 'Missed' WHERE exam_id = ?"
               " AND status = 'Not Started'", (exam_id,))
    db.execute("UPDATE recruitment_exams SET status = 'Closed' WHERE id = ?", (exam_id,))
    stamp_record(db, "recruitment_exams", exam_id, current_user())
    db.commit()
    log_activity("UPDATE", "hr", "Examination {} closed".format(exam["exam_title"]),
                 record_type="recruitment_exam", record_id=exam_id)
    flash("Examination closed. The results stay on the register.", "success")
    return redirect(url_for("recruitment.hr_exams"))


@recruitment_bp.route("/hr/exam/<int:exam_id>/results")
@login_required()
def hr_exam_results(exam_id):
    """The marked paper: ranked, with the pass line drawn under it."""
    db = get_db()
    scope = desk_scope("exam_result")
    exam = desk_exam(db, scope, exam_id)
    ranked = exam_ranking(db, exam_id)
    return render_template(
        "recruitment/hr_exam_results.html", exam=exam, applicants=ranked,
        total=len(ranked),
        passed_count=sum(1 for row in ranked if row["passed"]),
        failed_count=sum(1 for row in ranked if not row["passed"]),
        **desk_render(db, scope))


# ----------------------------------------------------------- the sitting page
def sitting_questions(db, exam_id):
    """The paper as a candidate sees it - never the key."""
    return db.execute("""
        SELECT id, question, option_a, option_b, option_c, option_d, marks
        FROM exam_questions WHERE exam_id = ? ORDER BY id
    """, (exam_id,)).fetchall()


def begin_sitting(db, attempt):
    """Start the clock on the first entry; a refresh never restarts it."""
    if attempt["status"] != "Not Started":
        return attempt
    db.execute("UPDATE exam_attempts SET started_at = ?, status = 'In Progress'"
               " WHERE id = ? AND status = 'Not Started'",
               (datetime.now().isoformat(timespec="seconds"), attempt["id"]))
    db.commit()
    return db.execute("SELECT * FROM exam_attempts WHERE id = ?",
                      (attempt["id"],)).fetchone()


def sitting_refused(exam, attempt, application, problem, target):
    """Why this paper is not open, with the candidate sent back where they came."""
    log_activity("DENY", "hr", "Sitting refused for candidate {}: {}".format(
        application["reference"] or "#{}".format(application["id"]), problem),
        outcome="DENIED", record_type="exam_attempt", record_id=attempt["id"],
        username=application["email"])
    flash(problem, "error")
    return redirect(target)


def render_sitting(db, exam, attempt, application, back, token=None):
    """The one paper, whether it was opened by a link or from a staff account."""
    problem = exam_window_problem(exam)
    if problem:
        return sitting_refused(exam, attempt, application, problem, back)
    attempt = begin_sitting(db, attempt)
    if request.method == "POST":
        score = grade_attempt(db, exam, attempt, request.form)
        db.commit()
        log_activity("CREATE", "hr", "Examination {} handed in by candidate {}".format(
            exam["exam_title"],
            application["reference"] or "#{}".format(application["id"])),
            record_type="exam_attempt", record_id=attempt["id"],
            details={"score": score, "total_marks": exam["total_marks"]},
            username=application["email"])
        return render_template(
            "recruitment/exam_result.html", exam=exam,
            result={"exam_title": exam["exam_title"], "score": score,
                    "total_marks": exam["total_marks"],
                    "pass_mark": exam["pass_mark"]},
            passed=score >= float(exam["pass_mark"] or 0),
            back=back)
    return render_template(
        "recruitment/take_exam.html", exam=exam, attempt=attempt,
        questions=sitting_questions(db, exam["id"]),
        remaining_seconds=seconds_left(exam, attempt),
        sitting_token=token, back=back,
        exam_title=exam["exam_title"], total_marks=exam["total_marks"])


@recruitment_bp.route("/exam/<token>", methods=["GET", "POST"])
def exam_sit(token):
    """The sitting opened by its one link, for a candidate with no account.

    Nothing here asks who is signed in: the link is the credential, and it is worth
    one attempt only. The digest is cleared when the paper is handed in, so the same
    address lands on this 404 afterwards and a copied history cannot reopen it.
    """
    db = get_db()
    attempt = attempt_for_link(db, token)
    if attempt is None:
        abort(404)
    application = db.execute("SELECT * FROM job_applications WHERE id = ?",
                             (attempt["application_id"],)).fetchone()
    exam = db.execute("SELECT * FROM recruitment_exams WHERE id = ?",
                      (attempt["exam_id"],)).fetchone()
    if application is None or exam is None:
        abort(404)
    back = url_for("recruitment.vacancies")
    if attempt["status"] == "Submitted" or exam["status"] != "Published":
        return sitting_refused(exam, attempt, application,
                               "That paper is not open for sitting.", back)
    return render_sitting(db, exam, attempt, application, back, token=token)


@recruitment_bp.route("/my/exam/<int:exam_id>", methods=["GET", "POST"])
@login_required()
def my_exam(exam_id):
    """The same paper for a staff applicant, opened from their own applications."""
    db = get_db()
    user = current_user()
    exam = db.execute("""
        SELECT e.*, v.job_title FROM recruitment_exams e
        JOIN job_vacancies v ON v.id = e.vacancy_id
        WHERE e.id = ?
    """, (exam_id,)).fetchone()
    if exam is None:
        abort(404)
    application = db.execute("""
        SELECT * FROM job_applications WHERE vacancy_id = ? AND user_id = ?
    """, (exam["vacancy_id"], user["id"])).fetchone()
    if application is None:
        deny("No application of yours leads to examination {}".format(exam_id),
             "recruitment_exam", exam_id)
    back = url_for("recruitment.my_applications")
    attempt = db.execute("SELECT * FROM exam_attempts WHERE exam_id = ?"
                        " AND application_id = ?",
                        (exam_id, application["id"])).fetchone()
    if attempt is None:
        if exam["status"] != "Published":
            flash("This examination is not open for sitting.", "error")
            return redirect(back)
        attempt = start_attempt(db, exam_id, application["id"])
        db.commit()
    if attempt["status"] == "Submitted":
        flash("You have already handed in that paper.", "error")
        return redirect(back)
    return render_sitting(db, exam, attempt, application, back)


# --------------------------------------------------------------- interviews
INTERVIEW_SELECT = """
    SELECT i.*, v.job_title, v.department AS vacancy_department,
           v.reference AS vacancy_reference, v.status AS vacancy_status,
           j.full_name AS candidate_name, j.email AS candidate_email,
           j.phone AS candidate_phone, j.reference AS candidate_reference,
           j.status AS application_status, j.user_id AS candidate_user_id,
           (SELECT COUNT(*) FROM interview_panel p
            WHERE p.interview_id = i.id) AS panel_size,
           (SELECT COUNT(*) FROM interview_scores s
            WHERE s.interview_id = i.id) AS scored_count,
           (SELECT COALESCE(AVG(s.score), 0) FROM interview_scores s
            WHERE s.interview_id = i.id) AS average_score
    FROM recruitment_interviews i
    JOIN job_vacancies v ON v.id = i.vacancy_id
    JOIN job_applications j ON j.id = i.application_id
"""

# The seats a panel is built from: the people the organisation already names as a
# desk holder, never a free-typed name. The candidate is not on the list - they are
# the one being scored.
PANEL_SEATS_SQL = """
    SELECT u.id, u.full_name, u.role, d.name AS department, p.title AS position
    FROM users u
    LEFT JOIN departments d ON d.id = u.department_id
    LEFT JOIN organizational_positions p ON p.id = u.position_id
    WHERE u.status = 'Active'
      AND (u.role IN ('admin', 'hr')
           OR p.level <= {level}
           OR EXISTS (SELECT 1 FROM user_permissions up
                      WHERE up.user_id = u.id AND up.module = 'hr'
                        AND up.allowed = 1))
    ORDER BY COALESCE(p.level, 9), u.full_name
""".format(level=RECRUITMENT_REVIEW_LEVEL)


def panel_candidates(db):
    return db.execute(PANEL_SEATS_SQL).fetchall()


def desk_interview(db, scope, interview_id):
    """One interview, reached through its post - or through a seat on its panel.

    A cross-department panel is normal in hiring, so an invitation to score is its
    own right to read that interview even when the post sits in another department.
    It is a right to read and to score, never to move the candidate or the panel.
    """
    clause, params = vacancy_clause(scope, "v")
    row = db.execute("{} WHERE i.id = ? AND ({})".format(INTERVIEW_SELECT, clause),
                     (interview_id,) + tuple(params)).fetchone()
    if row is None and is_panel_member(db, interview_id, scope["user_id"]):
        row = db.execute("{} WHERE i.id = ?".format(INTERVIEW_SELECT),
                         (interview_id,)).fetchone()
    if row is None:
        deny("Interview #{} is neither in this reach nor is this account on its panel"
             .format(interview_id), "recruitment_interview", interview_id)
    return row


def interview_scores(db, interview_id):
    return db.execute("""
        SELECT s.*, p.panel_user_name AS seat_name FROM interview_scores s
        LEFT JOIN interview_panel p ON p.interview_id = s.interview_id
            AND p.panel_user_id = s.panel_user_id
        WHERE s.interview_id = ? ORDER BY s.id
    """, (interview_id,)).fetchall()


def interview_panel(db, interview_id):
    return db.execute("""
        SELECT p.*, s.score, s.recommendation
        FROM interview_panel p
        LEFT JOIN interview_scores s ON s.interview_id = p.interview_id
            AND s.panel_user_id = p.panel_user_id
        WHERE p.interview_id = ? ORDER BY p.id
    """, (interview_id,)).fetchall()


@recruitment_bp.route("/hr/interviews")
@login_required()
def hr_interviews():
    """The panel register: what this desk scheduled and what it was asked to score."""
    db = get_db()
    scope = desk_scope("interview_view")
    clause, params = vacancy_clause(scope, "v")
    rows = db.execute("""
        {} WHERE ({}) OR i.id IN (
            SELECT interview_id FROM interview_panel WHERE panel_user_id = ?)
        ORDER BY CASE i.status WHEN 'Scheduled' THEN 0 ELSE 1 END,
                 CASE WHEN i.scheduled_at IS NULL OR i.scheduled_at = ''
                      THEN 1 ELSE 0 END, i.scheduled_at, i.id DESC
    """.format(INTERVIEW_SELECT, clause), tuple(params) + (scope["user_id"],)).fetchall()
    return render_template("recruitment/interviews.html", interviews=rows,
                           my_seat=scope["user_id"], **desk_render(db, scope))


@recruitment_bp.route("/hr/applicant/<int:application_id>/interview", methods=["POST"])
@login_required()
def create_interview(application_id):
    """Book a candidate in front of a panel and ask that panel to answer.

    Only the HR desk schedules: the invitation fixes who signs the evaluation, so the
    screen that sets the date is the one that sets the voters. The candidate is moved
    to the interview stage by the same action that books it, which keeps the pipeline
    and the calendar from telling two stories about one person.
    """
    db = get_db()
    scope = desk_scope()
    require_right(scope, "interview_book")
    application, vacancy = guarded_application(db, scope, application_id)
    if application["status"] in ("Hired", "Rejected"):
        flash("That candidate's pipeline has already closed.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    scheduled = (request.form.get("scheduled_at") or "").strip()[:19]
    if scheduled and parse_moment(scheduled) is None:
        flash("Enter the interview date and time as YYYY-MM-DD HH:MM.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    seats = [value for value in request.form.getlist("panel_user_id")
             if value.isdigit()]
    if not seats:
        flash("Name at least one panel member.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    accounts = db.execute("SELECT id, full_name FROM users WHERE status = 'Active'"
                          " AND id IN ({})".format(", ".join("?" * len(seats))),
                          tuple(seats)).fetchall()
    if len(accounts) != len(set(int(value) for value in seats)):
        flash("Every panel member has to be an active account.", "error")
        return redirect(url_for("recruitment.applicant_detail",
                                application_id=application_id))
    result = db.execute("""
        INSERT INTO recruitment_interviews (vacancy_id, application_id, scheduled_at,
                                           location, note, status, outcome,
                                           created_by_id, created_by_name)
        VALUES (?, ?, ?, ?, ?, 'Scheduled', 'Pending', ?, ?)
    """, (vacancy["id"], application_id, scheduled or None,
          (request.form.get("location") or "").strip()[:120],
          (request.form.get("note") or "").strip()[:1000],
          scope["user_id"], scope["name"]))
    interview_id = result.lastrowid
    invite_panel(db, interview_id, accounts)
    if application["status"] != "Interview":
        db.execute("UPDATE job_applications SET status = 'Interview' WHERE id = ?",
                   (application_id,))
    stamp_record(db, "recruitment_interviews", interview_id, current_user())
    db.commit()
    log_activity("CREATE", "hr", "Interview scheduled for candidate {} ({})".format(
        application["reference"] or "#{}".format(application_id),
        application["full_name"]),
        record_type="recruitment_interview", record_id=interview_id,
        details={"vacancy_id": vacancy["id"], "panel": [row["id"] for row in accounts]})
    href = url_for("recruitment.interview_detail", interview_id=interview_id)
    for account in accounts:
        deliver_notification(
            db, account["id"], "Interview panel: {}".format(application["full_name"]),
            "You are on the panel for the {} post. Score the candidate after the"
            " interview.".format(vacancy["job_title"]), href, category="recruitment",
            icon="users", tone="info",
            dedupe_key="panel-{}-{}".format(interview_id, account["id"]))
    if application["user_id"]:
        deliver_notification(
            db, application["user_id"],
            "Interview invited: {}".format(vacancy["job_title"]),
            "Your application reached the interview stage{}".format(
                " on {}.".format(scheduled) if scheduled else "."),
            url_for("recruitment.my_applications"), category="recruitment",
            icon="users", tone="info",
            dedupe_key="interview-{}-{}".format(interview_id, application["user_id"]))
    db.commit()
    flash("Interview scheduled and {} panel member(s) notified.".format(len(accounts)),
          "success")
    return redirect(url_for("recruitment.interview_detail", interview_id=interview_id))


@recruitment_bp.route("/hr/interview/<int:interview_id>")
@login_required()
def interview_detail(interview_id):
    """The candidate, the panel, and every score recorded so far."""
    db = get_db()
    scope = desk_scope("interview_view")
    interview = desk_interview(db, scope, interview_id)
    mine = next((row for row in interview_scores(db, interview_id)
                 if row["panel_user_id"] == scope["user_id"]), None)
    return render_template(
        "recruitment/interview_detail.html", interview=interview,
        panel=interview_panel(db, interview_id),
        scores=interview_scores(db, interview_id), my_score=mine,
        seats=panel_candidates(db),
        can_manage=scope["can_manage"],
        on_panel=is_panel_member(db, interview_id, scope["user_id"]),
        **desk_render(db, scope))


@recruitment_bp.route("/hr/interview/<int:interview_id>/score", methods=["POST"])
@login_required()
def score_interview(interview_id):
    """One panel member's answer, recorded once and overwritable until it closes.

    The score is the seat's own - a panel member cannot cast a second opinion for
    somebody else by guessing an id, because the row is keyed to the signed-in
    account rather than to anything the form carries.
    """
    db = get_db()
    scope = desk_scope("interview_view")
    interview = desk_interview(db, scope, interview_id)
    require_right(scope, "interview_score")
    if interview["status"] == "Cancelled":
        flash("That interview was cancelled.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    if not is_panel_member(db, interview_id, scope["user_id"]):
        deny("This account is not on the panel for interview {}".format(interview_id),
             "recruitment_interview", interview_id)
    try:
        score = float(request.form.get("score") or "")
    except (TypeError, ValueError):
        flash("Enter the score as a number.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    recommendation = (request.form.get("recommendation") or "").strip()
    if not 0 <= score <= INTERVIEW_SCORE_MAX:
        flash("The score has to sit between 0 and {}.".format(INTERVIEW_SCORE_MAX),
              "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    if recommendation not in INTERVIEW_RECOMMENDATIONS:
        flash("Choose Hire, Hold or No hire.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    record_interview_score(db, interview, current_user(), round(score, 2),
                          recommendation,
                          (request.form.get("comment") or "").strip()[:1000])
    if interview["status"] == "Scheduled":
        db.execute("UPDATE recruitment_interviews SET status = 'Held'"
                   " WHERE id = ? AND status = 'Scheduled'", (interview_id,))
    db.commit()
    log_activity("CREATE", "hr", "Panel score {} ({}) recorded for candidate {}".format(
        score, recommendation, interview["candidate_name"]),
        record_type="interview_score", record_id=interview_id,
        details={"interview_id": interview_id, "recommendation": recommendation})
    flash("Your score is recorded.", "success")
    return redirect(url_for("recruitment.interview_detail", interview_id=interview_id))


@recruitment_bp.route("/hr/interview/<int:interview_id>/panel", methods=["POST"])
@login_required()
def set_panel(interview_id):
    """Rebuild the panel before the first score is cast."""
    db = get_db()
    scope = desk_scope()
    require_right(scope, "interview_book")
    interview = desk_interview(db, scope, interview_id)
    if db.execute("SELECT COUNT(*) AS n FROM interview_scores"
                  " WHERE interview_id = ?", (interview_id,)).fetchone()["n"]:
        flash("The panel is set before the first score and not after it.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    seats = [int(value) for value in request.form.getlist("panel_user_id")
             if value.isdigit()]
    if not seats:
        flash("A panel needs at least one member.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    accounts = db.execute("SELECT id, full_name FROM users WHERE status = 'Active'"
                          " AND id IN ({})".format(", ".join("?" * len(seats))),
                          tuple(seats)).fetchall()
    if len(accounts) != len(seats):
        flash("Every panel member has to be an active account.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    db.execute("DELETE FROM interview_panel WHERE interview_id = ?", (interview_id,))
    invite_panel(db, interview_id, accounts)
    db.commit()
    log_activity("UPDATE", "hr", "Panel reset for interview {} of candidate {}".format(
        interview_id, interview["candidate_name"]),
        record_type="recruitment_interview", record_id=interview_id,
        details={"panel": [row["id"] for row in accounts]})
    flash("Panel updated.", "success")
    return redirect(url_for("recruitment.interview_detail", interview_id=interview_id))


@recruitment_bp.route("/hr/interview/<int:interview_id>/decide", methods=["POST"])
@login_required()
def decide_interview(interview_id):
    """Close the interview with the desk's answer; the panel only advises.

    The outcome is the HR desk's, not an average of the seats: a panel of two who
    both say Hire does not hire anybody, and a desk that disagrees with a unanimous
    panel has to say so in the open rather than let a figure decide a person.
    """
    db = get_db()
    scope = desk_scope()
    require_right(scope, "interview_decide")
    interview = desk_interview(db, scope, interview_id)
    status = (request.form.get("status") or "").strip()
    outcome = (request.form.get("outcome") or "").strip()
    if status not in ("Scheduled", "Held", "Cancelled") or \
            outcome not in INTERVIEW_OUTCOMES:
        flash("Choose a known status and outcome for the interview.", "error")
        return redirect(url_for("recruitment.interview_detail",
                                interview_id=interview_id))
    db.execute("UPDATE recruitment_interviews SET status = ?, outcome = ?, note ="
              " COALESCE(NULLIF(?, ''), note) WHERE id = ?",
              (status, outcome, (request.form.get("note") or "").strip()[:1000],
               interview_id))
    # The desk's Passed is the selection: the candidate is moved to Selected by
    # the same action that records the answer, so the hiring pipeline and the
    # interview register never disagree about who won the post.
    selected = False
    if outcome == "Passed" and status != "Cancelled":
        application = db.execute("SELECT id, status, user_id FROM job_applications"
                                 " WHERE id = ?",
                                 (interview["application_id"],)).fetchone()
        if application is not None and application["status"] in (
                "Applied", "Shortlisted", "Interview"):
            user = current_user()
            db.execute("UPDATE job_applications SET status = 'Selected',"
                       " screened_by_id = ?, screened_by_name = ?, screened_at = ?"
                       " WHERE id = ?",
                       (user["id"], user["full_name"],
                        datetime.now().isoformat(timespec="seconds"),
                        application["id"]))
            stamp_record(db, "job_applications", application["id"], user)
            selected = True
    stamp_record(db, "recruitment_interviews", interview_id, current_user())
    db.commit()
    log_activity("UPDATE", "hr", "Interview {} closed as {} ({})".format(
        interview_id, status, outcome),
        record_type="recruitment_interview", record_id=interview_id,
        details={"candidate": interview["candidate_name"],
                 "previous_status": interview["status"]})
    if interview["candidate_user_id"]:
        deliver_notification(
            db, interview["candidate_user_id"],
            "Interview outcome: {}".format(interview["job_title"]),
            "The interview for the {} post is recorded as {}.".format(
                interview["job_title"], outcome.lower()),
            url_for("recruitment.my_applications"), category="recruitment",
            icon="user", tone="ok" if outcome == "Passed" else "info",
            dedupe_key="interview-outcome-{}-{}".format(interview_id, outcome))
        db.commit()
    flash("Interview recorded as {}.".format(outcome.lower()), "success")
    if selected:
        flash("The candidate was moved to Selected - register the staff record to"
              " complete the hire.", "info")
    return redirect(url_for("recruitment.interview_detail", interview_id=interview_id))


# ------------------------------------------------------------------- reports
def vacancy_rollup(db, clause, params):
    """One row per post: how far its candidates got and what it costs.

    Every figure is counted from the same rows the desk screens read, so a report
    and a register never disagree about how many people are shortlisted.
    """
    return db.execute("""
        SELECT v.id, v.reference, v.job_title, v.department, v.status, v.openings,
               v.estimated_cost, v.closing_date, v.created_at,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id) AS applicants,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id AND a.status = 'Shortlisted') AS shortlisted,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id AND a.status = 'Interview') AS interviewed,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id AND a.status = 'Selected') AS selected,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id AND a.status = 'Rejected') AS rejected,
               (SELECT COUNT(*) FROM job_applications a
                WHERE a.vacancy_id = v.id AND a.status = 'Hired') AS hired,
               (SELECT COALESCE(AVG(at.score), 0) FROM exam_attempts at
                JOIN recruitment_exams e ON e.id = at.exam_id
                WHERE e.vacancy_id = v.id AND at.status = 'Submitted') AS exam_average,
               (SELECT COALESCE(AVG(s.score), 0) FROM interview_scores s
                JOIN recruitment_interviews i ON i.id = s.interview_id
                WHERE i.vacancy_id = v.id) AS interview_average
        FROM job_vacancies v
        WHERE ({}) ORDER BY v.id DESC
    """.format(clause), params).fetchall()


def stage_funnel(db, clause, params):
    """Applications grouped by the post's department, stage by stage."""
    return db.execute("""
        SELECT COALESCE(d.name, v.department, 'Not registered') AS department,
               COUNT(a.id) AS applicants,
               SUM(CASE WHEN a.status = 'Shortlisted' THEN 1 ELSE 0 END) AS shortlisted,
               SUM(CASE WHEN a.status = 'Interview' THEN 1 ELSE 0 END) AS interviewed,
               SUM(CASE WHEN a.status = 'Selected' THEN 1 ELSE 0 END) AS selected,
               SUM(CASE WHEN a.status = 'Hired' THEN 1 ELSE 0 END) AS hired,
               SUM(CASE WHEN a.status = 'Rejected' THEN 1 ELSE 0 END) AS rejected
        FROM job_vacancies v
        LEFT JOIN departments d ON d.id = v.department_id
        LEFT JOIN job_applications a ON a.vacancy_id = v.id
        WHERE ({})
        GROUP BY COALESCE(d.name, v.department, 'Not registered')
        ORDER BY COUNT(a.id) DESC, department
    """.format(clause), params).fetchall()


@recruitment_bp.route("/hr/reports")
@login_required()
def hr_reports():
    """The hiring report for this account's reach, including an auditor's whole view."""
    db = get_db()
    scope = desk_scope("hiring_report")
    clause, params = vacancy_clause(scope, "v")
    context = desk_render(db, scope)
    exams = db.execute("{} WHERE ({}) ORDER BY e.id DESC".format(EXAM_SELECT, clause),
                       params).fetchall()
    outcomes = db.execute("""
        SELECT i.outcome, COUNT(*) AS total FROM recruitment_interviews i
        JOIN job_vacancies v ON v.id = i.vacancy_id
        WHERE ({}) GROUP BY i.outcome ORDER BY total DESC
    """.format(clause), params).fetchall()
    money = db.execute("""
        SELECT COALESCE(SUM(v.estimated_cost), 0) AS requested,
               SUM(CASE WHEN v.status = 'Open' THEN v.estimated_cost ELSE 0 END)
                   AS published_cost,
               SUM(CASE WHEN v.status = 'Draft' THEN v.estimated_cost ELSE 0 END)
                   AS draft_cost
        FROM job_vacancies v WHERE ({})
    """.format(clause), params).fetchone()
    committed = db.execute("""
        SELECT COALESCE(SUM(c.amount), 0) AS total FROM budget_commitments c
        WHERE c.source_table = 'job_vacancies' AND c.status = 'Open'
    """).fetchone()["total"]
    return render_template(
        "recruitment/hr_reports.html", posts=vacancy_rollup(db, clause, params),
        funnel=stage_funnel(db, clause, params), exams=exams, outcomes=outcomes,
        money=money, committed=committed,
        awaiting=db.execute("""
            SELECT COUNT(*) AS n FROM approval_requests ar
            JOIN job_vacancies v ON v.id = ar.source_id
            WHERE ar.source_table = 'job_vacancies' AND ar.status = 'Pending'
              AND ({})
        """.format(clause), params).fetchone()["n"],
        **context)


# --------------------------------------------------------------- budget desk
@recruitment_bp.route("/finance")
@login_required()
def finance_desk():
    """What recruitment has asked the money for, read only.

    The signature itself is given on the approvals screen, which is where every
    chain in the company is decided and where the history of a decision is written.
    This page is the money's own view of the same requests: what is waiting, what
    was signed, and which allocation carries it. Nothing here decides twice.
    """
    db = get_db()
    user = current_user()
    scope = recruitment_scope(db, user)
    if scope is None or not (scope["can_budget"] or scope["can_manage"]):
        deny("The recruitment budget desk is for the finance and HR desks")
    wanted = request.args.get("status", "").strip()
    if wanted not in ("Pending", "Approved", "Rejected", "Returned"):
        wanted = ""
    acted = approval_acted_ids(db, user["id"])
    visible = []
    for row in db.execute("""
        SELECT ar.*, v.id AS vacancy_id, v.reference, v.job_title, v.department,
               v.status AS vacancy_status, v.openings, v.estimated_cost,
               b.allocation_name, b.department AS allocation_department,
               p.plan_name, p.fiscal_year
        FROM approval_requests ar
        JOIN job_vacancies v ON v.id = ar.source_id
        LEFT JOIN budget_allocations b ON b.id = v.budget_allocation_id
        LEFT JOIN budget_plans p ON p.id = b.plan_id
        WHERE ar.source_table = 'job_vacancies'
        ORDER BY CASE ar.status WHEN 'Pending' THEN 0 ELSE 1 END, ar.id DESC
    """).fetchall():
        # The same right that opens a requisition chain opens a hiring one: the
        # desk the route is standing at, the account that raised it, or anybody who
        # already signed. Which of those an account is comes from the chain, not
        # from which department the post happens to sit in.
        if not (scope["can_manage"] or can_view_request(row, user, acted)):
            continue
        if wanted and row["status"] != wanted:
            continue
        visible.append(dict(row, can_sign=can_decide_request(row, user)))
    totals = {
        "requests": len(visible),
        "awaiting": sum(1 for row in visible if row["status"] == "Pending"),
        "awaiting_amount": sum(float(row["amount"] or 0) for row in visible
                               if row["status"] == "Pending"),
        "approved_amount": sum(float(row["amount"] or 0) for row in visible
                               if row["status"] == "Approved"),
        "rejected_amount": sum(float(row["amount"] or 0) for row in visible
                               if row["status"] == "Rejected"),
    }
    return render_template(
        "recruitment/finance_desk.html", requests=visible, totals=totals,
        selected_status=wanted, scope=scope, can_manage=scope["can_manage"])
