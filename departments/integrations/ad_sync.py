"""Writing what the directory returns into the mapping tables.

The AD accounts a connection has seen are kept per integration, keyed by the
account's objectGUID, so a rename does not create a second person and a deleted
account does not silently reappear. ``run_integration_sync`` in
``synchronization.py`` owns the log row around this call.

The read itself is the directory module's LDAP provider - the same bind, search
and attribute decoding every directory screen uses - so a registry-only
connection and a profiled one see the domain identically.
"""

COLUMNS = ("object_guid", "username", "full_name", "email", "phone",
           "department", "job_title", "distinguished_name", "is_active")


def store_ad_users(db, integration_id, users):
    """Upsert every account; returns (new, updated)."""
    inserted = 0
    updated = 0
    for user in users:
        known = db.execute("""
            SELECT id FROM integration_ad_users
            WHERE integration_id = ? AND object_guid = ?
        """, (integration_id, user["object_guid"])).fetchone()
        db.execute("""
            INSERT INTO integration_ad_users
                (integration_id, object_guid, username, full_name, email, phone,
                 department, job_title, distinguished_name, is_active,
                 last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP)
            ON CONFLICT(integration_id, object_guid)
            DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                email = excluded.email,
                phone = excluded.phone,
                department = excluded.department,
                job_title = excluded.job_title,
                distinguished_name = excluded.distinguished_name,
                is_active = excluded.is_active,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
        """, tuple([integration_id] + [user[name] for name in COLUMNS]))
        if known:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def synchronize_ad_users(db, integration, search_base):
    """Read the directory and store it; returns the message for the log row."""
    from services.directory import integration_ldap_spec
    from services.directory import ldap_fetch_users

    spec = integration_ldap_spec(db, integration, search_base)
    endpoint = (spec["server_url"] or "").lower()
    if not endpoint.startswith("ldaps://"):
        raise ValueError("Synchronizing over plain LDAP is not allowed; change the"
                         " endpoint to ldaps://.")
    entries, problem = ldap_fetch_users(spec)
    if problem:
        raise ValueError(problem)
    users = [{
        "object_guid": entry["object_id"],
        "username": entry.get("username") or "",
        "full_name": entry.get("full_name") or "",
        "email": entry.get("email") or "",
        "phone": entry.get("phone") or "",
        "department": entry.get("department") or "",
        "job_title": entry.get("title") or "",
        "distinguished_name": entry.get("dn") or "",
        "is_active": 1 if entry.get("enabled") else 0,
    } for entry in entries if entry.get("object_id")]
    inserted, updated = store_ad_users(db, integration["id"], users)
    return ("Active Directory synchronization completed. "
            "New accounts: {}; updated accounts: {}.".format(inserted, updated))
