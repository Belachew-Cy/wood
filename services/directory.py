"""Enterprise directory integration: connections, synchronization and access mapping.

Two directory kinds are served from one code path. ``entra`` talks to Microsoft
Graph with a confidential app registration (the same MSAL package the Microsoft
sign-in already uses); ``ldap`` talks to an on-premises domain controller over
LDAP or LDAPS. Both are read-only against the directory: this module never
creates, edits or deletes an object in Active Directory, it only brings
identities, group membership and account state in.

Everything stored here is scoped to a connection row. A local account is only
touched when a synchronization has linked it to an object on that connection, so
the accounts every other screen already manages stay under local control until
an administrator hands one over.
"""

from core.config import DIRECTORY_GRAPH_TIMEOUT
from core.config import DIRECTORY_LDAP_TIMEOUT
from core.config import DIRECTORY_MAX_MEMBERS
from core.config import DIRECTORY_MAX_USERS
from core.config import DIRECTORY_RETAINED_RUNS
from core.config import USER_ROLES
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import base64
import hashlib
import json
import re
import sqlite3
import uuid


try:
    import ldap3
    from ldap3 import SUBTREE as LDAP_SUBTREE
    from ldap3 import TLS as LDAP_TLS
except ImportError:  # The screens stay usable; they report the missing package.
    ldap3 = None
    LDAP_SUBTREE = LDAP_TLS = None

try:
    import msal
except ImportError:
    msal = None

try:
    import requests
except ImportError:
    requests = None

try:
    from cryptography.fernet import Fernet
    from cryptography.fernet import InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    Fernet = InvalidToken = HKDF = hashes = None


DIRECTORY_KINDS = ("entra", "ldap")

DIRECTORY_KIND_LABEL = {"entra": "Microsoft Entra ID", "ldap": "Active Directory (LDAP)"}

DIRECTORY_SECRET_PLACEHOLDER = "WK_DIRECTORY_KEEP"

# A local password that cannot be used. Directory-managed accounts sign in against
# the domain, so the column keeps a value that is well-formed enough to satisfy the
# NOT NULL rule and is refused before it ever reaches the hash checker.
DIRECTORY_NO_PASSWORD = "!"

# Active Directory's LDAP_MATCHING_RULE_IN_CHAIN: "is a member of, at any depth".
# Nested group membership is answered by the domain controller, so the sync does
# not have to walk group trees itself.
LDAP_NESTED_MATCH = "1.2.840.113556.1.4.1941"

ACCOUNT_DISABLED_FLAG = 2

DIRECTORY_STATUS = ("untested", "ok", "warning", "failed")

DIRECTORY_RUN_STATUS = ("running", "success", "partial", "failed", "preview")

DIRECTORY_ITEM_ACTIONS = ("create", "link", "update", "roles", "disable", "restore",
                          "skip", "error")


CONNECTIONS_DDL = """
    CREATE TABLE IF NOT EXISTS directory_connections (
        id INTEGER PRIMARY KEY,
        integration_id INTEGER NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK (kind IN ('entra', 'ldap')),
        domain TEXT NOT NULL DEFAULT '',
        user_base_dn TEXT NOT NULL DEFAULT '',
        group_base_dn TEXT NOT NULL DEFAULT '',
        user_filter TEXT NOT NULL DEFAULT '',
        group_filter TEXT NOT NULL DEFAULT '',
        client_id TEXT NOT NULL DEFAULT '',
        tenant_id TEXT NOT NULL DEFAULT '',
        verify_tls INTEGER NOT NULL DEFAULT 1 CHECK (verify_tls IN (0, 1)),
        use_starttls INTEGER NOT NULL DEFAULT 0 CHECK (use_starttls IN (0, 1)),
        ca_bundle_path TEXT NOT NULL DEFAULT '',
        allowed_domains TEXT NOT NULL DEFAULT '',
        provision INTEGER NOT NULL DEFAULT 1 CHECK (provision IN (0, 1)),
        deprovision INTEGER NOT NULL DEFAULT 1 CHECK (deprovision IN (0, 1)),
        allow_signin INTEGER NOT NULL DEFAULT 0 CHECK (allow_signin IN (0, 1)),
        group_nested INTEGER NOT NULL DEFAULT 1 CHECK (group_nested IN (0, 1)),
        default_role TEXT NOT NULL DEFAULT 'member',
        last_preview_at TEXT,
        last_status TEXT NOT NULL DEFAULT 'untested',
        last_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (integration_id) REFERENCES integrations(id)
            ON DELETE CASCADE
    )
"""


# A directory connection is a registry row plus this profile. The view presents
# the one shape every directory screen and provider already reads, so the name,
# endpoint, status, schedule and credential have a single owner: the registry.
# Written straight into the integration file with bare names. The shared
# connection prefixes every table with ``integration.``, and a view stored that
# way cannot be parsed when the file is opened on its own - a backup check or any
# external tool then reports the whole database as malformed.
REGISTRY_VIEW_DDL = """
    CREATE VIEW directory_registry AS
    SELECT c.id,
           c.integration_id,
           i.name,
           c.kind,
           CASE WHEN i.status = 'active' THEN 1 ELSE 0 END AS enabled,
           i.endpoint AS server_url,
           c.domain,
           i.search_base AS base_dn,
           c.user_base_dn,
           c.group_base_dn,
           c.user_filter,
           c.group_filter,
           i.username AS bind_dn,
           c.client_id,
           c.tenant_id,
           COALESCE(
               (SELECT k.encrypted_value FROM integration_credentials AS k
                 WHERE k.integration_id = i.id
                   AND k.credential_name = CASE WHEN c.kind = 'entra'
                        THEN 'client_secret' ELSE 'service_account_password' END),
               (SELECT k.encrypted_value FROM integration_credentials AS k
                 WHERE k.integration_id = i.id
                   AND k.credential_name = 'password'),
               (SELECT k.encrypted_value FROM integration_credentials AS k
                 WHERE k.integration_id = i.id
                   AND k.credential_name = 'client_secret'),
               (SELECT k.encrypted_value FROM integration_credentials AS k
                 WHERE k.integration_id = i.id
                   AND k.credential_name = 'service_account_password'),
               '') AS secret_sealed,
           c.verify_tls,
           c.use_starttls,
           c.ca_bundle_path,
           c.allowed_domains,
           c.provision,
           c.deprovision,
           c.allow_signin,
           c.group_nested,
           c.default_role,
           CASE WHEN i.sync_enabled = 1 THEN i.sync_interval ELSE 0 END
               AS interval_minutes,
           (SELECT t.tested_at FROM integration_connection_tests AS t
             WHERE t.integration_id = i.id ORDER BY t.id DESC LIMIT 1)
               AS last_test_at,
           c.last_preview_at,
           i.last_sync_at,
           c.last_status,
           c.last_error,
           i.created_at,
           i.updated_at
    FROM directory_connections AS c
    JOIN integrations AS i ON i.id = c.integration_id
"""


GROUP_MAPPINGS_DDL = """
    CREATE TABLE IF NOT EXISTS directory_group_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id INTEGER NOT NULL,
        group_key TEXT NOT NULL,
        group_name TEXT NOT NULL DEFAULT '',
        role_key TEXT NOT NULL,
        nested INTEGER NOT NULL DEFAULT 1 CHECK (nested IN (0, 1)),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        note TEXT NOT NULL DEFAULT '',
        member_count INTEGER NOT NULL DEFAULT 0,
        discovered_at TEXT,
        synced_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (connection_id, group_key, role_key),
        FOREIGN KEY (connection_id) REFERENCES directory_connections(id)
            ON DELETE CASCADE
    )
"""


SYNC_RUNS_DDL = """
    CREATE TABLE IF NOT EXISTS directory_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        mode TEXT NOT NULL DEFAULT 'preview' CHECK (mode IN ('preview', 'apply')),
        trigger TEXT NOT NULL DEFAULT 'manual'
            CHECK (trigger IN ('manual', 'scheduled', 'test')),
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'success', 'partial', 'failed', 'preview')),
        seen INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL DEFAULT 0,
        linked INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        granted INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        restored INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        actor_id INTEGER,
        error TEXT NOT NULL DEFAULT '',
        summary_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (connection_id) REFERENCES directory_connections(id)
            ON DELETE CASCADE
    )
"""


SYNC_ITEMS_DDL = """
    CREATE TABLE IF NOT EXISTS directory_sync_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        object_id TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        display_name TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        user_id INTEGER,
        plan_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (run_id) REFERENCES directory_sync_runs(id) ON DELETE CASCADE
    )
"""


USER_DIRECTORY_COLUMNS = (
    ("directory_connection_id", "INTEGER"),
    ("directory_object_id", "TEXT"),
    ("directory_upn", "TEXT"),
    ("directory_managed", "INTEGER NOT NULL DEFAULT 0"),
    ("directory_departed", "INTEGER NOT NULL DEFAULT 0"),
    ("directory_synced_at", "TEXT"),
)


def directory_available():
    """True when at least one directory kind can be reached from this install."""
    return ldap3 is not None or msal is not None


def missing_provider(kind):
    """The package a kind needs and does not have, or "" when it is ready."""
    if kind == "ldap" and ldap3 is None:
        return "ldap3"
    if kind == "entra" and (msal is None or requests is None):
        return "msal and requests"
    return ""


def utc_stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ schema
def migrate_account_directory(db):
    """Give users the columns a directory link needs.

    Added with ALTER rather than through the users rebuild in core.db, the same
    way the Microsoft link arrived: SQLite keeps a table rebuild expensive and
    every other screen reads users by name, so a new nullable column is the
    change that cannot disturb them.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    for name, definition in USER_DIRECTORY_COLUMNS:
        if name not in columns:
            db.execute("ALTER TABLE users ADD COLUMN {} {}".format(name, definition))
    # One directory object may own at most one account per connection. The index
    # is partial because unlinked accounts carry no object id at all, and SQLite
    # counts each NULL in a UNIQUE index as distinct anyway.
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_directory_object"
               " ON users(directory_connection_id, directory_object_id)"
               " WHERE directory_object_id IS NOT NULL"
               " AND directory_object_id <> ''")
    db.execute("CREATE INDEX IF NOT EXISTS idx_users_directory_connection"
               " ON users(directory_connection_id)")


def _legacy_profile_table(db):
    """True while ``directory_connections`` is still the standalone table."""
    try:
        row = db.execute("""
            SELECT name FROM integration.sqlite_master
            WHERE type = 'table' AND name = 'directory_connections'
        """).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    columns = {entry[1] for entry in
               db.execute("PRAGMA table_info(directory_connections)").fetchall()}
    return bool(columns) and "integration_id" not in columns


def _legacy_integration_id(db, row):
    """The registry row a legacy connection belongs to, created when absent.

    An integration already carrying the same name and no directory profile is
    adopted rather than duplicated; any other clash keeps both rows under
    distinct names.
    """
    from departments.integrations.database import AD_TYPE
    from departments.integrations.database import ENTRA_TYPE

    existing = db.execute("SELECT id FROM integrations WHERE lower(name) = lower(?)",
                          (row["name"],)).fetchone()
    if existing is not None:
        owned = db.execute("SELECT 1 FROM integration.directory_connections_new"
                           " WHERE integration_id = ?", (existing["id"],)).fetchone()
        if owned is None:
            return existing["id"]

    base = (row["name"] or "Directory connection")[:110]
    name, attempt = base, 0
    while True:
        clash = db.execute("SELECT id FROM integrations WHERE lower(name) = lower(?)",
                           (name,)).fetchone()
        if clash is None:
            break
        attempt += 1
        name = "{} ({})".format(base[:90], row["id"] if attempt == 1
                                else "{}-{}".format(row["id"], attempt))

    interval = max(0, int(row["interval_minutes"] or 0))
    enabled = 1 if row["enabled"] else 0
    sync_enabled = 1 if enabled and interval > 0 else 0
    sync_interval = min(max(interval, 5), 10080) if sync_enabled else 60
    cursor = db.execute("""
        INSERT INTO integrations
            (name, integration_type, description, endpoint, username, search_base,
             status, sync_enabled, sync_interval, last_sync_at, created_at,
             updated_at)
        VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name,
          ENTRA_TYPE if row["kind"] == "entra" else AD_TYPE,
          row["server_url"] or "", row["bind_dn"] or "", row["base_dn"] or "",
          "active" if enabled else "inactive", sync_enabled, sync_interval,
          row["last_sync_at"], row["created_at"], row["updated_at"]))
    return cursor.lastrowid


def _migrate_legacy_profiles(db):
    """Rebuild the standalone directory table as a registry profile.

    The old row ids are kept, so every child table and every linked user still
    points at the same connection. The credential moves into the shared sealed
    store under the name the provider reads for its kind.
    """
    if not _legacy_profile_table(db):
        return
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("DROP TABLE IF EXISTS integration.directory_connections_new")
        db.execute(CONNECTIONS_DDL.replace(
            "CREATE TABLE IF NOT EXISTS directory_connections",
            "CREATE TABLE integration.directory_connections_new"))
        for row in db.execute("SELECT * FROM directory_connections").fetchall():
            integration_id = _legacy_integration_id(db, row)
            db.execute("""
                INSERT INTO integration.directory_connections_new
                    (id, integration_id, kind, domain, user_base_dn, group_base_dn,
                     user_filter, group_filter, client_id, tenant_id, verify_tls,
                     use_starttls, ca_bundle_path, allowed_domains, provision,
                     deprovision, allow_signin, group_nested, default_role,
                     last_preview_at, last_status, last_error, created_at,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?)
            """, (row["id"], integration_id, row["kind"], row["domain"] or "",
                  row["user_base_dn"] or "", row["group_base_dn"] or "",
                  row["user_filter"] or "", row["group_filter"] or "",
                  row["client_id"] or "", row["tenant_id"] or "",
                  row["verify_tls"], row["use_starttls"],
                  row["ca_bundle_path"] or "", row["allowed_domains"] or "",
                  row["provision"], row["deprovision"], row["allow_signin"],
                  row["group_nested"], row["default_role"] or "member",
                  row["last_preview_at"], row["last_status"] or "untested",
                  row["last_error"] or "", row["created_at"], row["updated_at"]))
            if (row["secret_sealed"] or "").strip():
                name = ("client_secret" if row["kind"] == "entra"
                        else "service_account_password")
                db.execute("""
                    INSERT INTO integration_credentials
                        (integration_id, credential_name, encrypted_value,
                         created_at, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(integration_id, credential_name) DO UPDATE SET
                        encrypted_value = excluded.encrypted_value,
                        updated_at = CURRENT_TIMESTAMP
                """, (integration_id, name, row["secret_sealed"]))
        db.execute("DROP TABLE directory_connections")
        db.execute("ALTER TABLE integration.directory_connections_new"
                   " RENAME TO directory_connections")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _rebuild_registry_view():
    """(Re)create the view inside the integration file, unqualified.

    A direct connection is used on purpose: the shared one would schema-qualify
    the body, and SQLite then refuses to parse the file without the schema
    attached. An earlier definition stored that way blocks even a DROP until its
    sqlite_master row is removed, which is what the writable_schema branch does.
    """
    from database.database_config import module_path
    connection = sqlite3.connect(str(module_path("integration")))
    try:
        try:
            connection.execute("DROP VIEW IF EXISTS directory_registry")
            connection.commit()
        except sqlite3.DatabaseError:
            connection.rollback()
            connection.execute("PRAGMA writable_schema = ON")
            connection.execute("DELETE FROM sqlite_master WHERE type = 'view'"
                               " AND name = 'directory_registry'")
            connection.commit()
            connection.execute("PRAGMA writable_schema = OFF")
        connection.execute(REGISTRY_VIEW_DDL)
        connection.commit()
    finally:
        connection.close()


def ensure_directory_schema():
    """Create or upgrade the directory tables. Safe to call on every boot."""
    from core.db import connect_db
    from departments.integrations.database import ensure_integrations_schema
    ensure_integrations_schema()
    db = connect_db()
    try:
        _migrate_legacy_profiles(db)
        db.execute(CONNECTIONS_DDL)
        db.execute(GROUP_MAPPINGS_DDL)
        db.execute(SYNC_RUNS_DDL)
        db.execute(SYNC_ITEMS_DDL)
        db.execute("CREATE INDEX IF NOT EXISTS idx_directory_mappings_connection"
                   " ON directory_group_mappings(connection_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_directory_runs_connection"
                   " ON directory_sync_runs(connection_id, started_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_directory_items_run"
                   " ON directory_sync_items(run_id)")
        # The view is written through its own connection, so the shared one must
        # not still hold the write lock.
        db.commit()
        _rebuild_registry_view()
        migrate_account_directory(db)
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------- credentials
def _sealing_key():
    """A 32-byte key derived from the application secret, never the secret itself.

    The stored directory credential has to survive a restart and a database copy,
    so it cannot live in a per-process random key. HKDF over the signing secret
    keeps the two uses of that secret separate: a leaked database gives an
    attacker the ciphertext and the session-signing material, neither of which is
    the derived key.
    """
    from core.config import load_app_secret
    material = load_app_secret().encode("utf-8")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b"wk.directory.connection.secret").derive(material)
    return base64.urlsafe_b64encode(key)


def seal_secret(value):
    """The ciphertext for a typed credential, or "" when nothing was typed."""
    value = (value or "").strip()
    if not value:
        return ""
    if Fernet is None:
        raise ValueError("The cryptography package is not installed, so a stored"
                         " directory credential cannot be sealed.")
    return Fernet(_sealing_key()).encrypt(value.encode("utf-8")).decode("ascii")


def open_secret(sealed):
    """(credential, problem) for a stored value; the caller must never show it."""
    if not sealed:
        return "", "No credential is stored on this connection."
    if Fernet is None:
        return "", "The cryptography package is missing, so the stored credential" \
                   " cannot be opened."
    try:
        return Fernet(_sealing_key()).decrypt(sealed.encode("ascii")).decode("utf-8"), ""
    except InvalidToken:
        return "", "The stored credential was sealed with a different application" \
                   " key. Re-enter it."
    except Exception:
        return "", "The stored credential could not be opened. Re-enter it."


def secret_is_set(connection):
    return bool((connection["secret_sealed"] or "").strip())


def secret_mask(connection):
    """What the screen may print about a credential: whether one exists, nothing more."""
    return "Stored" if secret_is_set(connection) else "Not stored"


# ----------------------------------------------------------------- naming
def clean_name(value):
    return re.sub(r"\s+", " ", (value or "").strip())[:80]


def clean_dn(value):
    """A DN as it should be typed: one space between relative components."""
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", (value or "").strip()))[:512]


def domain_allowed(connection, address):
    """True when an address belongs to the allow list stored on this connection.

    An empty list scopes the connection to the whole directory. Matching is exact
    on the part after '@', or on the NetBIOS prefix of a ``DOMAIN\\user`` name, so
    a look-alike sub-domain is never accepted silently.
    """
    allowed = {part for part in re.split(r"[,\s;]+", connection["allowed_domains"] or "")
               if part}
    if not allowed:
        return True
    value = (address or "").strip().lower()
    if "@" in value:
        return value.rsplit("@", 1)[1] in allowed
    if "\\" in value:
        return value.split("\\", 1)[0] in allowed
    return False


def escape_ldap_value(value):
    """RFC 4515 escaping for a value placed inside an LDAP filter.

    Every filter in this module interpolates a DN or an address an administrator
    typed, so an unescaped ')' or '*' would change the query the domain
    controller runs - and a crafted DN could widen the search.
    """
    return "".join("\\{:02x}".format(ord(ch)) if ch in "\\*()" else ch
                   for ch in str(value or ""))


def connection_by_id(db, connection_id):
    try:
        return db.execute("SELECT * FROM directory_registry WHERE id = ?",
                          (connection_id,)).fetchone()
    except sqlite3.Error:
        return None


def connection_by_integration(db, integration_id):
    """The directory profile an integration registry row owns, or None."""
    try:
        return db.execute("SELECT * FROM directory_registry"
                          " WHERE integration_id = ?", (integration_id,)).fetchone()
    except sqlite3.Error:
        return None


def all_connections(db):
    try:
        return db.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM directory_group_mappings m
                     WHERE m.connection_id = c.id AND m.enabled = 1) AS mapping_count,
                   (SELECT COUNT(*) FROM users u
                     WHERE u.directory_connection_id = c.id) AS account_count,
                   (SELECT COUNT(*) FROM users u
                     WHERE u.directory_connection_id = c.id
                       AND u.directory_departed = 1) AS departed_count
            FROM directory_registry AS c
            ORDER BY c.name COLLATE NOCASE
        """).fetchall()
    except sqlite3.Error:
        return []


# ---------------------------------------------------------- connection form
LDAP_USER_DEFAULTS = ("(objectClass=user)", "(objectCategory=person)")

LDAP_GROUP_DEFAULTS = ("(objectClass=group)",)

LDAP_ADMIN_FIELDS = ("server_url", "domain", "base_dn", "user_base_dn",
                     "group_base_dn", "user_filter", "group_filter", "bind_dn",
                     "client_id", "tenant_id", "ca_bundle_path", "allowed_domains",
                     "default_role")

LDAP_FLAG_FIELDS = ("enabled", "verify_tls", "use_starttls", "provision",
                    "deprovision", "allow_signin", "group_nested")


def form_flag(form, name):
    return 1 if (form.get(name) or "").strip() in ("1", "on", "true") else 0


def filter_with(extra, defaults):
    """One LDAP filter from the fixed object classes plus whatever the form adds."""
    text = (extra or "").strip()
    if text:
        if not text.startswith("("):
            text = "({})".format(text)
        return "(&{}{})".format("".join(defaults), text)
    return "(&{})".format("".join(defaults))


def normalize_connection_form(form, kind, existing=None):
    """The stored field values for one connection, with each value cleaned.

    Returns (values, problems). A blank credential means "leave the sealed value
    alone" - the form never has to show a stored secret back to the browser.
    """
    values, problems = {}, []
    name = clean_name(form.get("name"))
    if not name:
        problems.append("Give the connection a name administrators will recognise.")
    values["name"] = name
    values["kind"] = kind if kind in DIRECTORY_KINDS else "ldap"
    for field in LDAP_ADMIN_FIELDS:
        values[field] = re.sub(r"\s+", " ", str(form.get(field) or "")).strip()[:512]
    values["base_dn"] = clean_dn(values["base_dn"])
    values["user_base_dn"] = clean_dn(values["user_base_dn"] or values["base_dn"])
    values["group_base_dn"] = clean_dn(values["group_base_dn"] or values["base_dn"])
    # Spaces around the separators are cosmetic; a space inside an RDN value
    # ("CN=Service Accounts") is part of the DN and must survive.
    values["bind_dn"] = re.sub(r"\s*=\s*", "=",
                               re.sub(r"\s*,\s*", ",", values["bind_dn"]))
    values["client_id"] = values["client_id"].replace(" ", "")
    values["tenant_id"] = values["tenant_id"].replace(" ", "")
    for field in LDAP_FLAG_FIELDS:
        values[field] = form_flag(form, field)
    try:
        interval = int((str(form.get("interval_minutes") or "0").strip() or "0"))
    except ValueError:
        interval = 0
        problems.append("The sync interval must be a number of minutes.")
    values["interval_minutes"] = max(0, min(interval, 7 * 24 * 60))
    if 0 < values["interval_minutes"] < 5:
        problems.append("Scheduled runs must be at least 5 minutes apart, or off.")

    server = values["server_url"]
    if values["kind"] == "ldap":
        if not re.match(r"^ldaps?://[^\s]+$", server, re.I):
            problems.append("The server address must be a full ldaps:// (or ldap://)"
                            " URL, for example ldaps://dc01.corp.local:636.")
        if not values["base_dn"]:
            problems.append("A search base DN is required, for example"
                            " DC=corp,DC=local.")
        if not values["bind_dn"]:
            problems.append("Enter the bind account, for example"
                            " CN=svc-app,OU=Service Accounts,DC=corp,DC=local.")
        if not server.lower().startswith("ldaps://") and not values["use_starttls"]:
            problems.append("Plain ldap:// without StartTLS would send the bind"
                            " credential in the clear. Use ldaps://, or tick"
                            " StartTLS.")
    else:
        if not values["tenant_id"]:
            problems.append("Entra ID needs the tenant id or domain from the app"
                            " registration.")
        if not values["client_id"]:
            problems.append("Entra ID needs the application (client) id.")
    if values["default_role"] not in USER_ROLES:
        problems.append("Choose a role this system actually has.")
        values["default_role"] = "member"
    values["allowed_domains"] = ",".join(
        sorted({part.strip().lower()
                for part in re.split(r"[,\s;]+", values["allowed_domains"])
                if part.strip()}))[:254]

    typed = str(form.get("secret") or "")
    if typed and typed != DIRECTORY_SECRET_PLACEHOLDER:
        try:
            values["secret_sealed"] = seal_secret(typed)
        except ValueError as exc:
            problems.append(str(exc))
            values["secret_sealed"] = existing["secret_sealed"] if existing else ""
    elif existing is not None:
        values["secret_sealed"] = existing["secret_sealed"]
    else:
        values["secret_sealed"] = ""
    if not values["secret_sealed"]:
        problems.append("Store the bind password or client secret for this connection.")
    return values, problems


CONNECTION_VALUES = ("name", "kind", "enabled", "server_url", "domain", "base_dn",
                     "user_base_dn", "group_base_dn", "user_filter", "group_filter",
                     "bind_dn", "client_id", "tenant_id", "secret_sealed",
                     "verify_tls", "use_starttls", "ca_bundle_path",
                     "allowed_domains", "provision", "deprovision", "allow_signin",
                     "group_nested", "default_role", "interval_minutes")

PROFILE_COLUMNS = ("kind", "domain", "user_base_dn", "group_base_dn", "user_filter",
                   "group_filter", "client_id", "tenant_id", "verify_tls",
                   "use_starttls", "ca_bundle_path", "allowed_domains", "provision",
                   "deprovision", "allow_signin", "group_nested", "default_role")


def _credential_name(kind):
    return "client_secret" if kind == "entra" else "service_account_password"


def _integration_type(kind):
    from departments.integrations.database import AD_TYPE
    from departments.integrations.database import ENTRA_TYPE
    return ENTRA_TYPE if kind == "entra" else AD_TYPE


def _store_connection_secret(db, integration_id, kind, sealed):
    """Seal once, under the name this kind's provider reads; drop the other one."""
    if not (sealed or "").strip():
        return
    name = _credential_name(kind)
    db.execute("""
        INSERT INTO integration_credentials
            (integration_id, credential_name, encrypted_value, created_at,
             updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(integration_id, credential_name) DO UPDATE SET
            encrypted_value = excluded.encrypted_value,
            updated_at = CURRENT_TIMESTAMP
    """, (integration_id, name, sealed))
    db.execute("""
        DELETE FROM integration_credentials
        WHERE integration_id = ? AND credential_name <> ?
          AND credential_name IN ('service_account_password', 'client_secret')
    """, (integration_id, name))


def _next_run(connection, interval=None):
    """When the registry should next pick this connection up, or None."""
    if not connection["enabled"]:
        return None
    minutes = connection["interval_minutes"] if interval is None else interval
    if not minutes:
        return None
    return (datetime.now(timezone.utc) + timedelta(minutes=int(minutes))).isoformat(
        timespec="seconds")


def _schedule(values):
    """(sync_enabled, sync_interval) for the registry from a directory form."""
    interval = int(values.get("interval_minutes") or 0)
    if not values.get("enabled") or interval <= 0:
        return 0, 60
    return 1, min(max(interval, 5), 10080)


def _safe_details(values):
    """What a connection's configuration may say in the audit trail.

    The credential column is named but never carried, and the server address is
    kept because it is not a secret and it is the first thing an investigator
    needs.
    """
    keep = ("server_url", "base_dn", "user_base_dn", "group_base_dn", "bind_dn",
            "client_id", "tenant_id", "domain", "allowed_domains", "verify_tls",
            "use_starttls", "provision", "deprovision", "allow_signin",
            "group_nested", "default_role", "interval_minutes", "enabled")
    return {key: values.get(key, "") for key in keep if key in values}


def _registry_schedule(values):
    """What the registry row should say about scheduling this profile."""
    enabled = 1 if values.get("enabled") else 0
    sync_enabled, interval = _schedule(values)
    next_run = None
    if enabled and sync_enabled:
        next_run = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(
            timespec="seconds")
    return enabled, sync_enabled, interval, next_run


def _write_profile(db, connection_id, integration_id, values, kind):
    db.execute("""
        INSERT INTO directory_connections
            (id, integration_id, kind, domain, user_base_dn, group_base_dn,
             user_filter, group_filter, client_id, tenant_id, verify_tls,
             use_starttls, ca_bundle_path, allowed_domains, provision,
             deprovision, allow_signin, group_nested, default_role,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (connection_id, integration_id, kind, values["domain"],
          values["user_base_dn"], values["group_base_dn"], values["user_filter"],
          values["group_filter"], values["client_id"], values["tenant_id"],
          values["verify_tls"], values["use_starttls"], values["ca_bundle_path"],
          values["allowed_domains"], values["provision"], values["deprovision"],
          values["allow_signin"], values["group_nested"], values["default_role"],
          utc_stamp(), utc_stamp()))


def create_connection(db, values, actor=None):
    """Register one directory connection as a registry row plus its profile."""
    from services.audit import log_activity
    values = dict(values)
    values["kind"] = values.get("kind") if values.get("kind") in DIRECTORY_KINDS else "ldap"
    if db.execute("SELECT 1 FROM integrations WHERE lower(name) = ?",
                  (values["name"].lower(),)).fetchone() is not None:
        return None, "Another connection is already called {}.".format(values["name"])
    enabled, sync_enabled, interval, next_run = _registry_schedule(values)
    now = utc_stamp()
    cursor = db.execute("""
        INSERT INTO integrations
            (name, integration_type, endpoint, username, search_base, status,
             sync_enabled, sync_interval, next_run_at, created_by, created_at,
             updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (values["name"], _integration_type(values["kind"]),
          values["server_url"], values["bind_dn"], values["base_dn"],
          "active" if enabled else "inactive", sync_enabled, interval, next_run,
          actor["id"] if actor is not None else None, now, now))
    integration_id = cursor.lastrowid
    _write_profile(db, integration_id, integration_id, values, values["kind"])
    _store_connection_secret(db, integration_id, values["kind"],
                              values.get("secret_sealed", ""))
    db.commit()
    details = _safe_details(values)
    details["credential"] = "sealed"
    log_activity("CREATE", "integrations",
                 "Directory connection {} ({}) created".format(
                     values["name"], values["kind"]),
                 record_type="integration", record_id=integration_id,
                 details=details)
    return integration_id, ""


def update_connection(db, connection, values):
    """Save one connection back to both halves of its one row."""
    from services.audit import log_activity
    values = dict(values)
    clash = db.execute("SELECT id FROM integrations"
                       " WHERE lower(name) = ? AND id <> ?",
                       (values["name"].lower(), connection["integration_id"])).fetchone()
    if clash is not None:
        return "Another connection is already called {}.".format(values["name"])
    enabled, sync_enabled, interval, next_run = _registry_schedule(values)
    now = utc_stamp()
    db.execute("""
        UPDATE integrations SET
            name = ?, integration_type = ?, endpoint = ?, username = ?,
            search_base = ?, status = ?, sync_enabled = ?, sync_interval = ?,
            next_run_at = ?, updated_at = ?
        WHERE id = ?
    """, (values["name"], _integration_type(values["kind"]),
          values["server_url"], values["bind_dn"], values["base_dn"],
          "active" if enabled else "inactive", sync_enabled, interval, next_run,
          now, connection["integration_id"]))
    assignments = ", ".join("{} = ?".format(key) for key in PROFILE_COLUMNS)
    db.execute("UPDATE directory_connections SET {}, updated_at = ? WHERE id = ?"
               .format(assignments),
               tuple(values[key] for key in PROFILE_COLUMNS)
               + (now, connection["id"]))
    if values.get("secret_sealed") != connection["secret_sealed"]:
        _store_connection_secret(db, connection["integration_id"], values["kind"],
                                 values.get("secret_sealed", ""))
    db.commit()
    changed = [key for key in CONNECTION_VALUES
               if str(connection[key] or "") != str(values[key] or "")]
    details = {"changed": sorted(changed)}
    details.update(_safe_details({key: values[key] for key in changed}))
    details["credential"] = ("replaced" if values["secret_sealed"]
                             != connection["secret_sealed"] else "kept")
    log_activity("UPDATE", "integrations",
                 "Directory connection {} updated".format(values["name"]),
                 record_type="integration", record_id=connection["integration_id"],
                 details=details)
    return ""


def delete_connection(db, connection):
    """Retire a connection, refusing while it still owns local accounts.

    A directory-managed account whose connection disappears loses the only
    credential it may have, so releasing the accounts has to be a deliberate act
    on the managed-accounts screen first. The registry row is the owner: deleting
    it takes the profile, its mappings and its run history with it.
    """
    from services.audit import log_activity
    held = db.execute("SELECT COUNT(*) AS n FROM users"
                      " WHERE directory_connection_id = ?",
                      (connection["id"],)).fetchone()["n"]
    if held:
        return ("{} account{} on this connection must be released before the"
                " connection is deleted.".format(held, "" if held == 1 else "s"))
    integration_id = connection["integration_id"]
    for table, column in (("integration_connection_tests", "integration_id"),
                          ("integration_sync_logs", "integration_id"),
                          ("integration_errors", "integration_id")):
        db.execute("DELETE FROM {} WHERE {} = ?".format(table, column),
                   (integration_id,))
    db.execute("DELETE FROM integrations WHERE id = ?", (integration_id,))
    db.commit()
    log_activity("DELETE", "integrations",
                 "Directory connection {} deleted".format(connection["name"]),
                 record_type="integration", record_id=integration_id)
    return ""


def set_connection_enabled(db, connection, enabled):
    from services.audit import log_activity
    next_run = None
    if enabled and connection["interval_minutes"]:
        next_run = _next_run({"enabled": 1,
                              "interval_minutes": connection["interval_minutes"]})
    db.execute("UPDATE integrations SET status = ?, next_run_at = ?, updated_at = ?"
               " WHERE id = ?",
               ("active" if enabled else "inactive", next_run, utc_stamp(),
                connection["integration_id"]))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Directory connection {} {}".format(
                     connection["name"], "enabled" if enabled else "disabled"),
                 outcome="SUCCESS", record_type="integration",
                 record_id=connection["integration_id"],
                 details={"enabled": 1 if enabled else 0})
    return ""


# ------------------------------------------------------------- LDAP provider
def _ldap_server(spec):
    from urllib.parse import urlsplit
    parts = urlsplit(spec["server_url"])
    host = parts.hostname or ""
    encrypted = (parts.scheme or "").lower() == "ldaps"
    port = parts.port or (636 if encrypted else 389)
    tls = None
    if encrypted or spec["use_starttls"]:
        import ssl as _ssl
        validate = getattr(ldap3, "CERT_REQUIRED", _ssl.CERT_REQUIRED)
        if not spec["verify_tls"]:
            validate = getattr(ldap3, "CERT_NONE", _ssl.CERT_NONE)
        bundle = (spec["ca_bundle_path"] or "").strip()
        tls = ldap3.TLS(validate=validate, ca_certs_file_path=bundle or None)
    return ldap3.Server(host, port=port, use_ssl=encrypted, tls=tls,
                        get_info=ldap3.NONE, connect_timeout=DIRECTORY_LDAP_TIMEOUT)


def _ldap_open(connection):
    """(bound client, problem); auto_bind performs the bind and reports failure."""
    credential, problem = open_secret(connection["secret_sealed"])
    if problem:
        return None, problem
    try:
        client = ldap3.Connection(
            _ldap_server(connection), user=connection["bind_dn"],
            password=credential,
            authentication=getattr(ldap3, "SIMPLE", "SIMPLE"),
            client_strategy=getattr(ldap3, "SYNC", "SYNC"),
            read_only=True, auto_bind=True, raise_exceptions=False,
            receive_timeout=DIRECTORY_LDAP_TIMEOUT,
            connect_timeout=DIRECTORY_LDAP_TIMEOUT)
    except Exception as exc:
        return None, "Bind failed: {}".format(str(exc)[:200])
    if not client.bound:
        detail = str(getattr(client, "last_error", "") or "")[:200]
        return None, "Bind failed: {}".format(detail or "the controller refused")
    starttls = (connection["use_starttls"]
                and not (connection["server_url"] or "").lower().startswith("ldaps://"))
    if starttls:
        try:
            client.start_tls()
        except Exception as exc:
            _ldap_close(client)
            return None, "StartTLS failed: {}".format(str(exc)[:200])
    return client, ""


def _ldap_close(client):
    try:
        client.unbind()
    except Exception:
        pass


def _ldap_raw(entry, *names):
    raw = getattr(entry, "raw_attributes", None) or {}
    for wanted in names:
        for key, value in raw.items():
            if key.lower() == wanted.lower():
                return value
    return None


def _ldap_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return _ldap_text(value[0]) if value else ""
    return str(value)


def _guid_text(value):
    """An objectGUID, in any shape ldap3 hands over, as a stable text key."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, bytes) and len(value) == 16:
        try:
            return str(uuid.UUID(bytes_le=value))
        except ValueError:
            return value.hex()
    return _ldap_text(value).strip()


LDAP_USER_ATTRIBUTES = ("objectGUID", "distinguishedName", "userPrincipalName",
                        "sAMAccountName", "mail", "displayName", "givenName", "sn",
                        "title", "department", "telephoneNumber", "employeeID",
                        "userAccountControl")


def _ldap_entry_user(connection, entry):
    dn = _ldap_text(_ldap_raw(entry, "distinguishedName")
                    or getattr(entry, "entry_dn", ""))
    upn = _ldap_text(_ldap_raw(entry, "userPrincipalName"))
    mail = _ldap_text(_ldap_raw(entry, "mail")).strip().lower()
    sam = _ldap_text(_ldap_raw(entry, "sAMAccountName"))
    name = _ldap_text(_ldap_raw(entry, "displayName")).strip() or " ".join(
        part for part in (_ldap_text(_ldap_raw(entry, "givenName")).strip(),
                          _ldap_text(_ldap_raw(entry, "sn")).strip()) if part)
    try:
        flags = int(_ldap_text(_ldap_raw(entry, "userAccountControl")) or 0)
    except ValueError:
        flags = 0
    login = upn or ("{}@{}".format(sam, connection["domain"])
                    if connection["domain"] else dn)
    return {
        "object_id": _guid_text(_ldap_raw(entry, "objectGUID")) or dn,
        "dn": dn,
        "upn": login,
        "email": mail or (upn if "@" in upn else ""),
        "full_name": (name or sam or dn)[:120],
        "username": sam[:120],
        "phone": _ldap_text(_ldap_raw(entry, "telephoneNumber"))[:30],
        "title": _ldap_text(_ldap_raw(entry, "title"))[:80],
        "department": _ldap_text(_ldap_raw(entry, "department"))[:80],
        "employee_code": _ldap_text(_ldap_raw(entry, "employeeID")).strip().upper()[:32],
        "enabled": not bool(flags & ACCOUNT_DISABLED_FLAG),
        "source": "ldap",
    }


def integration_ldap_spec(db, integration, search_base=None):
    """The shape the LDAP reader expects, built from a registry row alone.

    A registry-only Active Directory row (one with no directory profile) still
    reads through this module's LDAP provider, so the desk has one bind, one
    search and one set of attribute decoders instead of two.
    """
    sealed = ""
    for name in ("service_account_password", "password"):
        row = db.execute("""
            SELECT encrypted_value FROM integration_credentials
            WHERE integration_id = ? AND credential_name = ?
        """, (integration["id"], name)).fetchone()
        if row is not None:
            sealed = row["encrypted_value"]
            break
    base = (search_base or integration["search_base"] or "").strip()
    return {
        "name": integration["name"], "kind": "ldap",
        "server_url": integration["endpoint"] or "",
        "bind_dn": integration["username"] or "",
        "secret_sealed": sealed, "domain": "",
        "base_dn": base, "user_base_dn": base, "group_base_dn": base,
        "user_filter": "", "group_filter": "",
        "verify_tls": 1, "use_starttls": 0, "ca_bundle_path": "",
        "allowed_domains": "", "provision": 0, "deprovision": 0,
        "allow_signin": 0, "group_nested": 1, "default_role": "member",
    }


def ldap_test_bind(connection):
    """(ok, message, elapsed_ms) for one bind, reading no objects."""
    import time as _time
    started = _time.perf_counter()
    client, problem = _ldap_open(connection)
    elapsed = round((_time.perf_counter() - started) * 1000, 2)
    if problem:
        return False, problem, elapsed
    _ldap_close(client)
    return True, "Directory connection and bind succeeded.", elapsed


def ldap_fetch_users(connection):
    """(users, problem) for the account scope this connection defines."""
    client, problem = _ldap_open(connection)
    if problem:
        return [], problem
    users, seen = [], set()
    try:
        client.search(search_base=connection["user_base_dn"] or connection["base_dn"],
                      search_filter=filter_with(connection["user_filter"],
                                                LDAP_USER_DEFAULTS),
                      search_scope=LDAP_SUBTREE,
                      attributes=list(LDAP_USER_ATTRIBUTES),
                      size_limit=DIRECTORY_MAX_USERS)
        for entry in client.entries:
            user = _ldap_entry_user(connection, entry)
            key = user["object_id"]
            if not key or key in seen:
                continue
            seen.add(key)
            if not domain_allowed(connection, user["email"] or user["upn"]):
                continue
            users.append(user)
    except Exception as exc:
        return users, "Search failed: {}".format(str(exc)[:200])
    finally:
        _ldap_close(client)
    return users, ""


LDAP_GROUP_ATTRIBUTES = ("distinguishedName", "name", "description", "member")


def ldap_discover_groups(connection):
    """Groups an administrator can map to a role."""
    client, problem = _ldap_open(connection)
    if problem:
        return [], problem
    rows = []
    try:
        client.search(search_base=connection["group_base_dn"] or connection["base_dn"],
                      search_filter=filter_with(connection["group_filter"],
                                                LDAP_GROUP_DEFAULTS),
                      search_scope=LDAP_SUBTREE,
                      attributes=list(LDAP_GROUP_ATTRIBUTES),
                      size_limit=DIRECTORY_MAX_MEMBERS)
        for entry in client.entries:
            dn = _ldap_text(getattr(entry, "entry_dn", "")
                            or _ldap_raw(entry, "distinguishedName"))
            members = _ldap_raw(entry, "member")
            members = members if isinstance(members, (list, tuple)) else (
                [members] if members else [])
            rows.append({"key": dn,
                         "name": _ldap_text(_ldap_raw(entry, "name")) or dn,
                         "detail": _ldap_text(_ldap_raw(entry, "description"))[:120],
                         "count": len(members)})
    except Exception as exc:
        return rows, "Group search failed: {}".format(str(exc)[:200])
    finally:
        _ldap_close(client)
    rows.sort(key=lambda row: row["name"].lower())
    return rows, ""


def ldap_group_member_ids(connection, group_key, nested):
    """Object ids of everyone in one group; nested membership is the controller's job."""
    client, problem = _ldap_open(connection)
    if problem:
        return set(), problem
    safe = escape_ldap_value(group_key)
    clause = ("({}:={})".format(LDAP_NESTED_MATCH, safe) if nested
              else "(member={})".format(safe))
    try:
        client.search(search_base=connection["user_base_dn"] or connection["base_dn"],
                      search_filter="(&{}{})".format("".join(LDAP_USER_DEFAULTS), clause),
                      search_scope=LDAP_SUBTREE,
                      attributes=["objectGUID", "distinguishedName"],
                      size_limit=DIRECTORY_MAX_MEMBERS)
        ids = {_guid_text(_ldap_raw(entry, "objectGUID"))
               or _ldap_text(getattr(entry, "entry_dn", ""))
               for entry in client.entries}
        return {key for key in ids if key}, ""
    except Exception as exc:
        return set(), "Member lookup failed: {}".format(str(exc)[:200])
    finally:
        _ldap_close(client)


def ldap_verify_password(connection, sign_in_name, password):
    """(ok, problem) - bind as the person's own identity, never the service account.

    The submitted password is only ever offered as its own holder, so a sign-in
    through this path cannot read or disturb anything the sync owns.
    """
    who = (sign_in_name or "").strip()[:512]
    if not who or not password:
        return False, "This account has no directory sign-in name on record."
    try:
        client = ldap3.Connection(
            _ldap_server(connection), user=who, password=password,
            authentication=getattr(ldap3, "SIMPLE", "SIMPLE"),
            client_strategy=getattr(ldap3, "SYNC", "SYNC"),
            read_only=True, auto_bind=False, raise_exceptions=False,
            receive_timeout=DIRECTORY_LDAP_TIMEOUT,
            connect_timeout=DIRECTORY_LDAP_TIMEOUT)
        ok = bool(client.bind())
        detail = "" if ok else str(getattr(client, "last_error", "") or "")[:200]
        _ldap_close(client)
        return ok, detail
    except Exception as exc:
        return False, str(exc)[:200]


# ------------------------------------------------------------ Graph provider
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

GRAPH_SCOPE = "https://graph.microsoft.com/.default"

GRAPH_PAGE_SIZE = 999

# An app credential is valid for an hour and a synchronization needs one token per
# connection. Caching under a digest of the stored secret keeps repeat syncs off
# the token endpoint without holding the secret anywhere new.
_graph_cache = {}


def _graph_authority(connection):
    tenant = (connection["tenant_id"] or "").strip()
    return "https://login.microsoftonline.com/{}".format(tenant or "common")


def _graph_cache_key(connection):
    return hashlib.sha256("{}|{}|{}".format(
        connection["client_id"], _graph_authority(connection),
        connection["secret_sealed"]).encode("utf-8")).hexdigest()[:32]


def graph_token(connection):
    """(bearer token, problem) from the app registration stored on the connection."""
    if msal is None:
        return "", "The msal package is not installed, so Entra ID cannot be reached."
    credential, problem = open_secret(connection["secret_sealed"])
    if problem:
        return "", problem
    key = _graph_cache_key(connection)
    now = datetime.now(timezone.utc).timestamp()
    cached = _graph_cache.get(key)
    if cached and cached[1] > now + 60:
        return cached[0], ""
    try:
        app = msal.ConfidentialClientApplication(
            connection["client_id"], authority=_graph_authority(connection),
            client_credential=credential)
        result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
    except Exception as exc:
        return "", "Entra ID sign-in for the app registration failed: {}".format(
            str(exc)[:200])
    if not result or result.get("error"):
        detail = "{} {}".format((result or {}).get("error"),
                                (result or {}).get("error_description"))
        return "", "Entra ID refused the app registration: {}".format(
            detail.strip()[:200])
    token = (result.get("access_token") or "").strip()
    if not token:
        return "", "Entra ID returned no access token."
    _graph_cache[key] = (token, now + int(result.get("expires_in") or 3600))
    return token, ""


def _graph_headers(token):
    return {"Authorization": "Bearer {}".format(token),
            "Accept": "application/json"}


def _graph_page(url, token, params=None, cap=DIRECTORY_MAX_USERS):
    """(rows, problem) for one Graph collection, following every next link."""
    if requests is None:
        return [], "The requests package is not installed."
    rows, session = [], requests.Session()
    while url and len(rows) < cap:
        try:
            response = session.get(url, headers=_graph_headers(token),
                                   params=params, timeout=DIRECTORY_GRAPH_TIMEOUT)
        except Exception as exc:
            return rows, "Entra ID could not be reached: {}".format(str(exc)[:200])
        params = None
        if response.status_code == 403:
            return rows, "Entra ID refused the read (403). The app registration needs" \
                         " a directory read permission such as" \
                         " GroupMember.Read.All or Directory.Read.All, and it has to" \
                         " be granted by an administrator of the tenant."
        if response.status_code == 429:
            return rows, "Entra ID throttled this request (429). Try again in a few" \
                         " minutes."
        if not response.ok:
            return rows, "Entra ID returned {} for {}.".format(
                response.status_code, url.split("?")[0])
        try:
            payload = response.json()
        except ValueError:
            return rows, "Entra ID returned a response that is not JSON."
        rows.extend(payload.get("value") or [])
        url = payload.get("@odata.nextLink")
    return rows, ""


GRAPH_USER_SELECT = ("id", "userPrincipalName", "mail", "displayName", "givenName",
                     "surname", "jobTitle", "department", "businessPhones",
                     "employeeId", "accountEnabled")


def _graph_user(row):
    mail = str(row.get("mail") or "").strip().lower()
    upn = str(row.get("userPrincipalName") or "").strip()
    name = str(row.get("displayName") or "").strip() or " ".join(
        part for part in (str(row.get("givenName") or "").strip(),
                          str(row.get("surname") or "").strip()) if part)
    phones = row.get("businessPhones") or []
    return {
        "object_id": str(row.get("id") or "").strip(),
        "dn": upn,
        "upn": upn,
        "email": mail or (upn if "@" in upn else ""),
        "full_name": name[:120],
        "phone": str(phones[0])[:30] if isinstance(phones, (list, tuple)) and phones
                 else "",
        "title": str(row.get("jobTitle") or "")[:80],
        "department": str(row.get("department") or "")[:80],
        "employee_code": str(row.get("employeeId") or "").strip().upper()[:32],
        "enabled": row.get("accountEnabled") is not False,
        "source": "entra",
    }


def graph_fetch_users(connection):
    """(users, problem) for every mailbox in scope, enabled or not."""
    token, problem = graph_token(connection)
    if problem:
        return [], problem
    rows, problem = _graph_page(
        "{}/users".format(GRAPH_BASE), token,
        params={"$select": ",".join(GRAPH_USER_SELECT), "$top": GRAPH_PAGE_SIZE},
        cap=DIRECTORY_MAX_USERS)
    if problem:
        return [], problem
    users, seen = [], set()
    for row in rows:
        user = _graph_user(row)
        key = user["object_id"]
        if not key or key in seen:
            continue
        seen.add(key)
        if not domain_allowed(connection, user["email"] or user["upn"]):
            continue
        users.append(user)
    return users, ""


GRAPH_GROUP_SELECT = ("id", "displayName", "description", "mail", "securityEnabled",
                      "groupTypes", "memberCount")


def graph_discover_groups(connection):
    """Groups the app registration can see; the screen lets an admin map them."""
    token, problem = graph_token(connection)
    if problem:
        return [], problem
    params = {"$select": ",".join(GRAPH_GROUP_SELECT), "$top": GRAPH_PAGE_SIZE}
    extra = (connection["group_filter"] or "").strip()
    if extra:
        params["$filter"] = extra.split("=", 1)[1] if extra.startswith(
            "$filter=") else extra
    rows, problem = _graph_page("{}/groups".format(GRAPH_BASE), token,
                                params=params, cap=DIRECTORY_MAX_MEMBERS)
    if problem:
        return [], problem
    groups = [{"key": str(row.get("id") or ""),
               "name": str(row.get("displayName") or "").strip()[:120],
               "detail": str(row.get("description") or "").strip()[:120],
               "count": int(row.get("memberCount") or 0)}
              for row in rows if row.get("id")]
    groups.sort(key=lambda row: row["name"].lower())
    return groups, ""


def graph_group_member_ids(connection, group_key, nested):
    """User ids in one group, with or without membership through nested groups."""
    token, problem = graph_token(connection)
    if problem:
        return set(), problem
    safe = re.sub(r"[^0-9a-fA-F-]", "", group_key or "")
    if not safe:
        return set(), "That group id is not a Graph object id."
    endpoint = "transitiveMembers" if nested else "members"
    rows, problem = _graph_page(
        "{}/groups/{}/{}".format(GRAPH_BASE, safe, endpoint), token,
        params={"$select": "id,@odata.type", "$top": GRAPH_PAGE_SIZE},
        cap=DIRECTORY_MAX_MEMBERS)
    if problem:
        return set(), problem
    return {str(row.get("id")) for row in rows
            if row.get("id") and row.get("@odata.type") == "#microsoft.graph.user"}, ""


def graph_test(connection):
    """(ok, message, facts) - proves the credential, the tenant and the read scope."""
    token, problem = graph_token(connection)
    if problem:
        return False, problem, {}
    if requests is None:
        return False, "The requests package is not installed.", {}
    try:
        response = requests.get("{}/organization".format(GRAPH_BASE),
                                headers=_graph_headers(token),
                                timeout=DIRECTORY_GRAPH_TIMEOUT)
    except Exception as exc:
        return False, "Entra ID could not be reached: {}".format(str(exc)[:200]), {}
    if response.status_code == 403:
        return True, "The credential works, but the app registration cannot read the" \
                     " organization: the directory read permission is still missing" \
                     " or not yet granted.", {}
    if not response.ok:
        return False, "Entra ID returned {}.".format(response.status_code), {}
    facts = {}
    try:
        first = (response.json().get("value") or [{}])[0]
        domains = first.get("verifiedDomains") or []
        facts = {"Tenant name": str(first.get("displayName") or "")[:120],
                 "Primary domain": str(domains[0])[:120] if domains else ""}
    except (AttributeError, IndexError, KeyError, ValueError):
        pass
    return True, "Authenticated against the tenant and read the organization.", facts


# ----------------------------------------------------------- provider bridge
PROVIDERS = {
    "ldap": {"fetch_users": ldap_fetch_users,
             "discover_groups": ldap_discover_groups,
             "member_ids": ldap_group_member_ids},
    "entra": {"fetch_users": graph_fetch_users,
              "discover_groups": graph_discover_groups,
              "member_ids": graph_group_member_ids},
}


def provider_for(connection):
    return PROVIDERS.get(connection["kind"], {})


def _bridge(connection, name):
    missing = missing_provider(connection["kind"])
    if missing:
        return None, "The {} package is not installed on this server.".format(missing)
    handler = provider_for(connection).get(name)
    if handler is None:
        return None, "This connection kind has no directory provider."
    return handler, ""


def fetch_users(connection):
    handler, problem = _bridge(connection, "fetch_users")
    return ([], problem) if problem else handler(connection)


def discover_groups(connection):
    handler, problem = _bridge(connection, "discover_groups")
    return ([], problem) if problem else handler(connection)


def group_member_ids(connection, group_key, nested):
    handler, problem = _bridge(connection, "member_ids")
    return (set(), problem) if problem else handler(connection, group_key, nested)


def test_connection(connection):
    """(ok, message, facts) for one connection, reading nothing from the database."""
    if connection["kind"] == "entra":
        if missing_provider("entra"):
            return False, "Install the msal package to use this connection.", {}
        return graph_test(connection)
    if missing_provider("ldap"):
        return False, "Install the ldap3 package to use this connection.", {}
    if connection["kind"] != "ldap":
        return False, "Unknown connection kind.", {}
    users, problem = ldap_fetch_users(connection)
    if problem:
        return False, problem, {}
    groups, group_problem = ldap_discover_groups(connection)
    facts = {"Accounts in scope": len(users),
             "Groups in scope": len(groups),
             "Sample": ", ".join(row["full_name"] for row in users[:3])}
    if not users:
        return (False if group_problem else True,
                group_problem or "The bind succeeded but the account scope matched"
                                 " nobody - check the base DN and the filter.", facts)
    return True, "Bound as the service account, read {} account{} and {} group{}.".format(
        len(users), "" if len(users) == 1 else "s",
        len(groups), "" if len(groups) == 1 else "s"), facts


def record_connection_test(db, connection):
    """Probe, store the outcome on the registry, audit it. (ok, message, facts)."""
    from services.audit import log_activity
    import time as _time
    started = _time.perf_counter()
    ok, message, facts = test_connection(connection)
    elapsed = round((_time.perf_counter() - started) * 1000, 1)
    db.execute("UPDATE directory_connections SET last_status = ?, last_error = ?"
               " WHERE id = ?",
               ("ok" if ok else "failed", "" if ok else message[:400],
                connection["id"]))
    db.execute("""
        INSERT INTO integration_connection_tests
            (integration_id, status, message, response_time_ms, tested_at)
        VALUES (?, ?, ?, ?, ?)
    """, (connection["integration_id"], "success" if ok else "failed",
          message[:400], elapsed, utc_stamp()))
    db.commit()
    details = {"message": message[:300]}
    details.update({key: str(value)[:120] for key, value in facts.items()})
    log_activity("TEST", "integrations",
                 "Directory connection {} {}".format(
                     connection["name"], "reachable" if ok else "unreachable"),
                 outcome="SUCCESS" if ok else "FAILURE",
                 record_type="integration", record_id=connection["integration_id"],
                 details=details)
    return ok, message, facts


# ------------------------------------------------------------------ mappings
def mappings(db, connection_id):
    try:
        return db.execute("""
            SELECT m.*, r.name AS role_name
            FROM directory_group_mappings AS m
            LEFT JOIN roles AS r ON r.key = m.role_key
            WHERE m.connection_id = ?
            ORDER BY m.group_name COLLATE NOCASE, m.role_key
        """, (connection_id,)).fetchall()
    except sqlite3.Error:
        return []


def discover_groups_into(db, connection, actor_id):
    """Refresh the storable group list from the directory. Returns (rows, problem)."""
    from services.audit import log_activity
    rows, problem = discover_groups(connection)
    if problem:
        return [], problem
    stamp = utc_stamp()
    for row in rows:
        db.execute("""
            INSERT INTO directory_group_mappings
                (connection_id, group_key, group_name, role_key, nested, enabled,
                 member_count, discovered_at)
            VALUES (?, ?, ?, 'member', ?, 0, ?, ?)
            ON CONFLICT(connection_id, group_key, role_key) DO UPDATE SET
                group_name = excluded.group_name,
                member_count = excluded.member_count,
                discovered_at = excluded.discovered_at
        """, (connection["id"], row["key"][:512], row["name"],
              connection["group_nested"], row["count"], stamp))
    db.commit()
    log_activity("SYNC", "integrations",
                 "Discovered {} groups on {}".format(len(rows), connection["name"]),
                 record_type="integration", record_id=connection["integration_id"],
                 details={"groups": len(rows)})
    return rows, ""


def save_mapping(db, connection, group_key, group_name, role_key, nested, note,
                 enabled=True):
    from services.audit import log_activity
    if role_key not in USER_ROLES and db.execute(
            "SELECT 1 FROM roles WHERE key = ?", (role_key,)).fetchone() is None:
        return "That role is not in the catalogue."
    if not group_key:
        return "Choose a group to map."
    db.execute("""
        INSERT INTO directory_group_mappings
            (connection_id, group_key, group_name, role_key, nested, enabled, note,
             discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(connection_id, group_key, role_key) DO UPDATE SET
            group_name = excluded.group_name,
            nested = excluded.nested,
            enabled = excluded.enabled,
            note = excluded.note
    """, (connection["id"], group_key[:512], (group_name or "")[:120], role_key,
          1 if nested else 0, 1 if enabled else 0, (note or "")[:200], utc_stamp()))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Group {} mapped to {} on {}".format(
                     group_name or group_key, role_key, connection["name"]),
                 record_type="integration_mapping", record_id=connection["id"],
                 details={"connection": connection["name"],
                          "group": group_key[:200], "role": role_key,
                          "nested": bool(nested), "enabled": bool(enabled)})
    return ""


def set_mapping_enabled(db, mapping, enabled):
    from services.audit import log_activity
    db.execute("UPDATE directory_group_mappings SET enabled = ? WHERE id = ?",
               (1 if enabled else 0, mapping["id"]))
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Group mapping {} {}".format(
                     mapping["group_name"] or mapping["group_key"],
                     "enabled" if enabled else "disabled"),
                 record_type="integration_mapping", record_id=mapping["id"],
                 details={"role": mapping["role_key"], "enabled": bool(enabled)})
    return ""


def delete_mapping(db, mapping):
    from services.audit import log_activity
    db.execute("DELETE FROM directory_group_mappings WHERE id = ?", (mapping["id"],))
    db.commit()
    log_activity("DELETE", "integrations",
                 "Group mapping {} -> {} removed".format(
                     mapping["group_name"] or mapping["group_key"],
                     mapping["role_key"]),
                 record_type="integration_mapping", record_id=mapping["id"])
    return ""


def mapping_roles(db, connection):
    """The enabled group mappings for one connection, as plain rows of work."""
    try:
        return db.execute("""
            SELECT id, group_key, group_name, role_key, nested
            FROM directory_group_mappings
            WHERE connection_id = ? AND enabled = 1
            ORDER BY group_name COLLATE NOCASE
        """, (connection["id"],)).fetchall()
    except sqlite3.Error:
        return []


# ------------------------------------------------------------------ planning
def managed_accounts(db, connection_id=None):
    where = "WHERE u.directory_connection_id IS NOT NULL"
    params = []
    if connection_id is not None:
        where += " AND u.directory_connection_id = ?"
        params.append(connection_id)
    try:
        return db.execute("""
            SELECT u.id, u.full_name, u.email, u.username, u.role, u.status,
                   u.must_change_password, u.last_login, u.password_hash,
                   u.directory_connection_id, u.directory_object_id,
                   u.directory_upn, u.directory_managed, u.directory_departed,
                   u.directory_synced_at,
                   c.name AS connection_name, c.kind AS connection_kind,
                   c.allow_signin AS connection_allow_signin
            FROM users AS u
            LEFT JOIN directory_registry AS c ON c.id = u.directory_connection_id
            {}
            ORDER BY c.name COLLATE NOCASE, u.full_name COLLATE NOCASE
        """.format(where), params).fetchall()
    except sqlite3.Error:
        return []


def _wanted_fields(user_row, entry):
    """Directory-owned profile columns that may follow the domain over.

    Name and telephone only: an account's role, status and address are decided
    elsewhere in this system, and a directory title is not a local job grade.
    """
    if user_row is None or "phone" not in entry:
        return {}
    wanted = {}
    for column, value in (("full_name", entry["full_name"]),
                          ("phone", entry["phone"])):
        if value and (user_row[column] or "") != value:
            wanted[column] = value
    return wanted


def _plan_grants(roles_by_object):
    return sorted({mapping["role_key"] for mapping in roles_by_object})


def plan_sync(db, connection, actor=None):
    """Read the directory and store what a synchronization would do, doing nothing.

    The plan is kept as items so the apply step works from the same decision an
    administrator just read, instead of asking the domain controller a second
    time and possibly acting on a different answer.
    """
    from services.audit import log_activity
    users, problem = fetch_users(connection)
    run_id = _open_run(db, connection, "preview", actor)
    if problem:
        _close_run(db, run_id, "failed", error=problem)
        _flag_connection(db, connection, "failed", problem)
        _record_sync_log(db, connection, "directory-preview", "failed", problem)
        _record_error(db, connection, "directory_preview", problem)
        log_activity("SYNC", "integrations",
                     "Directory preview failed on {}".format(connection["name"]),
                     outcome="FAILURE", record_type="integration",
                     record_id=connection["integration_id"],
                     details={"problem": problem[:300]})
        return run_id, 0, problem

    by_object = {user["object_id"]: user for user in users if user["object_id"]}
    roles_by_object = {}
    group_problems = []
    for mapping in mapping_roles(db, connection):
        ids, member_problem = group_member_ids(connection, mapping["group_key"],
                                               bool(mapping["nested"]))
        if member_problem:
            group_problems.append("{}: {}".format(
                mapping["group_name"] or mapping["group_key"], member_problem))
            continue
        for key in ids:
            roles_by_object.setdefault(key, []).append(mapping)
        db.execute("UPDATE directory_group_mappings SET member_count = ?,"
                   " synced_at = ? WHERE id = ?",
                   (len(ids), utc_stamp(), mapping["id"]))

    linked = {row["directory_object_id"]: row for row in db.execute(
        "SELECT * FROM users WHERE directory_connection_id = ?",
        (connection["id"],)).fetchall() if row["directory_object_id"]}
    plan, counts = [], {"create": 0, "link": 0, "update": 0, "roles": 0,
                        "disable": 0, "restore": 0, "skip": 0}

    for key, entry in sorted(by_object.items(),
                             key=lambda item: (item[1]["full_name"] or "").lower()):
        grants = _plan_grants(roles_by_object.get(key, []))
        row = linked.get(key)
        already_linked = row is not None
        if row is None:
            row = _match_existing(db, connection, entry)
        if row is not None and not already_linked and row["directory_connection_id"]:
            plan.append(("skip", entry, grants, row,
                         "Already owned by another directory connection, so this"
                         " one will not claim it."))
            counts["skip"] += 1
            continue
        if row is None:
            if not connection["provision"]:
                plan.append(("skip", entry, grants, row,
                             "Not provisioned because this connection does not"
                             " create accounts."))
                counts["skip"] += 1
                continue
            if not entry["email"] or "@" not in entry["email"]:
                plan.append(("skip", entry, grants, row,
                             "No usable email address in the directory; an account"
                             " needs one."))
                counts["skip"] += 1
                continue
            plan.append(("create", entry, grants, row, ""))
            counts["create"] += 1
            continue
        if not already_linked:
            plan.append(("link", entry, grants, row, ""))
            counts["link"] += 1
        fields = _wanted_fields(row, entry)
        if fields:
            plan.append(("update", entry, grants, row,
                         ", ".join(sorted(fields))))
            counts["update"] += 1
        held = {extra["role_key"] for extra in db.execute(
            "SELECT role_key FROM user_roles WHERE user_id = ?", (row["id"],))}
        missing_roles = [role for role in grants if role not in held]
        if missing_roles:
            plan.append(("roles", entry, missing_roles, row, ", ".join(missing_roles)))
            counts["roles"] += 1
        if not entry["enabled"] and row["status"] == "Active":
            if not connection["deprovision"]:
                plan.append(("skip", entry, grants, row,
                             "Disabled in the directory, but this connection does"
                             " not disable local accounts."))
                counts["skip"] += 1
            else:
                plan.append(("disable", entry, grants, row,
                             "Disabled in the directory."))
                counts["disable"] += 1
        elif entry["enabled"] and row["status"] != "Active" and row["directory_departed"]:
            plan.append(("restore", entry, grants, row, ""))
            counts["restore"] += 1

    if connection["deprovision"]:
        for row in db.execute("""
            SELECT * FROM users WHERE directory_connection_id = ?
              AND directory_managed = 1 AND directory_departed = 0
        """, (connection["id"],)).fetchall():
            if row["directory_object_id"] in by_object or not row["directory_object_id"]:
                continue
            if row["id"] == (actor["id"] if actor is not None else None):
                plan.append(("skip", _local_entry(row), [], row,
                             "You are signed in, so the account was left alone."))
                counts["skip"] += 1
                continue
            if row["status"] != "Active":
                continue
            plan.append(("disable", _local_entry(row), [], row,
                         "No longer inside this connection's account scope."))
            counts["disable"] += 1

    for action, entry, payload, row, detail in plan:
        fields = _wanted_fields(row, entry) if row is not None and "phone" in entry \
            else {}
        _add_item(db, run_id, entry, action, detail,
                  row["id"] if row is not None else None,
                  {"grants": payload, "fields": fields, "entry": dict(entry)})
    counts["seen"] = len(by_object)
    counts["errors"] = len(group_problems)
    status = "preview" if not group_problems else "partial"
    _close_run(db, run_id, status, counts,
               error="; ".join(group_problems)[:400])
    db.execute("UPDATE directory_connections SET last_preview_at = ? WHERE id = ?",
               (utc_stamp(), connection["id"]))
    db.commit()
    _record_sync_log(
        db, connection, "directory-preview",
        "failed" if group_problems else "success",
        "{} account{} read; {} to create, {} to link, {} to disable".format(
            len(by_object), "" if len(by_object) == 1 else "s", counts["create"],
            counts["link"], counts["disable"]))
    log_activity("SYNC", "integrations",
                 "Directory preview for {}: {} account{}, {} to create, {} to link,"
                 " {} to disable".format(
                     connection["name"], len(by_object),
                     "" if len(by_object) == 1 else "s", counts["create"],
                     counts["link"], counts["disable"]),
                 outcome="FAILURE" if group_problems else "SUCCESS",
                 record_type="integration", record_id=connection["integration_id"],
                 details={"run_id": run_id, "mode": "preview", **counts,
                          "group_problems": group_problems[:4]})
    return run_id, len(by_object), "; ".join(group_problems)


def _match_existing(db, connection, entry):
    """The local account this directory object belongs to, or None.

    Address is the only key offered: it is what the Microsoft link already uses,
    it is unique on the table, and an administrator recognises it. Nothing here
    invents a match from a display name.
    """
    email = (entry["email"] or "").strip().lower()
    if not email:
        return None
    if connection["kind"] == "entra":
        row = db.execute("SELECT * FROM users WHERE ms_object_id = ?",
                         (entry["object_id"],)).fetchone()
        if row is not None:
            return row
    try:
        return db.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE"
                          " ORDER BY id LIMIT 1", (email,)).fetchone()
    except sqlite3.Error:
        return None


# ------------------------------------------------------------------- runs
def _open_run(db, connection, mode, actor, trigger="manual"):
    cursor = db.execute("""
        INSERT INTO directory_sync_runs (connection_id, started_at, mode, trigger,
                                         status, actor_id)
        VALUES (?, ?, ?, ?, 'running', ?)
    """, (connection["id"], utc_stamp(), mode, trigger,
          actor["id"] if actor is not None else None))
    db.commit()
    return cursor.lastrowid


def _add_item(db, run_id, entry, action, detail, user_id, plan):
    db.execute("""
        INSERT INTO directory_sync_items
            (run_id, object_id, email, display_name, action, detail, user_id,
             plan_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, (entry.get("object_id") or "")[:120],
          (entry.get("email") or entry.get("upn") or "")[:254],
          (entry.get("full_name") or "")[:120], action, detail[:400], user_id,
          json.dumps(plan or {}, default=str)[:4000]))


def _close_run(db, run_id, status, counts=None, error=""):
    counts = counts or {}
    db.execute("""
        UPDATE directory_sync_runs SET finished_at = ?, status = ?, seen = ?,
            created = ?, linked = ?, updated = ?, granted = ?, disabled = ?,
            restored = ?, skipped = ?, errors = ?, error = ?, summary_json = ?
        WHERE id = ?
    """, (utc_stamp(), status, counts.get("seen", 0), counts.get("create", 0),
          counts.get("link", 0), counts.get("update", 0), counts.get("roles", 0),
          counts.get("disable", 0), counts.get("restore", 0),
          counts.get("skip", 0), counts.get("errors", 0), error[:400],
          json.dumps(counts, default=str)[:4000], run_id))
    db.commit()


def _record_sync_log(db, connection, sync_type, status, message,
                     started_at=None, completed_at=None):
    """Mirror a directory run into the shared sync log the integration desk reads."""
    db.execute("""
        INSERT INTO integration_sync_logs
            (integration_id, sync_type, status, message, started_at, completed_at)
        VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
    """, (connection["integration_id"], sync_type, status, (message or "")[:400],
          started_at, completed_at or utc_stamp()))
    db.commit()


def _record_error(db, connection, error_type, message):
    db.execute("""
        INSERT INTO integration_errors
            (integration_id, error_type, error_message)
        VALUES (?, ?, ?)
    """, (connection["integration_id"], error_type, (message or "")[:400]))
    db.commit()


def _flag_connection(db, connection, status, error=""):
    db.execute("UPDATE directory_connections SET last_status = ?, last_error = ?"
               " WHERE id = ?", (status, (error or "")[:400], connection["id"]))
    db.commit()


def prune_runs(db, connection_id):
    """Keep the recent history and drop the rest, so the tables stay small."""
    keep = db.execute("""
        SELECT id FROM directory_sync_runs WHERE connection_id = ?
        ORDER BY started_at DESC LIMIT ?
    """, (connection_id, DIRECTORY_RETAINED_RUNS)).fetchall()
    ids = [row["id"] for row in keep]
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    db.execute("DELETE FROM directory_sync_items WHERE run_id NOT IN ({})".format(marks),
               ids)
    db.execute("DELETE FROM directory_sync_runs WHERE connection_id = ?"
               " AND id NOT IN ({})".format(marks), [connection_id] + ids)
    db.commit()


def run_items(db, run_id, limit=400):
    try:
        return db.execute("""
            SELECT i.*, u.username, u.role, u.status
            FROM directory_sync_items AS i
            LEFT JOIN users AS u ON u.id = i.user_id
            WHERE i.run_id = ?
            ORDER BY i.id
            LIMIT ?
        """, (run_id, limit)).fetchall()
    except sqlite3.Error:
        return []


def recent_runs(db, connection_id=None, limit=12):
    where, params = "", []
    if connection_id is not None:
        where = "WHERE r.connection_id = ?"
        params.append(connection_id)
    try:
        return db.execute("""
            SELECT r.*, c.name AS connection_name, c.kind AS connection_kind
            FROM directory_sync_runs AS r
            JOIN directory_registry AS c ON c.id = r.connection_id
            {}
            ORDER BY r.started_at DESC, r.id DESC
            LIMIT ?
        """.format(where), params + [limit]).fetchall()
    except sqlite3.Error:
        return []


def get_run(db, run_id):
    try:
        return db.execute("""
            SELECT r.*, c.name AS connection_name, c.kind AS connection_kind
            FROM directory_sync_runs AS r
            JOIN directory_registry AS c ON c.id = r.connection_id
            WHERE r.id = ?
        """, (run_id,)).fetchone()
    except sqlite3.Error:
        return None


# ------------------------------------------------------------------ apply
def apply_sync(db, connection, run_id, actor):
    """Carry out a previewed plan. Returns (counts, problem)."""
    from services.audit import log_activity
    from services.notifications import deliver_notification
    run = db.execute("SELECT * FROM directory_sync_runs WHERE id = ?",
                     (run_id,)).fetchone()
    if run is None or run["connection_id"] != connection["id"]:
        return {}, "That preview is no longer available. Run it again."
    if run["mode"] != "preview":
        return {}, "Only a preview can be applied."
    items = db.execute("SELECT * FROM directory_sync_items WHERE run_id = ?"
                       " ORDER BY id", (run_id,)).fetchall()
    if not items:
        return {}, "That preview holds nothing to apply."

    counts = {"create": 0, "link": 0, "update": 0, "granted": 0, "disabled": 0,
              "restored": 0, "skipped": 0, "errors": 0}
    handover = []
    stamp = utc_stamp()
    bindable = bool(connection["allow_signin"]) and connection["kind"] == "ldap"

    for item in items:
        try:
            plan = json.loads(item["plan_json"] or "{}")
        except ValueError:
            plan = {}
        grants = plan.get("grants") or []
        fields = plan.get("fields") or {}
        action = item["action"]
        if action in ("link", "update", "roles", "disable", "restore") \
                and item["user_id"] is None:
            counts["skipped"] += 1
            continue
        try:
            if action == "create":
                created = _provision_account(db, connection, item, plan, actor,
                                             bindable)
                if created is None:
                    counts["skipped"] += 1
                    continue
                user_id, issued, username = created
                db.execute("UPDATE directory_sync_items SET user_id = ?, detail = ?"
                           " WHERE id = ?", (user_id, "Account created as {}".format(
                               username), item["id"]))
                counts["create"] += 1
                if issued:
                    handover.append({"username": username, "password": issued,
                                     "name": item["display_name"]})
                deliver_notification(
                    db, user_id, "Your account was created from the directory",
                    "An account for {} was opened by the {} directory connection."
                    " Choose a password of your own the first time you sign in.".format(
                        item["display_name"] or item["email"], connection["name"]),
                    "", category="account", icon="user", tone="info")
            elif action == "link":
                _link_account(db, connection, item["user_id"], item, plan, stamp)
                db.execute("UPDATE directory_sync_items SET detail = ? WHERE id = ?",
                           ("Linked to the existing account", item["id"]))
                counts["link"] += 1
            elif action == "update":
                columns = sorted(key for key in fields if key in ("full_name", "phone"))
                if columns:
                    db.execute("UPDATE users SET {} WHERE id = ?".format(
                        ", ".join("{} = ?".format(key) for key in columns)),
                        tuple(fields[key] for key in columns) + (item["user_id"],))
                _link_account(db, connection, item["user_id"], item, plan, stamp)
                db.execute("UPDATE directory_sync_items SET detail = ? WHERE id = ?",
                           ("Refreshed {}".format(", ".join(columns))
                            if columns else "Directory link refreshed", item["id"]))
                counts["update"] += 1
            elif action == "roles":
                added = _grant_roles(db, item["user_id"], grants)
                db.execute("UPDATE directory_sync_items SET detail = ? WHERE id = ?",
                           ("Granted {}".format(", ".join(added)) if added
                            else "Already held", item["id"]))
                counts["granted"] += len(added)
            elif action == "disable":
                problem = _deprovision_account(db, connection, item, actor)
                if problem:
                    db.execute("UPDATE directory_sync_items SET action = 'skip',"
                               " detail = ? WHERE id = ?", (problem, item["id"]))
                    counts["skipped"] += 1
                else:
                    db.execute("UPDATE directory_sync_items SET detail = ? WHERE id = ?",
                               ("Disabled to match the directory", item["id"]))
                    counts["disabled"] += 1
            elif action == "restore":
                db.execute("UPDATE users SET status = 'Active',"
                           " directory_departed = 0, directory_synced_at = ?"
                           " WHERE id = ? AND directory_connection_id = ?",
                           (stamp, item["user_id"], connection["id"]))
                db.execute("UPDATE directory_sync_items SET detail = ? WHERE id = ?",
                           ("Re-enabled: the directory has an active object again",
                            item["id"]))
                counts["restored"] += 1
            else:
                counts["skipped"] += 1
        except sqlite3.IntegrityError:
            db.rollback()
            db.execute("UPDATE directory_sync_items SET action = 'error',"
                       " detail = ? WHERE id = ?",
                       ("Another account already holds that address", item["id"]))
            counts["errors"] += 1
        except Exception as exc:
            db.rollback()
            db.execute("UPDATE directory_sync_items SET action = 'error', detail = ?"
                       " WHERE id = ?", (str(exc)[:200], item["id"]))
            counts["errors"] += 1

    seen = db.execute("SELECT seen FROM directory_sync_runs WHERE id = ?",
                      (run_id,)).fetchone()["seen"]
    counts["seen"] = seen
    status = "failed" if counts["errors"] and not (
        counts["create"] or counts["link"] or counts["update"]
        or counts["granted"] or counts["disabled"] or counts["restored"]) else (
        "partial" if counts["errors"] else "success")
    error = ""
    if counts["errors"]:
        error = db.execute("SELECT detail FROM directory_sync_items WHERE run_id = ?"
                           " AND action = 'error' ORDER BY id LIMIT 1",
                           (run_id,)).fetchone()["detail"]
    db.execute("UPDATE directory_sync_runs SET mode = 'apply', finished_at = ?,"
               " status = ?, created = ?, linked = ?, updated = ?, granted = ?,"
               " disabled = ?, restored = ?, skipped = ?, errors = ?, error = ?,"
               " summary_json = ?, actor_id = ? WHERE id = ?",
               (stamp, status, counts["create"], counts["link"], counts["update"],
                counts["granted"], counts["disabled"], counts["restored"],
                counts["skipped"], counts["errors"], error[:400],
                json.dumps(counts, default=str)[:4000],
                actor["id"] if actor is not None else None, run_id))
    db.execute("UPDATE integrations SET last_sync_at = ?, next_run_at = ?,"
               " updated_at = ? WHERE id = ?",
               (stamp, _next_run(connection), stamp, connection["integration_id"]))
    db.execute("UPDATE directory_connections SET last_status = ?,"
               " last_error = ? WHERE id = ?",
               ("ok" if status == "success" else "warning",
                error[:400], connection["id"]))
    db.commit()
    _record_sync_log(
        db, connection, "directory-apply",
        "failed" if status == "failed" else "success",
        "{} created, {} linked, {} updated, {} roles granted, {} disabled,"
        " {} restored, {} errors".format(
            counts["create"], counts["link"], counts["update"], counts["granted"],
            counts["disabled"], counts["restored"], counts["errors"]),
        run["started_at"], stamp)
    if counts["errors"]:
        _record_error(db, connection, "directory_apply", error)
    prune_runs(db, connection["id"])
    log_activity("SYNC", "integrations",
                 "Directory sync for {}: {} created, {} linked, {} updated, {}"
                 " roles granted, {} disabled, {} restored".format(
                     connection["name"], counts["create"], counts["link"],
                     counts["update"], counts["granted"], counts["disabled"],
                     counts["restored"]),
                 outcome="FAILURE" if status == "failed" else "SUCCESS",
                 record_type="integration", record_id=connection["integration_id"],
                 details={"run_id": run_id, "mode": "apply", **counts})
    if counts["disabled"]:
        from services.notifications import notify_permission
        notify_permission(
            db, "accounts", "Directory sync disabled accounts",
            "{} account{} taken offline by {} because the directory no longer"
            " vouches for them.".format(
                counts["disabled"], "" if counts["disabled"] == 1 else "s",
                connection["name"]),
            "", category="security", icon="shield", tone="warn",
            dedupe_prefix="directory-disable-{}-{}".format(
                connection["id"], stamp[:13]))
        db.commit()
    return {"counts": counts, "handover": handover, "status": status,
            "error": error}, ""


def _provision_account(db, connection, item, plan, actor, bindable):
    """Open a local account for a directory object that has none yet."""
    from services.audit import log_activity
    from services.accounts import generate_initial_password
    from services.accounts import next_username
    from services.recency import stamp as stamp_record
    from werkzeug.security import generate_password_hash
    email = (item["email"] or "").strip().lower()[:254]
    if not email or "@" not in email:
        db.execute("UPDATE directory_sync_items SET action = 'skip', detail = ?"
                   " WHERE id = ?", ("The directory holds no email address", item["id"]))
        return None
    entry = plan.get("entry") or {}
    name = (item["display_name"] or email.split("@")[0])[:120]
    employee_id = None
    code = str(entry.get("employee_code") or "").strip().upper()
    if code:
        record = db.execute("SELECT id FROM employees WHERE upper(employee_code) = ?",
                            (code,)).fetchone()
        if record is not None:
            from services.accounts import employee_link_problem
            if not employee_link_problem(db, record["id"], None):
                employee_id = record["id"]
    # The initial password is generated, handed to the administrator once, and never
    # kept in readable form. When the connection authenticates against the domain,
    # the account carries the unusable sentinel instead, so there is no local secret
    # at all and only the directory can open the door.
    issued = "" if bindable else generate_initial_password()
    placeholder = DIRECTORY_NO_PASSWORD if bindable else generate_password_hash(issued)
    username = next_username(db)
    grants = plan.get("grants") or []
    cursor = db.execute("""
        INSERT INTO users (full_name, email, phone, password_hash, role, status,
                           username, must_change_password, password_updated_at,
                           employee_id, directory_connection_id, directory_object_id,
                           directory_upn, directory_managed, directory_departed,
                           directory_synced_at)
        VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
    """, (name, email, str(entry.get("phone") or "")[:30], placeholder,
          connection["default_role"], username, 0 if bindable else 1, utc_stamp(),
          employee_id, connection["id"], item["object_id"],
          str(entry.get("upn") or email)[:254], utc_stamp()))
    user_id = cursor.lastrowid
    _grant_roles(db, user_id, grants)
    stamp_record(db, "users", user_id, actor)
    db.commit()
    log_activity("CREATE", "integrations",
                 "Account {} created for {} from {}".format(
                     username, name, connection["name"]),
                 record_type="user", record_id=user_id,
                 details={"email": email, "role": connection["default_role"],
                          "source": "directory", "connection": connection["name"],
                          "grants": list(grants),
                          "staff_record": employee_id,
                          "authentication": "domain" if bindable else "initial password",
                          "initial_password": "generated" if issued else "none"})
    return user_id, issued, username


def _local_entry(row):
    """The shape of a directory entry, built from a local account a plan will drop.

    A planned disable carries whatever the local row already knows, so the apply
    step reads one entry shape whether the object came from the directory or only
    from local history.
    """
    return {"object_id": row["directory_object_id"] or "",
            "dn": row["directory_upn"] or "",
            "upn": row["directory_upn"] or "",
            "email": row["email"] or "",
            "full_name": row["full_name"] or "",
            "phone": row["phone"] or "",
            "title": "", "department": "", "employee_code": "",
            "enabled": False, "source": "local"}


def _grant_roles(db, user_id, grants):
    """Add directory-granted roles; nothing here ever removes a role.

    An administrator's own ticks on Role Management stay in force, which is what
    makes the mapping additive rather than a second authority over access.
    """
    added = []
    for role_key in grants:
        held = db.execute("SELECT 1 FROM user_roles WHERE user_id = ? AND role_key = ?",
                          (user_id, role_key)).fetchone()
        if held is not None:
            continue
        db.execute("INSERT INTO user_roles (user_id, role_key, assigned_at)"
                   " VALUES (?, ?, ?)", (user_id, role_key, utc_stamp()))
        added.append(role_key)
    return added


def _link_account(db, connection, user_id, item, plan, stamp):
    """Record which directory object an account belongs to.

    The sign-in name is kept as the directory gave it, because that is what a
    later domain bind has to offer; an address alone would not be enough.
    """
    if user_id is None:
        return
    entry = plan.get("entry") or {}
    upn = str(entry.get("upn") or entry.get("dn") or item["email"] or "")[:254]
    db.execute("""
        UPDATE users SET directory_connection_id = ?, directory_object_id = ?,
                         directory_upn = ?, directory_managed = 1,
                         directory_synced_at = ?
        WHERE id = ?
    """, (connection["id"], item["object_id"], upn, stamp, user_id))
    _grant_roles(db, user_id, plan.get("grants") or [])


def _deprovision_account(db, connection, item, actor):
    """Take a directory-owned account offline, or explain why this one is exempt."""
    from core.access import current_user
    from services.audit import log_activity
    row = db.execute("SELECT * FROM users WHERE id = ?", (item["user_id"],)).fetchone()
    if row is None:
        return "The account no longer exists locally."
    if not row["directory_managed"] or row["directory_connection_id"] != connection["id"]:
        return "Left alone: the account is not owned by this connection."
    if row["role"] == "admin":
        return "Left alone: an administrator account is never disabled by sync."
    signed_in = current_user()
    if signed_in is not None and signed_in["id"] == row["id"]:
        return "Left alone: you are signed in with this account."
    db.execute("UPDATE users SET status = 'Disabled', directory_departed = 1,"
               " directory_synced_at = ? WHERE id = ?",
               (utc_stamp(), row["id"]))
    db.commit()
    log_activity("DISABLE", "integrations",
                 "Account {} disabled by directory sync on {}".format(
                     row["email"], connection["name"]),
                 record_type="user", record_id=row["id"],
                 details={"source": "directory", "connection": connection["name"],
                          "reason": item["detail"] or "object absent or disabled"})
    return ""


# ------------------------------------------------------------- release
def release_account(db, user, actor, reset_password=False):
    """Cut the directory link so the account is entirely local again."""
    from services.audit import log_activity
    from werkzeug.security import generate_password_hash
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    missing = [name for name, _ in USER_DIRECTORY_COLUMNS if name not in columns]
    if missing:
        return None, "This database has no directory columns yet."
    issued = ""
    sql = """
        UPDATE users SET directory_connection_id = NULL, directory_object_id = NULL,
                         directory_upn = NULL, directory_managed = 0,
                         directory_departed = 0, directory_synced_at = NULL
    """
    if reset_password:
        from services.accounts import generate_initial_password
        issued = generate_initial_password()
        sql += (", password_hash = ?, must_change_password = 1,"
                " password_updated_at = ?")
    sql += " WHERE id = ?"
    params = ((generate_password_hash(issued), utc_stamp(), user["id"])
              if reset_password else (user["id"],))
    db.execute(sql, params)
    db.commit()
    log_activity("UPDATE", "integrations",
                 "Account {} released from directory management".format(user["email"]),
                 record_type="user", record_id=user["id"],
                 details={"password_reset": bool(reset_password),
                          "initial_password": "generated" if issued else "unchanged"})
    return issued, ""


# ------------------------------------------------------------------- health
def connection_summary(db, connection):
    """The numbers one connection card shows, all read from local tables."""
    counts = db.execute("""
        SELECT COUNT(*) AS accounts,
               COALESCE(SUM(directory_departed), 0) AS departed,
               COALESCE(SUM(CASE WHEN status <> 'Active' THEN 1 ELSE 0 END), 0)
                   AS disabled
        FROM users WHERE directory_connection_id = ?
    """, (connection["id"],)).fetchone()
    run = db.execute("""
        SELECT id, status, finished_at, created, linked, disabled, errors
        FROM directory_sync_runs WHERE connection_id = ?
        ORDER BY started_at DESC, id DESC LIMIT 1
    """, (connection["id"],)).fetchone()
    return {"accounts": counts["accounts"], "departed": counts["departed"],
            "disabled": counts["disabled"], "run": run}


def directory_health(db):
    """The totals the Admin Dashboard and the integration hub both read."""
    try:
        connections = db.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(enabled), 0) AS active,
                   COALESCE(SUM(CASE WHEN last_status = 'failed' THEN 1 ELSE 0 END), 0)
                       AS failing
            FROM directory_registry
        """).fetchone()
        accounts = db.execute("""
            SELECT COUNT(*) AS managed,
                   COALESCE(SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END), 0)
                       AS active_accounts,
                   COALESCE(SUM(directory_departed), 0) AS departed
            FROM users WHERE directory_connection_id IS NOT NULL
        """).fetchone()
        mappings = db.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(enabled), 0) AS active
            FROM directory_group_mappings
        """).fetchone()
        runs = db.execute("""
            SELECT COUNT(*) AS runs,
                   COALESCE(SUM(CASE WHEN status IN ('failed', 'partial')
                                THEN 1 ELSE 0 END), 0) AS trouble
            FROM directory_sync_runs WHERE mode = 'apply'
              AND started_at >= ?
        """, ((datetime.now(timezone.utc) - timedelta(days=30))
              .isoformat(timespec="seconds"),)).fetchone()
    except sqlite3.Error:
        return {"ready": False}
    return {"ready": True,
            "connections": connections["total"], "active_connections":
                connections["active"], "failing_connections": connections["failing"],
            "managed": accounts["managed"], "active_accounts":
                accounts["active_accounts"], "departed": accounts["departed"],
            "mappings": mappings["total"], "active_mappings": mappings["active"],
            "runs": runs["runs"], "trouble": runs["trouble"]}


def due_connections(db):
    """Enabled connections whose interval has passed, for the scheduler to run."""
    try:
        rows = db.execute("""
            SELECT * FROM directory_registry
            WHERE enabled = 1 AND interval_minutes > 0
        """).fetchall()
    except sqlite3.Error:
        return []
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        last = row["last_sync_at"] or row["last_preview_at"]
        if not last:
            due.append(row)
            continue
        try:
            touched = datetime.fromisoformat(last)
        except ValueError:
            due.append(row)
            continue
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        if now - touched >= timedelta(minutes=row["interval_minutes"]):
            due.append(row)
    return due


def sync_now(db, connection, actor=None, trigger="manual", apply_after_preview=True):
    """Preview and, when asked, apply in one call. Used by the CLI scheduler."""
    run_id, seen, problem = plan_sync(db, connection, actor)
    if problem and not seen:
        return {"run_id": run_id, "counts": {}, "problem": problem}
    if not apply_after_preview:
        return {"run_id": run_id, "counts": {}, "problem": problem}
    result, apply_problem = apply_sync(db, connection, run_id, actor)
    return {"run_id": run_id, "counts": (result or {}).get("counts", {}),
            "problem": apply_problem or problem, "handover": (result or {}).get(
                "handover", [])}


def sync_due_connections(db):
    """Run every connection that is overdue; the scheduled entry point."""
    report = []
    for connection in due_connections(db):
        outcome = sync_now(db, connection, None, trigger="scheduled")
        report.append({"connection": connection["name"],
                       "run_id": outcome["run_id"],
                       "counts": outcome["counts"],
                       "problem": outcome["problem"]})
    return report


def sync_directory_command():
    """``flask sync-directory`` - for Task Scheduler or cron beside backup-db."""
    import click
    from core.db import connect_db
    db = connect_db()
    try:
        report = sync_due_connections(db)
    finally:
        db.close()
    if not report:
        click.echo("No directory connection is due for synchronization.")
        return
    for row in report:
        counts = row["counts"] or {}
        click.echo("{} run {}: created {} linked {} updated {} disabled {} errors"
                   " {}{}".format(
                       row["connection"], row["run_id"],
                       counts.get("create", 0), counts.get("link", 0),
                       counts.get("update", 0), counts.get("disabled", 0),
                       counts.get("errors", 0),
                       " - {}".format(row["problem"]) if row["problem"] else ""))




