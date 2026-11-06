"""The company charter's organization, kept as data.

The charter places a chief executive above four directorates and thirteen
departments - Information Technology sits directly under the CEO - and names the
positions that fill them. This module holds that reference so the administrator
can publish it into the live structure from the hierarchy screen.

Applying it is additive and repeatable. A unit is matched by its own name first
and then by the names already in use (an existing "Budget" department answers
for the charter's "Budget Department"), a position is matched by title inside
its unit, and only missing rows are created. A matched unit keeps its name, its
leadership seat and every field a person already set; a missing parent link or
reporting line is filled in, never overwritten. Nothing is renamed, retired or
deleted, so existing employees, accounts and approval history stay pointed at
the same rows.
"""


STANDARD_UNITS = (
    {
        "name": "Finance Management Directorate",
        "unit_type": "Directorate",
        "leadership_position": "Director",
        "description": "Leads Finance, Budget and Payroll.",
        "parent": None,
        "aliases": (),
    },
    {
        "name": "Finance Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Accounting operations, invoices, payments and financial"
                       " reporting.",
        "parent": "Finance Management Directorate",
        "aliases": ("Finance",),
    },
    {
        "name": "Budget Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Annual and quarterly budgets, allocations and utilisation.",
        "parent": "Finance Management Directorate",
        "aliases": ("Budget",),
    },
    {
        "name": "Payroll Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Monthly payroll, salary calculations, allowances and"
                       " deductions.",
        "parent": "Finance Management Directorate",
        "aliases": ("Payroll",),
    },
    {
        "name": "Property & Procurement Directorate",
        "unit_type": "Directorate",
        "leadership_position": "Director",
        "description": "Leads Procurement, Property & Asset Management and"
                       " Inventory & Warehouse.",
        "parent": None,
        "aliases": (),
    },
    {
        "name": "Procurement Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Suppliers, quotations and purchase orders.",
        "parent": "Property & Procurement Directorate",
        "aliases": ("Procurement",),
    },
    {
        "name": "Property & Asset Management Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Asset register, allocation, transfers and disposal"
                       " requests.",
        "parent": "Property & Procurement Directorate",
        "aliases": ("Property & Asset Management", "Property", "Assets"),
    },
    {
        "name": "Inventory & Warehouse Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Stock receipts, issues, transfers and warehouse counts.",
        "parent": "Property & Procurement Directorate",
        "aliases": ("Inventory & Warehouse", "Inventory", "Warehouse"),
    },
    {
        "name": "Human Resources & Administration Directorate",
        "unit_type": "Directorate",
        "leadership_position": "Director",
        "description": "Leads Human Resources, Administration and Recruitment.",
        "parent": None,
        "aliases": (),
    },
    {
        "name": "Human Resources Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Employee records, attendance, leave and performance.",
        "parent": "Human Resources & Administration Directorate",
        "aliases": ("Human Resources", "HR"),
    },
    {
        "name": "Administration Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Office administration, facilities and correspondence.",
        "parent": "Human Resources & Administration Directorate",
        "aliases": ("Administration", "Admin"),
    },
    {
        "name": "Recruitment Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Vacancies, applications, examinations and hiring"
                       " results.",
        "parent": "Human Resources & Administration Directorate",
        "aliases": ("Recruitment", "Hiring"),
    },
    {
        "name": "Business Development & Operations Directorate",
        "unit_type": "Directorate",
        "leadership_position": "Director",
        "description": "Leads Sales & Marketing, Customer Service and Project"
                       " Management.",
        "parent": None,
        "aliases": (),
    },
    {
        "name": "Sales & Marketing Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Sales, quotations, marketing campaigns and business"
                       " development.",
        "parent": "Business Development & Operations Directorate",
        "aliases": ("Sales & Marketing", "Sales", "Marketing"),
    },
    {
        "name": "Customer Service Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Customer inquiries, complaints, feedback and after-sales"
                       " support.",
        "parent": "Business Development & Operations Directorate",
        "aliases": ("Customer Service",),
    },
    {
        "name": "Project Management Department",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Project planning, schedules, milestones and site"
                       " progress.",
        "parent": "Business Development & Operations Directorate",
        "aliases": ("Project Management", "Projects"),
    },
    {
        "name": "Information Technology",
        "unit_type": "Department",
        "leadership_position": "Manager",
        "description": "Independent department reporting to the CEO:"
                       " infrastructure, systems and security.",
        "parent": None,
        "aliases": ("ICT", "IT"),
    },
)


STANDARD_POSITIONS = (
    {"title": "Chief Executive Officer", "unit": None, "level": 0,
     "reports_to": None},

    {"title": "Finance Director", "unit": "Finance Management Directorate",
     "level": 2, "reports_to": "Chief Executive Officer"},
    {"title": "Finance Manager", "unit": "Finance Department", "level": 3,
     "reports_to": "Finance Director"},
    {"title": "Senior Accountant", "unit": "Finance Department", "level": 4,
     "reports_to": "Finance Manager"},
    {"title": "Accountant", "unit": "Finance Department", "level": 4,
     "reports_to": "Finance Manager"},
    {"title": "Accounts Assistant", "unit": "Finance Department", "level": 4,
     "reports_to": "Finance Manager"},
    {"title": "Cashier", "unit": "Finance Department", "level": 4,
     "reports_to": "Finance Manager"},
    {"title": "Budget Manager", "unit": "Budget Department", "level": 3,
     "reports_to": "Finance Director"},
    {"title": "Budget Officer", "unit": "Budget Department", "level": 4,
     "reports_to": "Budget Manager"},
    {"title": "Budget Analyst", "unit": "Budget Department", "level": 4,
     "reports_to": "Budget Manager"},
    {"title": "Payroll Manager", "unit": "Payroll Department", "level": 3,
     "reports_to": "Finance Director"},
    {"title": "Payroll Officer", "unit": "Payroll Department", "level": 4,
     "reports_to": "Payroll Manager"},
    {"title": "Payroll Assistant", "unit": "Payroll Department", "level": 4,
     "reports_to": "Payroll Manager"},

    {"title": "Property & Procurement Director",
     "unit": "Property & Procurement Directorate", "level": 2,
     "reports_to": "Chief Executive Officer"},
    {"title": "Procurement Manager", "unit": "Procurement Department",
     "level": 3, "reports_to": "Property & Procurement Director"},
    {"title": "Senior Procurement Officer", "unit": "Procurement Department",
     "level": 4, "reports_to": "Procurement Manager"},
    {"title": "Procurement Officer", "unit": "Procurement Department",
     "level": 4, "reports_to": "Procurement Manager"},
    {"title": "Procurement Assistant", "unit": "Procurement Department",
     "level": 4, "reports_to": "Procurement Manager"},
    {"title": "Property & Asset Manager",
     "unit": "Property & Asset Management Department", "level": 3,
     "reports_to": "Property & Procurement Director"},
    {"title": "Asset Management Officer",
     "unit": "Property & Asset Management Department", "level": 4,
     "reports_to": "Property & Asset Manager"},
    {"title": "Property Officer",
     "unit": "Property & Asset Management Department", "level": 4,
     "reports_to": "Property & Asset Manager"},
    {"title": "Inventory & Warehouse Manager",
     "unit": "Inventory & Warehouse Department", "level": 3,
     "reports_to": "Property & Procurement Director"},
    {"title": "Inventory Controller", "unit": "Inventory & Warehouse Department",
     "level": 4, "reports_to": "Inventory & Warehouse Manager"},
    {"title": "Storekeeper", "unit": "Inventory & Warehouse Department",
     "level": 4, "reports_to": "Inventory & Warehouse Manager"},
    {"title": "Warehouse Assistant", "unit": "Inventory & Warehouse Department",
     "level": 4, "reports_to": "Inventory & Warehouse Manager"},

    {"title": "HR & Administration Director",
     "unit": "Human Resources & Administration Directorate", "level": 2,
     "reports_to": "Chief Executive Officer"},
    {"title": "HR Manager", "unit": "Human Resources Department", "level": 3,
     "reports_to": "HR & Administration Director"},
    {"title": "HR Officer", "unit": "Human Resources Department", "level": 4,
     "reports_to": "HR Manager"},
    {"title": "Employee Relations Officer", "unit": "Human Resources Department",
     "level": 4, "reports_to": "HR Manager"},
    {"title": "Administration Manager", "unit": "Administration Department",
     "level": 3, "reports_to": "HR & Administration Director"},
    {"title": "Administration Officer", "unit": "Administration Department",
     "level": 4, "reports_to": "Administration Manager"},
    {"title": "Office Assistant", "unit": "Administration Department",
     "level": 4, "reports_to": "Administration Manager"},
    {"title": "Recruitment Manager", "unit": "Recruitment Department",
     "level": 3, "reports_to": "HR & Administration Director"},
    {"title": "Recruitment Officer", "unit": "Recruitment Department",
     "level": 4, "reports_to": "Recruitment Manager"},
    {"title": "Examination Officer", "unit": "Recruitment Department",
     "level": 4, "reports_to": "Recruitment Manager"},

    {"title": "Business Development & Operations Director",
     "unit": "Business Development & Operations Directorate", "level": 2,
     "reports_to": "Chief Executive Officer"},
    {"title": "Sales & Marketing Manager",
     "unit": "Sales & Marketing Department", "level": 3,
     "reports_to": "Business Development & Operations Director"},
    {"title": "Sales Officer", "unit": "Sales & Marketing Department",
     "level": 4, "reports_to": "Sales & Marketing Manager"},
    {"title": "Marketing Officer", "unit": "Sales & Marketing Department",
     "level": 4, "reports_to": "Sales & Marketing Manager"},
    {"title": "Business Development Officer",
     "unit": "Sales & Marketing Department", "level": 4,
     "reports_to": "Sales & Marketing Manager"},
    {"title": "Customer Service Manager", "unit": "Customer Service Department",
     "level": 3, "reports_to": "Business Development & Operations Director"},
    {"title": "Customer Service Officer", "unit": "Customer Service Department",
     "level": 4, "reports_to": "Customer Service Manager"},
    {"title": "Customer Relations Officer", "unit": "Customer Service Department",
     "level": 4, "reports_to": "Customer Service Manager"},
    {"title": "Project Manager", "unit": "Project Management Department",
     "level": 3, "reports_to": "Business Development & Operations Director"},
    {"title": "Project Coordinator", "unit": "Project Management Department",
     "level": 4, "reports_to": "Project Manager"},
    {"title": "Project Officer", "unit": "Project Management Department",
     "level": 4, "reports_to": "Project Manager"},
    {"title": "Site Supervisor", "unit": "Project Management Department",
     "level": 4, "reports_to": "Project Manager"},

    {"title": "IT Manager", "unit": "Information Technology", "level": 3,
     "reports_to": "Chief Executive Officer"},
    {"title": "System Administrator", "unit": "Information Technology",
     "level": 4, "reports_to": "IT Manager"},
    {"title": "Network Administrator", "unit": "Information Technology",
     "level": 4, "reports_to": "IT Manager"},
    {"title": "Software Developer", "unit": "Information Technology",
     "level": 4, "reports_to": "IT Manager"},
    {"title": "Information Security Officer", "unit": "Information Technology",
     "level": 4, "reports_to": "IT Manager"},
    {"title": "IT Support Officer", "unit": "Information Technology",
     "level": 4, "reports_to": "IT Manager"},
)


def _key(value):
    return (value or "").strip().casefold()


def _departments(conn):
    return conn.execute("""
        SELECT id, name, description, is_active, leadership_position,
               parent_id, unit_type
        FROM departments
        ORDER BY id
    """).fetchall()


def _positions(conn):
    return conn.execute("""
        SELECT id, title, department_id, level, reports_to_position_id,
               COALESCE(is_active, 1) AS is_active
        FROM organizational_positions
        ORDER BY id
    """).fetchall()


def _resolved_units(conn):
    """Each charter unit beside the live department that already answers it."""
    departments = _departments(conn)
    by_name = {}
    for row in departments:
        by_name.setdefault(_key(row["name"]), row)

    claimed = set()
    resolved = []
    for unit in STANDARD_UNITS:
        match = by_name.get(_key(unit["name"]))
        matched_by = unit["name"] if match is not None else None
        if match is None:
            for alias in unit["aliases"]:
                candidate = by_name.get(_key(alias))
                if candidate is not None and candidate["id"] not in claimed:
                    match = candidate
                    matched_by = alias
                    break
        if match is not None and match["id"] in claimed:
            match, matched_by = None, None
        if match is not None:
            claimed.add(match["id"])
        resolved.append({"unit": unit, "row": match, "matched_by": matched_by})
    return resolved


def _position_index(conn):
    index = {}
    for row in _positions(conn):
        index.setdefault((_key(row["title"]), row["department_id"]), row)
    return index


def _position_state(item, row):
    return {
        "title": item["title"],
        "level": item["level"],
        "reports_to": item["reports_to"],
        "exists": row is not None,
        "linked": row is not None and row["reports_to_position_id"] is not None,
    }


def structure_plan(conn):
    """What applying the charter would add, without changing anything.

    The screen reads this to show each unit as in place or still to create and
    to count the positions and reporting lines that are missing.
    """
    resolved = _resolved_units(conn)
    index = _position_index(conn)
    units = []
    position_existing = position_new = links_pending = 0

    for entry in resolved:
        unit = entry["unit"]
        department_id = entry["row"]["id"] if entry["row"] is not None else None
        items = []
        for item in STANDARD_POSITIONS:
            if item["unit"] != unit["name"]:
                continue
            row = index.get((_key(item["title"]), department_id))
            items.append(_position_state(item, row))
            position_existing += 1 if row is not None else 0
            position_new += 1 if row is None else 0
            if (row is not None and item["reports_to"]
                    and row["reports_to_position_id"] is None):
                links_pending += 1
        units.append({
            "name": unit["name"],
            "unit_type": unit["unit_type"],
            "description": unit["description"],
            "parent": unit["parent"] or "Chief Executive Officer",
            "existing_id": department_id,
            "existing_name": entry["row"]["name"] if entry["row"] else "",
            "matched_by": entry["matched_by"],
            "positions": items,
        })

    leadership = []
    for item in STANDARD_POSITIONS:
        if item["unit"] is not None:
            continue
        row = index.get((_key(item["title"]), None))
        leadership.append(_position_state(item, row))
        position_existing += 1 if row is not None else 0
        position_new += 1 if row is None else 0

    unit_new = sum(1 for entry in resolved if entry["row"] is None)
    return {
        "units": units,
        "leadership": leadership,
        "unit_total": len(STANDARD_UNITS),
        "unit_new": unit_new,
        "position_total": len(STANDARD_POSITIONS),
        "position_existing": position_existing,
        "position_new": position_new,
        "links_pending": links_pending,
        "ready": (unit_new == 0 and position_new == 0 and links_pending == 0),
    }


def apply_standard_structure(conn):
    """Create the charter's missing units, positions and reporting lines.

    The caller owns the transaction and commits it. Matched rows are left with
    their names and settings; only an empty parent or reporting link is filled.
    """
    resolved = _resolved_units(conn)
    unit_ids = {}
    units_created, units_matched = [], []

    for entry in resolved:
        unit = entry["unit"]
        parent_id = None
        if unit["parent"]:
            parent_id = unit_ids.get(unit["parent"])
            if parent_id is None:
                raise ValueError(
                    "The charter parent '{}' was not resolved.".format(
                        unit["parent"]))

        if entry["row"] is None:
            department_id = conn.execute("""
                INSERT INTO departments
                    (name, description, leadership_position, unit_type, parent_id)
                VALUES (?, ?, ?, ?, ?)
            """, (unit["name"], unit["description"],
                  unit["leadership_position"], unit["unit_type"],
                  parent_id)).lastrowid
            units_created.append(unit["name"])
        else:
            department_id = entry["row"]["id"]
            if parent_id is not None and entry["row"]["parent_id"] is None:
                conn.execute(
                    "UPDATE departments SET parent_id = ? WHERE id = ?",
                    (parent_id, department_id))
            units_matched.append(entry["row"]["name"])
        unit_ids[unit["name"]] = department_id

    index = _position_index(conn)
    position_ids = {}
    positions_created, positions_matched = [], []
    links_added = 0

    for item in STANDARD_POSITIONS:
        department_id = unit_ids.get(item["unit"]) if item["unit"] else None
        if item["unit"] and department_id is None:
            raise ValueError(
                "The charter unit '{}' was not resolved.".format(item["unit"]))

        parent_id = position_ids.get(_key(item["reports_to"])) \
            if item["reports_to"] else None
        row = index.get((_key(item["title"]), department_id))

        if row is None:
            position_id = conn.execute("""
                INSERT INTO organizational_positions
                    (title, department_id, level, reports_to_position_id)
                VALUES (?, ?, ?, ?)
            """, (item["title"], department_id, item["level"],
                  parent_id)).lastrowid
            positions_created.append(item["title"])
        else:
            position_id = row["id"]
            positions_matched.append(item["title"])
            if parent_id is not None and row["reports_to_position_id"] is None:
                conn.execute("""
                    UPDATE organizational_positions
                    SET reports_to_position_id = ?
                    WHERE id = ?
                """, (parent_id, position_id))
                links_added += 1
        position_ids[_key(item["title"])] = position_id

    return {
        "units_created": units_created,
        "units_matched": units_matched,
        "positions_created": positions_created,
        "positions_matched": positions_matched,
        "links_added": links_added,
    }
