"""The tasks, functions and reports each department's desks carry.

The registry is the navigation rail itself: every link SIDEBAR_NAV offers is a
task, read at request time and resolved against the app's URL map, so a screen
added to the rail appears under its department without a second list to keep in
step. A group's visible module names the desk that owns it; the module-less
groups name theirs in ``GROUP_DESK``.

A unit inherits the desks of the units filed below it, and a role filed or bound
on the unit adds the modules it opens. A unit with no roles yet still shows the
desks its name stands for, which is what lets the console answer "what does this
department do?" before any account has been placed in it.

The ``held`` flag answers whether the unit's effective roles currently grant the
task's requirement. It is informational: the department console is read by the
administrator, who is told which right is missing rather than shown a dead link.
Letter tasks are granted per account, so their row says so instead of guessing.
"""

from flask import current_app
from flask import url_for
from werkzeug.routing import BuildError

from core.config import MODULE_LABEL
from core.config import PERMISSION_BY_KEY
from core.config import ROLE_LABEL
from core.config import SIDEBAR_GROUP_ICONS
from core.config import SIDEBAR_NAV
from services.letters import LETTER_PERMISSION_LABEL
import reporting
import sqlite3


# A rail group with no module of its own still belongs to a desk.
GROUP_DESK = {
    "Procurement Management": "procurement",
    "Careers": "hiring",
    "Reporting": "reporting",
}


# The module a desk needs before its rail group is drawn.
DESK_MODULE = {
    "accounts": "accounts",
    "integrations": "integrations",
    "procurement": "procurement",
    "letters": "letters",
    "hr": "hr",
    "finance": "finance",
    "budget": "budget",
    "payroll": "payroll",
    "inventory": "inventory",
    "asset": "asset",
    "marketing": "marketing",
    "project": "project",
    "hiring": "hr",
    "approvals": "approvals",
    "audit": "audit",
    "reporting": None,
}


DESK_TITLE = {
    "accounts": "Admin Management",
    "integrations": "System Integration",
    "procurement": "Procurement Management",
    "letters": "Letter Management",
    "hr": "HR Management",
    "finance": "Finance Management",
    "budget": "Budget Management",
    "payroll": "Payroll Management",
    "inventory": "Inventory & Stock",
    "asset": "Asset Management",
    "marketing": "Marketing Management",
    "project": "Project Management",
    "hiring": "Recruitment Desk",
    "approvals": "Approvals",
    "audit": "Audit & Oversight",
    "reporting": "Reporting",
}


# The desks a unit name stands for. Every match adds its desks, so a directorate
# name can carry more than one and its departments still add their own.
NAME_DESKS = (
    (("cyber", "audit", "information security"), ("audit",)),
    (("ict", "information technology"), ("integrations", "audit")),
    (("administration",), ("accounts", "letters")),
    (("human resources", "human resource"), ("hr", "hiring")),
    (("recruitment",), ("hiring", "hr")),
    (("finance",), ("finance",)),
    (("budget",), ("budget",)),
    (("payroll",), ("payroll",)),
    (("procurement", "purchasing"), ("procurement",)),
    (("inventory", "warehouse", "store"), ("inventory",)),
    (("asset", "property"), ("asset",)),
    (("marketing", "sales", "business development"), ("marketing",)),
    (("customer",), ("letters",)),
    (("project",), ("project",)),
)


TASK_KIND = (
    ("Approval", ("approval", "awaiting")),
    ("Report", ("report",)),
    ("Oversight", ("audit", "oversight", "management overview")),
    ("Settings", ("setting", "configuration", "hierarchy setup")),
    ("Register", ("register", "employees", "inventory items", "leads",
                  "campaigns", "customers", "applicants", "vacancies",
                  "requisitions", "orders")),
    ("Dashboard", ("dashboard", "overview", "my tasks", "my applications",
                   "my payslips")),
)


def department_task_view(db, department_id):
    """The current tasks for one unit, grouped by the desk they belong to."""
    units = db.execute(
        "SELECT id, name, parent_id FROM departments").fetchall()
    branch = _branch_ids(units, department_id)
    names = [row["name"] for row in units if row["id"] in branch]

    role_keys = _role_keys(db, branch, _ancestor_ids(units, department_id))
    modules, permissions = _role_grants(db, role_keys)
    names_by_key = _role_names(db, role_keys)
    name_desks = set()
    for name in names:
        name_desks |= _name_desks(name)

    groups, seen, total, held_total = [], set(), 0, 0
    for label, module, links in SIDEBAR_NAV:
        desk = module or GROUP_DESK.get(label)
        if desk not in DESK_MODULE:
            continue
        required = DESK_MODULE[desk]
        if required is not None and not (
                required in modules or desk in name_desks):
            continue
        tasks = []
        for raw in links:
            task = _task(raw, modules, permissions, role_keys)
            if task is None:
                continue
            key = (task["label"], task["href"])
            if key in seen:
                continue
            seen.add(key)
            tasks.append(task)
            total += 1
            if task["held"]:
                held_total += 1
        if desk == "reporting":
            for task in _report_tasks(modules, permissions):
                key = (task["label"], task["href"])
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(task)
                total += 1
                if task["held"]:
                    held_total += 1
        if tasks:
            groups.append({
                "label": label or DESK_TITLE.get(desk, desk.title()),
                "desk": desk,
                "icon": SIDEBAR_GROUP_ICONS.get(label or "", ""),
                "tasks": tasks,
                "open": sum(1 for task in tasks if task["held"]),
            })

    return {
        "groups": groups,
        "total": total,
        "open": held_total,
        "desks": sorted({group["label"] for group in groups}),
        "roles": [names_by_key[key] for key in sorted(names_by_key)],
    }


def _branch_ids(units, department_id):
    """The unit and every unit filed below it."""
    children = {}
    for row in units:
        children.setdefault(row["parent_id"], []).append(row["id"])
    found, pending = {department_id}, [department_id]
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def _ancestor_ids(units, department_id):
    parents = {row["id"]: row["parent_id"] for row in units}
    found, current = [], parents.get(department_id)
    while current and current not in found:
        found.append(current)
        current = parents.get(current)
    return found


def _name_desks(name):
    lowered = (name or "").lower()
    desks = set()
    for words, matched in NAME_DESKS:
        if any(word in lowered for word in words):
            desks |= set(matched)
    return desks


def _role_keys(db, branch, ancestors):
    places = sorted(branch)
    marks = ", ".join("?" for _ in places)
    keys = {row["key"] for row in db.execute(
        "SELECT key FROM roles WHERE department_id IN ({})".format(marks),
        tuple(places))}
    for row in db.execute(
            "SELECT role_key FROM department_roles"
            " WHERE department_id IN ({})".format(marks), tuple(places)):
        keys.add(row["role_key"])
    if ancestors:
        marks = ", ".join("?" for _ in ancestors)
        # A unit-wide binding on a directorate reaches every seat below it.
        for row in db.execute(
                "SELECT role_key FROM department_roles"
                " WHERE department_id IN ({}) AND position_level = -1".format(
                    marks), tuple(ancestors)):
            keys.add(row["role_key"])
    keys.discard("admin")
    return keys


def _role_names(db, role_keys):
    if not role_keys:
        return {}
    marks = ", ".join("?" for _ in role_keys)
    names = {row["key"]: row["name"] for row in db.execute(
        "SELECT key, name FROM roles WHERE key IN ({})".format(marks),
        tuple(sorted(role_keys)))}
    for key in role_keys:
        names.setdefault(key, ROLE_LABEL.get(key, key.replace("_", " ").title()))
    return names


def _role_grants(db, role_keys):
    """Modules and permission keys the unit's roles carry."""
    from core.access import role_granted_permissions
    from core.access import role_permission_set

    modules, permissions = set(), set()
    for key in role_keys:
        try:
            modules |= set(role_permission_set(key, db))
            permissions |= set(role_granted_permissions(db, key))
        except sqlite3.Error:
            continue
    return modules, permissions


def _task(raw, modules, permissions, role_keys):
    link = raw if isinstance(raw, dict) else {"label": raw[0], "target": raw[1]}
    href = _href(link)
    if href is None:
        return None

    required = link.get("modules") or ()
    keys = link.get("permission") or link.get("permissions") or ()
    if isinstance(keys, str):
        keys = (keys,)
    roles = link.get("roles") or ()
    letter = link.get("letter")
    categories = bool(link.get("letter_categories"))

    held = True
    if required:
        held = held and bool(set(required) & modules)
    if keys:
        held = held and any(key in permissions for key in keys)
    if roles:
        held = held and bool(set(roles) & role_keys)
    if letter or categories:
        # Letter actions are granted per account, not carried by the role.
        held = None

    return {
        "label": link["label"],
        "href": href,
        "kind": _kind(link["label"]),
        "requires": _requires(required, keys, roles, letter, categories),
        "held": held,
        "letter": bool(letter or categories),
    }


def _href(link):
    target = link.get("target")
    if not target or target not in current_app.view_functions:
        return None
    try:
        href = url_for(target, **(link.get("params") or {}))
    except (BuildError, TypeError):
        return None
    anchor = link.get("anchor")
    return "#".join((href, anchor)) if anchor else href


def _kind(label):
    lowered = label.lower()
    for kind, words in TASK_KIND:
        if any(word in lowered for word in words):
            return kind
    return "Desk"


def _report_tasks(modules, permissions):
    """The report catalogue the unit's modules (or permission) already opens."""
    tasks = []
    for spec in reporting.REPORT_SPECS:
        if spec.get("personal"):
            continue
        needed = set(spec.get("modules") or ())
        key = spec.get("permission")
        # A report with no module and no permission is a personal or team
        # report every desk can open; it is not this department's report.
        if not needed and not key:
            continue
        if needed and not (needed & modules):
            continue
        if key and key not in permissions:
            continue
        try:
            href = url_for("reports_centre", report=spec["key"])
        except BuildError:
            continue
        requires = []
        if key:
            requires.append(PERMISSION_BY_KEY.get(key, {}).get("label") or key)
        requires.extend(MODULE_LABEL.get(module, module) for module in needed)
        tasks.append({
            "label": spec["label"],
            "href": href,
            "kind": "Report",
            "requires": requires or ["Signed-in staff"],
            "held": True,
            "letter": False,
        })
    return tasks


def _requires(modules, keys, roles, letter, categories):
    needs = []
    for key in keys:
        label = (PERMISSION_BY_KEY.get(key, {}).get("label")
                 or LETTER_PERMISSION_LABEL.get(key))
        needs.append(label or key)
    for module in modules:
        needs.append(MODULE_LABEL.get(module, module))
    for role in roles:
        needs.append("Role: " + ROLE_LABEL.get(role, role))
    if letter:
        needs.append(LETTER_PERMISSION_LABEL.get(letter, letter))
    if categories:
        needs.append("Letter categories")
    if not needs:
        needs.append("Signed-in staff")
    return needs
