"""The mapping desk: directory accounts and departments matched to HR records.

Nothing here edits an employee row. The synchronization fills
``integration_ad_users``; this module decides which company department an AD
department string means, and it opens a pending ``integration_employee_mappings``
row per account so a person can be matched to an employee by hand later.
"""


def mapping_rows(db, integration_id):
    """Every directory account with its department and employee mapping."""
    return db.execute("""
        SELECT ad.*,
               employee_mapping.mapping_status,
               employee_mapping.hr_employee_id,
               department_mapping.hr_department_id,
               department_mapping.hr_department_name
        FROM integration_ad_users AS ad
        LEFT JOIN integration_employee_mappings AS employee_mapping
            ON employee_mapping.integration_id = ad.integration_id
           AND employee_mapping.ad_object_guid = ad.object_guid
        LEFT JOIN integration_department_mappings AS department_mapping
            ON department_mapping.integration_id = ad.integration_id
           AND department_mapping.ad_department = TRIM(ad.department)
           AND department_mapping.is_active = 1
        WHERE ad.integration_id = ?
        ORDER BY ad.full_name COLLATE NOCASE, ad.username
    """, (integration_id,)).fetchall()


def department_choices(db):
    return db.execute("""
        SELECT id, name FROM departments ORDER BY name COLLATE NOCASE
    """).fetchall()


def ad_departments(db, integration_id):
    """The distinct department strings the directory holds, with their mapping."""
    return db.execute("""
        SELECT TRIM(ad.department) AS ad_department,
               mapping.hr_department_id,
               mapping.hr_department_name,
               COUNT(*) AS accounts
        FROM integration_ad_users AS ad
        LEFT JOIN integration_department_mappings AS mapping
            ON mapping.integration_id = ad.integration_id
           AND mapping.ad_department = TRIM(ad.department)
           AND mapping.is_active = 1
        WHERE ad.integration_id = ? AND TRIM(COALESCE(ad.department, '')) != ''
        GROUP BY TRIM(ad.department), mapping.hr_department_id, mapping.hr_department_name
        ORDER BY TRIM(ad.department) COLLATE NOCASE
    """, (integration_id,)).fetchall()


def save_department_mapping(db, integration_id, ad_department, hr_department_id):
    """Point one directory department string at one company department."""
    ad_department = (ad_department or "").strip()[:120]
    if not ad_department:
        raise ValueError("A directory department name is required.")

    department = None
    if hr_department_id:
        department = db.execute(
            "SELECT id, name FROM departments WHERE id = ?",
            (hr_department_id,)).fetchone()
        if department is None:
            raise ValueError("That company department no longer exists.")

    if department is None:
        db.execute("""
            DELETE FROM integration_department_mappings
            WHERE integration_id = ? AND ad_department = ?
        """, (integration_id, ad_department))
        return ""

    db.execute("""
        INSERT INTO integration_department_mappings
            (integration_id, ad_department, hr_department_id, hr_department_name,
             is_active, updated_at)
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(integration_id, ad_department)
        DO UPDATE SET
            hr_department_id = excluded.hr_department_id,
            hr_department_name = excluded.hr_department_name,
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
    """, (integration_id, ad_department, department["id"], department["name"]))
    return department["name"]


def prepare_employee_mappings(db, integration_id):
    """Open a pending mapping row for each directory account that has none."""
    rows = db.execute("""
        SELECT ad.object_guid
        FROM integration_ad_users AS ad
        LEFT JOIN integration_employee_mappings AS mapping
            ON mapping.integration_id = ad.integration_id
           AND mapping.ad_object_guid = ad.object_guid
        WHERE ad.integration_id = ? AND mapping.id IS NULL
    """, (integration_id,)).fetchall()
    for row in rows:
        db.execute("""
            INSERT INTO integration_employee_mappings
                (integration_id, ad_object_guid, mapping_status)
            VALUES (?, ?, 'pending')
        """, (integration_id, row["object_guid"]))
    return len(rows)
