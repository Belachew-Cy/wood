"""Roles bound to a department, and the standard bundles the charter needs.

A role is normally carried by an account. A department binding is the other
direction: it says that everyone placed in this unit - or only the seats on one
rung of it - also carries this role. The unit's own binding reaches the units
below it, so a directorate can hand its desk to every department inside it, and
the company-wide sentinel (department 0) lets the CEO and the audit seats apply
without naming a unit.

Nothing here changes an account's primary role or its home dashboard. A binding
adds modules and actions, exactly as an additional role does, and the account's
own permission rows are still applied last, so an explicit denial keeps winning.

Applying the standard roles and bindings is additive and repeatable: an existing
role is left as the administrator has edited it, an existing binding is skipped,
and no account, employee or department row is touched.
"""

from core.access import account_department_id
from core.access import department_action_baseline
from core.access import department_branch_ids
from core.access import role_granted_permissions
from core.access import role_permission_set
from core.config import PERMISSION_HIRING_RIGHTS
from core.config import PERMISSION_LIST
from services.org import POSITION_LEVEL_LABEL
from services.org import build_department_tree
from services.org_structure import structure_plan
from services.workflow import account_position_level
import sqlite3


# Every role below is a standard bundle owned by one unit of the organization.
# The unit groups the role on the console and seeds the permissions the create
# dialog offers; it never grants anything by itself. A manager bundle adds the
# approval queue to its desk, an officer bundle keeps the desk alone, and the
# director bundles cover a whole directorate. "level" is the rung a suggested
# binding would use, and "rank" is the word the screen shows for it.
STANDARD_ROLES = (
    {
        "key": "chief_executive",
        "name": "Chief Executive Officer",
        "unit": None,
        "level": 0,
        "rank": "Executive",
        "description": "Company-wide oversight of every operational desk, the"
                       " approval ladder and the audit trail. Not account or"
                       " integration administration.",
        "modules": ("hr", "finance", "budget", "payroll", "inventory", "asset",
                    "procurement", "marketing", "project", "approvals", "audit",
                    "letters"),
    },
    {
        "key": "finance_director",
        "name": "Finance Director",
        "unit": "Finance Management Directorate",
        "level": 2,
        "rank": "Director",
        "description": "Directs Finance, Budget and Payroll: money records,"
                       " plans, payroll runs and their approvals.",
        "modules": ("finance", "budget", "payroll", "approvals", "audit"),
    },
    {
        "key": "property_procurement_director",
        "name": "Property & Procurement Director",
        "unit": "Property & Procurement Directorate",
        "level": 2,
        "rank": "Director",
        "description": "Directs Procurement, Property & Assets and Inventory &"
                       " Warehouse, with their approvals and the audit trail.",
        "modules": ("procurement", "inventory", "asset", "approvals", "audit"),
    },
    {
        "key": "hr_admin_director",
        "name": "HR & Administration Director",
        "unit": "Human Resources & Administration Directorate",
        "level": 2,
        "rank": "Director",
        "description": "Directs Human Resources, Administration and Recruitment,"
                       " with the letter register and the audit trail.",
        "modules": ("hr", "letters", "approvals", "audit"),
    },
    {
        "key": "business_operations_director",
        "name": "Business Development & Operations Director",
        "unit": "Business Development & Operations Directorate",
        "level": 2,
        "rank": "Director",
        "description": "Directs Sales & Marketing, Customer Service and Project"
                       " Management, with their campaign, project, correspondence"
                       " and approval desks.",
        "modules": ("marketing", "project", "letters", "approvals", "audit"),
    },
    {
        "key": "finance_manager",
        "name": "Finance Manager",
        "unit": "Finance Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs the finance desk: income, expenses, invoices,"
                       " payments and the approvals they raise.",
        "modules": ("finance", "approvals"),
    },
    {
        "key": "accountant",
        "name": "Accountant",
        "unit": "Finance Department",
        "level": 4,
        "rank": "Officer",
        "description": "Keeps the finance records and posts transactions.",
        "modules": ("finance",),
    },
    {
        "key": "budget_manager",
        "name": "Budget Manager",
        "unit": "Budget Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs budget planning, allocations and their approvals.",
        "modules": ("budget", "approvals"),
    },
    {
        "key": "budget_officer",
        "name": "Budget Officer",
        "unit": "Budget Department",
        "level": 4,
        "rank": "Officer",
        "description": "Prepares plans, allocations and expenditure records.",
        "modules": ("budget",),
    },
    {
        "key": "payroll_manager",
        "name": "Payroll Manager",
        "unit": "Payroll Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs payroll processing and its approval step.",
        "modules": ("payroll", "approvals"),
    },
    {
        "key": "payroll_officer",
        "name": "Payroll Officer",
        "unit": "Payroll Department",
        "level": 4,
        "rank": "Officer",
        "description": "Prepares payslips and keeps salary records.",
        "modules": ("payroll",),
    },
    {
        "key": "procurement_manager",
        "name": "Procurement Manager",
        "unit": "Procurement Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs requisitions, purchase orders and their approvals.",
        "modules": ("procurement", "approvals"),
    },
    {
        "key": "procurement_officer",
        "name": "Procurement Officer",
        "unit": "Procurement Department",
        "level": 4,
        "rank": "Officer",
        "description": "Raises requisitions and keeps supplier and order records.",
        "modules": ("procurement",),
    },
    {
        "key": "inventory_manager",
        "name": "Inventory Manager",
        "unit": "Inventory & Warehouse Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs stock, warehouse counts, purchasing and approvals.",
        "modules": ("inventory", "procurement", "approvals"),
    },
    {
        "key": "storekeeper",
        "name": "Storekeeper",
        "unit": "Inventory & Warehouse Department",
        "level": 4,
        "rank": "Officer",
        "description": "Records stock receipts, issues and transfers.",
        "modules": ("inventory",),
    },
    {
        "key": "hr_manager",
        "name": "HR Manager",
        "unit": "Human Resources Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs employee records, leave, attendance and their"
                       " approvals.",
        "modules": ("hr", "approvals"),
    },
    {
        "key": "hr_officer",
        "name": "HR Officer",
        "unit": "Human Resources Department",
        "level": 4,
        "rank": "Officer",
        "description": "Keeps staff records, attendance and leave requests.",
        "modules": ("hr",),
    },
    {
        "key": "recruitment_manager",
        "name": "Recruitment Manager",
        "unit": "Recruitment Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs vacancies, examinations, hiring and their approvals.",
        "modules": ("hr", "approvals"),
    },
    {
        "key": "recruitment_officer",
        "name": "Recruitment Officer",
        "unit": "Recruitment Department",
        "level": 4,
        "rank": "Officer",
        "description": "Registers candidates and runs examinations and panels.",
        "modules": ("hr",),
    },
    {
        "key": "administration_manager",
        "name": "Administration Manager",
        "unit": "Administration Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs office administration, the letter register and its"
                       " approvals.",
        "modules": ("letters", "approvals"),
    },
    {
        "key": "administration_officer",
        "name": "Administration Officer",
        "unit": "Administration Department",
        "level": 4,
        "rank": "Officer",
        "description": "Office administration and the correspondence register.",
        "modules": ("letters",),
    },
    {
        "key": "it_manager",
        "name": "IT Manager",
        "unit": "Information Technology",
        "level": 3,
        "rank": "Manager",
        "description": "Runs systems, integrations and the audit trail.",
        "modules": ("integrations", "audit"),
    },
    {
        "key": "it_operations",
        "name": "IT Operations",
        "unit": "Information Technology",
        "level": 4,
        "rank": "Officer",
        "description": "Systems, integrations and the audit trail. Deliberately"
                       " holds no account or role administration.",
        "modules": ("integrations", "audit"),
    },
    {
        "key": "security_officer",
        "name": "Information Security Officer",
        "unit": "Cybersecurity",
        "level": 4,
        "rank": "Officer",
        "description": "Reads the audit trail and reviews access and activity.",
        "modules": ("audit",),
    },
    {
        "key": "marketing_manager",
        "name": "Marketing Manager",
        "unit": "Sales & Marketing Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs campaigns, customer correspondence and approvals.",
        "modules": ("marketing", "letters", "approvals"),
    },
    {
        "key": "marketing_officer",
        "name": "Marketing Officer",
        "unit": "Sales & Marketing Department",
        "level": 4,
        "rank": "Officer",
        "description": "Keeps campaign and customer correspondence.",
        "modules": ("marketing", "letters"),
    },
    {
        "key": "customer_service_manager",
        "name": "Customer Service Manager",
        "unit": "Customer Service Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs customer inquiries, correspondence and approvals.",
        "modules": ("letters", "approvals"),
    },
    {
        "key": "customer_service_officer",
        "name": "Customer Service Officer",
        "unit": "Customer Service Department",
        "level": 4,
        "rank": "Officer",
        "description": "Answers customer correspondence and registers requests.",
        "modules": ("letters",),
    },
    {
        "key": "project_manager",
        "name": "Project Manager",
        "unit": "Project Management Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs the project register, its schedule, correspondence"
                       " and approvals.",
        "modules": ("project", "letters", "approvals"),
    },
    {
        "key": "project_coordinator",
        "name": "Project Coordinator",
        "unit": "Project Management Department",
        "level": 4,
        "rank": "Officer",
        "description": "Keeps project plans, schedules and letters.",
        "modules": ("project", "letters"),
    },
    {
        "key": "asset_manager",
        "name": "Property & Asset Manager",
        "unit": "Property & Asset Management Department",
        "level": 3,
        "rank": "Manager",
        "description": "Runs the asset register, procurement and approvals.",
        "modules": ("asset", "procurement", "approvals"),
    },
    {
        "key": "property_officer",
        "name": "Property Officer",
        "unit": "Property & Asset Management Department",
        "level": 4,
        "rank": "Officer",
        "description": "Keeps asset allocations, transfers and disposal records.",
        "modules": ("asset",),
    },
)


# Letter actions a standard role starts with. The register itself is granted per
# account as well, so these rows only pre-tick the role dialog; an administrator
# can change them at any time.
STANDARD_LETTER_ACTIONS = {
    4: ("letters_view", "letters_create", "letters_edit", "letters_send",
        "letters_print", "letters_download"),
    3: ("letters_view", "letters_create", "letters_edit", "letters_send",
        "letters_approve", "letters_print", "letters_download"),
    2: ("letters_view", "letters_create", "letters_edit", "letters_send",
        "letters_approve", "letters_print", "letters_download"),
    0: ("letters_view", "letters_create", "letters_edit", "letters_send",
        "letters_approve", "letters_print", "letters_download"),
}


# The unit each built-in role belongs to on the console. The administrator, the
# auditor and the two staff roles stay company-wide.
BUILTIN_ROLE_UNITS = {
    "hr": "Human Resources Department",
    "finance": "Finance Department",
    "budget": "Budget Department",
    "procurement": "Procurement Department",
    "payroll": "Payroll Department",
    "inventory": "Inventory & Warehouse Department",
}


# Which unit a standard bundle belongs to. A functional department is bound to
# the built-in role that already owns its desk. "level" -1 reaches every seat in
# the unit; a director and the CEO bind only their own rung, so a unit binding
# can never lift a staff account onto a signing desk.
SUGGESTED_BINDINGS = (
    {"unit": None, "role_key": "chief_executive", "level": 0,
     "note": "Company-wide, CEO seat only."},
    {"unit": "Finance Management Directorate", "role_key": "finance_director",
     "level": 2, "note": "Director seat; reaches the three departments below."},
    {"unit": "Property & Procurement Directorate",
     "role_key": "property_procurement_director", "level": 2,
     "note": "Director seat; reaches the three departments below."},
    {"unit": "Human Resources & Administration Directorate",
     "role_key": "hr_admin_director", "level": 2,
     "note": "Director seat; reaches the three departments below."},
    {"unit": "Business Development & Operations Directorate",
     "role_key": "business_operations_director", "level": 2,
     "note": "Director seat; reaches the three departments below."},
    {"unit": "Sales & Marketing Department", "role_key": "marketing_officer",
     "level": -1, "note": "Campaign desk for every seat."},
    {"unit": "Sales & Marketing Department", "role_key": "marketing_manager",
     "level": 3, "note": "Manager seat; adds the marketing approval queue."},
    {"unit": "Project Management Department", "role_key": "project_coordinator",
     "level": -1, "note": "Project desk for every seat."},
    {"unit": "Project Management Department", "role_key": "project_manager",
     "level": 3, "note": "Manager seat; adds the project approval queue."},
    {"unit": "Property & Asset Management Department",
     "role_key": "property_officer", "level": -1,
     "note": "Asset desk for every seat."},
    {"unit": "Property & Asset Management Department",
     "role_key": "asset_manager", "level": 3,
     "note": "Manager seat; adds procurement and the approval queue."},
    {"unit": "Finance Department", "role_key": "finance", "level": -1,
     "note": "Accounting, invoices and payments."},
    {"unit": "Budget Department", "role_key": "budget", "level": -1,
     "note": "Plans, allocations and budget requests."},
    {"unit": "Payroll Department", "role_key": "payroll", "level": -1,
     "note": "Payroll runs and salary records."},
    {"unit": "Procurement Department", "role_key": "procurement", "level": -1,
     "note": "Requisitions, orders and suppliers."},
    {"unit": "Inventory & Warehouse Department", "role_key": "inventory",
     "level": -1, "note": "Stock, issues and warehouse counts."},
    {"unit": "Human Resources Department", "role_key": "hr", "level": -1,
     "note": "Staff records, leave, attendance and the chart."},
    {"unit": "Recruitment Department", "role_key": "hr", "level": -1,
     "note": "Hiring desk; hiring rights follow the HR module."},
    {"unit": "Information Technology", "role_key": "it_operations", "level": -1,
     "note": "Integrations and audit, without account administration."},
    {"unit": "Administration Department", "role_key": "administration_officer",
     "level": -1, "note": "Correspondence register."},
)

# Units with no module of their own in this build. Named here so the screen can
# say why they carry no binding instead of leaving a silent gap. Sales &
# Marketing, Project Management and Property & Assets each own a module now and
# carry suggested bindings above.
UNMATCHED_UNITS = (
    "Customer Service Department",
)


def role_bundle_permissions(modules):
    """The catalogue keys a bundle's modules open, excluding hiring rights.

    Hiring rights are answered by the recruitment tables from the module tick
    itself, so writing them into the matrix would freeze a copy that drifts.
    """
    modules = set(modules)
    return sorted(
        permission["key"] for permission in PERMISSION_LIST
        if not permission.get("hiring_rights")
        and set(permission.get("modules") or ()) <= modules)


def _department_ids(db):
    """Unit name -> live department id, for the units already published.

    The charter answers most names and its aliases; a live unit outside the
    charter (Cybersecurity today) still answers its own exact name, so its
    standard role is filed there too.
    """
    ids = {}
    for unit in structure_plan(db)["units"]:
        if unit["existing_id"] is not None:
            ids[unit["name"]] = unit["existing_id"]
    for row in db.execute("SELECT id, name FROM departments").fetchall():
        ids.setdefault(row["name"], row["id"])
    return ids


def _seed_standard_letters(db, role_key, level):
    """Pre-tick the letter actions a standard role starts with."""
    actions = STANDARD_LETTER_ACTIONS.get(level)
    if not actions:
        return
    try:
        for action in actions:
            db.execute("INSERT INTO letter.letter_role_permissions"
                       " (role_key, permission, allowed) VALUES (?, ?, 1)",
                       (role_key, action))
    except sqlite3.Error:
        # The register is created after the roles on a brand new file; the next
        # start seeds the actions without losing the role itself.
        pass


def create_standard_roles(db):
    """Create the standard bundles that are missing, then sync their units.

    An existing role is never re-seeded: its permissions belong to the
    administrator once it is created. A standard role does keep the unit the
    charter assigns it, because that unit is how the console groups it. An
    existing role whose key is unknown to the charter is left untouched.
    """
    unit_ids = _department_ids(db)
    created = []
    for template in STANDARD_ROLES:
        department_id = (0 if template["unit"] is None
                         else unit_ids.get(template["unit"], 0))
        row = db.execute("SELECT key FROM roles WHERE key = ?",
                         (template["key"],)).fetchone()
        if row is not None:
            db.execute("""
                UPDATE roles SET is_standard = 1, department_id = ?,
                                 updated_at = CURRENT_TIMESTAMP
                WHERE key = ? AND (COALESCE(is_standard, 0) = 0
                                   OR COALESCE(department_id, 0) <> ?)
            """, (department_id, template["key"], department_id))
            continue
        db.execute("""
            INSERT INTO roles (key, name, description, landing, is_system,
                               is_standard, department_id)
            VALUES (?, ?, ?, 'dashboard', 0, 1, ?)
        """, (template["key"], template["name"], template["description"],
              department_id))
        for module in template["modules"]:
            db.execute("INSERT INTO role_permissions (role_key, module)"
                       " VALUES (?, ?)", (template["key"], module))
        for permission in role_bundle_permissions(template["modules"]):
            db.execute("""
                INSERT INTO role_permission_grants
                    (role_key, permission, allowed, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            """, (template["key"], permission))
        if "letters" in template["modules"]:
            _seed_standard_letters(db, template["key"], template["level"])
        created.append(template["name"])

    # A built-in desk role belongs to the department it serves as well, so the
    # console can group every role the same way. The role keeps its permissions.
    for key, unit in BUILTIN_ROLE_UNITS.items():
        department_id = unit_ids.get(unit)
        if department_id is None:
            continue
        db.execute("""
            UPDATE roles SET department_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE key = ? AND COALESCE(department_id, 0) <> ?
        """, (department_id, key, department_id))
    return created


def _standard_template(role_key):
    for template in STANDARD_ROLES:
        if template["key"] == role_key:
            return template
    return None


def standard_role_drift(db, role_key):
    """What a standard role's charter names but the stored matrix no longer has.

    Only missing rows are counted. A permission the administrator unticked is
    stored as a refusal, so the drift never asks to restore a decision that was
    already made; it only reports what was never written in the first place.
    """
    template = _standard_template(role_key)
    if template is None:
        return None
    row = db.execute("SELECT is_standard FROM roles WHERE key = ?",
                     (role_key,)).fetchone()
    if row is None or not row["is_standard"]:
        return None
    modules = {entry["module"] for entry in db.execute(
        "SELECT module FROM role_permissions WHERE role_key = ?", (role_key,))}
    stored = {entry["permission"] for entry in db.execute(
        "SELECT permission FROM role_permission_grants WHERE role_key = ?",
        (role_key,))}
    wanted = set(role_bundle_permissions(template["modules"]))
    actions = set(STANDARD_LETTER_ACTIONS.get(template["level"], ()))
    try:
        stored_actions = {entry["permission"] for entry in db.execute(
            "SELECT permission FROM letter.letter_role_permissions"
            " WHERE role_key = ?", (role_key,))}
    except sqlite3.Error:
        stored_actions = set()
    missing_modules = sorted(set(template["modules"]) - modules)
    missing_grants = sorted(wanted - stored)
    missing_letters = sorted(actions - stored_actions)
    return {
        "modules": missing_modules,
        "permissions": missing_grants,
        "letters": missing_letters,
        "total": (len(missing_modules) + len(missing_grants)
                  + len(missing_letters)),
    }


def sync_standard_role(db, role_key):
    """Add the charter rows a standard role is missing; nothing is removed.

    Used from the department console when a standard role was filed before its
    desk's permissions existed. A refusal the administrator stored by hand is
    left as it is, so the sync can be run more than once without undoing it.
    """
    drift = standard_role_drift(db, role_key)
    if drift is None:
        raise ValueError("Only a standard role can be synced from the charter.")
    role = db.execute("SELECT name FROM roles WHERE key = ?",
                      (role_key,)).fetchone()
    name = role["name"] if role is not None else role_key
    if not drift["total"]:
        return {
            "notice": "{} already carries every right its charter names.".format(name),
            "changed": False,
        }
    for module in drift["modules"]:
        db.execute("INSERT INTO role_permissions (role_key, module)"
                   " VALUES (?, ?)", (role_key, module))
    for permission in drift["permissions"]:
        db.execute("""
            INSERT INTO role_permission_grants
                (role_key, permission, allowed, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        """, (role_key, permission))
    for action in drift["letters"]:
        try:
            db.execute("INSERT INTO letter.letter_role_permissions"
                       " (role_key, permission, allowed) VALUES (?, ?, 1)",
                       (role_key, action))
        except sqlite3.Error:
            break
    return {
        "notice": "{} restored to its charter: {} desk{} and {} permission{}"
                  " added.".format(
                      name, len(drift["modules"]),
                      "" if len(drift["modules"]) == 1 else "s",
                      len(drift["permissions"]),
                      "" if len(drift["permissions"]) == 1 else "s"),
        "changed": True,
        "details": {
            "modules": drift["modules"],
            "permissions": drift["permissions"],
            "letter_permissions": drift["letters"],
        },
    }


def sync_all_standard_roles(db):
    """Add every missing charter row across the standard catalogue.

    Only rows the charter names and the database never stored are added, so an
    administrator's own refusal survives. Used by the catalogue's bulk action
    when a whole installation predates the current bundles.
    """
    synced = []
    for template in STANDARD_ROLES:
        drift = standard_role_drift(db, template["key"])
        if not drift or not drift["total"]:
            continue
        result = sync_standard_role(db, template["key"])
        if result.get("changed"):
            synced.append({
                "role": template["key"],
                "name": template["name"],
                "details": result.get("details") or {},
            })
    return {"roles": synced, "total": len(synced)}


def standard_role_groups(db):
    """The standard catalogue grouped by unit, with each role's live state."""
    unit_ids = _department_ids(db)
    rows = {row["key"]: row for row in db.execute("""
        SELECT key, COALESCE(active, 1) AS active,
               COALESCE(department_id, 0) AS department_id
        FROM roles
    """).fetchall()}
    groups = []
    index = {}
    for template in STANDARD_ROLES:
        unit = template["unit"] or "Company-wide"
        if unit not in index:
            index[unit] = {"unit": unit, "roles": []}
            groups.append(index[unit])
        department_id = (0 if template["unit"] is None
                         else unit_ids.get(template["unit"], 0))
        row = rows.get(template["key"])
        index[unit]["roles"].append({
            "key": template["key"],
            "name": template["name"],
            "rank": template["rank"],
            "description": template["description"],
            "modules": list(template["modules"]),
            "department_id": (department_id if template["unit"] else 0),
            "present": row is not None,
            "active": bool(row["active"]) if row is not None else False,
            "drift": ((standard_role_drift(db, template["key"]) or {}).get("total", 0)
                      if row is not None else 0),
        })
    return groups


def standard_role_presets(db):
    """The bundles the create dialog can pre-tick, with their live unit id."""
    from services.recruitment import HIRING_MODULE_RIGHTS
    unit_ids = _department_ids(db)
    presets = []
    for template in STANDARD_ROLES:
        permissions = role_bundle_permissions(template["modules"])
        letters = list(STANDARD_LETTER_ACTIONS.get(template["level"], ()))
        hiring = set()
        for module in template["modules"]:
            hiring |= HIRING_MODULE_RIGHTS.get(module, frozenset())
        department_actions = department_action_baseline(
            db, template["key"], permissions, set(template["modules"]),
            letter_actions=set(letters), hiring_rights=hiring)
        presets.append({
            "key": template["key"],
            "name": template["name"],
            "unit": template["unit"] or "Company-wide",
            "department_id": (0 if template["unit"] is None
                              else unit_ids.get(template["unit"])),
            "description": template["description"],
            "level": template["level"],
            "permissions": permissions,
            "letters": letters,
            "department_actions": sorted(department_actions),
        })
    return presets


def department_permission_scope(db):
    """Department id -> the catalogue keys its standard roles open.

    A unit inherits the scope of the units below it, so choosing a directorate
    offers its departments' desks as well. The create dialog narrows the
    catalogue to this scope, and its "show all" switch lifts that narrowing; the
    scope never prevents any permission from being saved.
    """
    unit_ids = _department_ids(db)
    scope = {}

    def add(department_id, keys):
        scope.setdefault(department_id, set()).update(keys)

    from services.recruitment import HIRING_MODULE_RIGHTS

    def hiring_keys(modules):
        rights = set()
        for module in modules:
            rights |= HIRING_MODULE_RIGHTS.get(module, frozenset())
        return {key for key, needed in PERMISSION_HIRING_RIGHTS.items()
                if set(needed) <= rights}

    for template in STANDARD_ROLES:
        if template["unit"] is None:
            continue
        department_id = unit_ids.get(template["unit"])
        if department_id is None:
            continue
        keys = set(role_bundle_permissions(template["modules"]))
        keys |= hiring_keys(template["modules"])
        if "letters" in template["modules"]:
            keys |= set(STANDARD_LETTER_ACTIONS.get(template["level"], ()))
        add(department_id, keys)

    for key, unit in BUILTIN_ROLE_UNITS.items():
        department_id = unit_ids.get(unit)
        if department_id is None:
            continue
        keys = set(role_granted_permissions(db, key))
        if "letters" in role_permission_set(key, db):
            keys |= set(STANDARD_LETTER_ACTIONS[4])
        add(department_id, keys)

    parents = {row["id"]: row["parent_id"] for row in db.execute(
        "SELECT id, parent_id FROM departments").fetchall()}
    for department_id in list(scope):
        seen = {department_id}
        parent = parents.get(department_id)
        while parent and parent not in seen:
            seen.add(parent)
            scope.setdefault(parent, set()).update(scope[department_id])
            parent = parents.get(parent)

    return {key: sorted(keys) for key, keys in scope.items()}


def _unit_ids(db):
    """Charter unit name -> live department id, for every unit already present."""
    plan = structure_plan(db)
    return {unit["name"]: unit["existing_id"] for unit in plan["units"]}


def _binding_rows(db):
    try:
        return db.execute("""
            SELECT b.department_id, b.role_key, b.position_level,
                   r.name AS role_name, r.is_system, r.active AS role_active
            FROM department_roles AS b
            LEFT JOIN roles AS r ON r.key = b.role_key
            ORDER BY b.department_id, r.name COLLATE NOCASE
        """).fetchall()
    except sqlite3.Error:
        return []


def binding_plan(db):
    """Every suggested binding, whether it is already stored, and who it reaches."""
    unit_ids = _unit_ids(db)
    stored = {(row["department_id"], row["role_key"], row["position_level"])
              for row in _binding_rows(db)}
    role_rows = db.execute(
        "SELECT key, COALESCE(active, 1) AS active FROM roles").fetchall()
    role_keys = {row["key"] for row in role_rows}
    active_keys = {row["key"] for row in role_rows if row["active"]}
    plan = []
    for item in SUGGESTED_BINDINGS:
        if item["unit"] is None:
            department_id, unit_name, present = 0, "Company-wide", True
        else:
            department_id = unit_ids.get(item["unit"])
            unit_name = item["unit"]
            present = department_id is not None
        plan.append({
            "unit": unit_name,
            "department_id": department_id,
            "role_key": item["role_key"],
            "level": item["level"],
            "note": item["note"],
            "unit_present": present,
            "role_present": item["role_key"] in role_keys,
            "role_active": item["role_key"] in active_keys,
            "applied": (department_id is not None
                        and (department_id, item["role_key"], item["level"]) in stored),
        })
    return plan


def bind_role(db, department_id, role_key, position_level):
    """Store one binding, refusing the administrator role and unknown units."""
    if role_key == "admin":
        raise ValueError("The Administrator role cannot be bound to a department.")
    role = db.execute("SELECT active FROM roles WHERE key = ?",
                      (role_key,)).fetchone()
    if role is None:
        raise ValueError("That role does not exist.")
    if not role["active"]:
        raise ValueError("That role is inactive. Activate it before binding it.")
    if position_level not in (-1, 0, 1, 2, 3, 4):
        raise ValueError("Choose a rung, or every rung.")
    if department_id:
        if db.execute("SELECT 1 FROM departments WHERE id = ?",
                      (department_id,)).fetchone() is None:
            raise ValueError("That department does not exist.")
    else:
        department_id = 0
    cursor = db.execute("""
        INSERT OR IGNORE INTO department_roles
            (department_id, role_key, position_level)
        VALUES (?, ?, ?)
    """, (int(department_id), role_key, int(position_level)))
    return cursor.rowcount > 0


def unbind_role(db, department_id, role_key, position_level):
    cursor = db.execute("""
        DELETE FROM department_roles
        WHERE department_id = ? AND role_key = ? AND position_level = ?
    """, (int(department_id), role_key, int(position_level)))
    return cursor.rowcount > 0


def apply_suggested(db, create_missing=True):
    """Create the missing bundles, then store every suggestion not yet stored."""
    created = create_standard_roles(db) if create_missing else []
    unit_ids = _unit_ids(db)
    active_keys = {row["key"] for row in db.execute(
        "SELECT key FROM roles WHERE COALESCE(active, 1) = 1").fetchall()}
    stored = {(row["department_id"], row["role_key"], row["position_level"])
              for row in _binding_rows(db)}
    added, skipped, parked = [], [], []
    for item in SUGGESTED_BINDINGS:
        department_id = 0 if item["unit"] is None else unit_ids.get(item["unit"])
        if department_id is None:
            skipped.append(item["unit"])
            continue
        if (department_id, item["role_key"], item["level"]) in stored:
            continue
        if item["role_key"] not in active_keys:
            parked.append(item["role_key"])
            continue
        if bind_role(db, department_id, item["role_key"], item["level"]):
            added.append(item)
    return {"roles_created": created, "bindings_added": added,
            "units_missing": skipped, "roles_inactive": sorted(set(parked))}


def account_placements(db):
    """Active non-admin accounts with the unit and rung a binding tests against."""
    accounts = db.execute("""
        SELECT * FROM users
        WHERE status = 'Active' AND role <> 'admin'
        ORDER BY full_name
    """).fetchall()
    placements = []
    for account in accounts:
        department_id = account_department_id(db, account)
        placements.append({
            "id": account["id"],
            "name": account["full_name"],
            "role": account["role"],
            "department_id": department_id,
            "chain": set(department_branch_ids(db, department_id))
                     if department_id else set(),
            "level": account_position_level(account, db),
        })
    return placements


def binding_impact(placements, department_id, position_level):
    """How many active accounts a binding reaches right now."""
    reached = 0
    for person in placements:
        if department_id and department_id not in person["chain"]:
            continue
        if position_level != -1 and person["level"] != position_level:
            continue
        reached += 1
    return reached


def department_access_snapshot(db):
    """The unit tree, its stored bindings and every suggestion, for the screen."""
    units = []
    for row in db.execute("""
        SELECT id, name, parent_id, unit_type, COALESCE(is_active, 1) AS is_active
        FROM departments ORDER BY name COLLATE NOCASE
    """).fetchall():
        units.append(dict(row))
    tree = build_department_tree(units)

    placements = account_placements(db)
    direct_counts = {}
    for person in placements:
        if person["department_id"]:
            direct_counts[person["department_id"]] = direct_counts.get(
                person["department_id"], 0) + 1

    bindings = {}
    for row in _binding_rows(db):
        bindings.setdefault(row["department_id"], []).append({
            "department_id": row["department_id"],
            "role_key": row["role_key"],
            "role_name": row["role_name"] or row["role_key"],
            "is_system": bool(row["is_system"]),
            "role_active": bool(row["role_active"]) if row["role_active"] is not None else True,
            "level": row["position_level"],
            "level_label": ("Every rung" if row["position_level"] == -1
                            else POSITION_LEVEL_LABEL.get(
                                row["position_level"], "Staff")),
            "modules": sorted(role_permission_set(row["role_key"], db)),
            "reaches": binding_impact(placements, row["department_id"],
                                      row["position_level"]),
        })

    def dress(nodes):
        for node in nodes:
            node["bindings"] = bindings.get(node["id"], [])
            node["accounts"] = direct_counts.get(node["id"], 0)
            dress(node["children"])

    dress(tree)
    suggestions = binding_plan(db)
    for item in suggestions:
        item["reaches"] = (binding_impact(placements, item["department_id"],
                                          item["level"])
                           if item["department_id"] is not None else 0)

    department_names = {unit["id"]: unit["name"] for unit in units}
    role_options = [dict(row) for row in db.execute("""
        SELECT key, name, is_system, COALESCE(is_standard, 0) AS is_standard,
               COALESCE(department_id, 0) AS department_id
        FROM roles
        WHERE key <> 'admin' AND COALESCE(active, 1) = 1
        ORDER BY is_system DESC, name COLLATE NOCASE
    """).fetchall()]
    for role in role_options:
        role["modules"] = sorted(role_permission_set(role["key"], db))
        role["department_name"] = (
            department_names.get(role["department_id"], "Unassigned")
            if role["department_id"] else "Company-wide")

    grouped = {}
    for role in role_options:
        grouped.setdefault(role["department_name"], []).append(role)
    role_groups = [
        {"label": label, "options": grouped[label]}
        for label in sorted(grouped,
                            key=lambda name: (name != "Company-wide",
                                              name.lower()))
    ]

    bound_ids = set()
    for person in placements:
        for item_department, items in bindings.items():
            if item_department and item_department not in person["chain"]:
                continue
            for binding in items:
                if not binding["role_active"]:
                    continue
                if (binding["level"] == -1
                        or person["level"] == binding["level"]):
                    bound_ids.add(person["id"])
                    break
            if person["id"] in bound_ids:
                break

    existing_keys = {row["key"] for row in db.execute(
        "SELECT key FROM roles").fetchall()}
    missing_roles = [template["name"] for template in STANDARD_ROLES
                     if template["key"] not in existing_keys]

    return {
        "tree": tree,
        "bindings": bindings.get(0, []),
        "company_bindings": bindings.get(0, []),
        "suggestions": suggestions,
        "role_options": role_options,
        "role_groups": role_groups,
        "departments": [
            {"id": unit["id"], "name": unit["name"],
             "unit_type": unit.get("unit_type") or "Department"}
            for unit in sorted(units, key=lambda row: row["name"].lower())],
        "level_labels": POSITION_LEVEL_LABEL,
        "unmatched_units": UNMATCHED_UNITS,
        "accounts": len(placements),
        "bound_accounts": len(bound_ids),
        "binding_total": sum(len(items) for items in bindings.values()),
        "bound_units": len([key for key in bindings if key]),
        "missing_roles": missing_roles,
    }
