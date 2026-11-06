"""Does the configured endpoint actually answer?

A test is reachability plus a bind, never a data write: it opens the socket or
the directory, says how long that took, and records one
``integration_connection_tests`` row that the history screen can read later.
Failure messages name the exception class only - an unreachable host and a
refused password look different to the administrator, but neither message may
carry the credential that was tried.
"""
import smtplib
import ssl
import time
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # REST-style endpoints report the missing package instead.
    requests = None

from core.config import DIRECTORY_LDAP_TIMEOUT

from .security import read_sealed

TIMEOUT = DIRECTORY_LDAP_TIMEOUT


def get_credential(db, integration_id, *names):
    """The first of ``names`` this integration has on file, as plaintext."""
    for name in names:
        value, _problem = read_sealed(db, integration_id, name)
        if value:
            return value
    return ""


def parse_endpoint(endpoint, allowed_schemes):
    parsed = urlparse((endpoint or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in allowed_schemes:
        raise ValueError("Use one of {} for the endpoint.".format(
            ", ".join(sorted(allowed_schemes))))
    if not parsed.hostname:
        raise ValueError("The endpoint does not name a server.")
    return parsed, scheme


def test_http_connection(endpoint, token=""):
    if requests is None:
        raise ValueError("Install the requests package to test an HTTP endpoint.")
    parsed, _scheme = parse_endpoint(endpoint, {"http", "https"})
    headers = {"Authorization": "Bearer {}".format(token)} if token else {}

    started = time.perf_counter()
    response = requests.get(endpoint, headers=headers, timeout=TIMEOUT,
                            allow_redirects=False, verify=True, stream=True)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    status = response.status_code
    response.close()

    # 401/403 proves the service answered but not that the credential worked.
    if status in (401, 403):
        return False, ("Server responded with HTTP {}; the credential was not "
                       "accepted.".format(status)), elapsed
    if 200 <= status < 400:
        return True, "HTTP service responded with status {}.".format(status), elapsed
    return False, "HTTP service returned status {}.".format(status), elapsed


def test_smtp_connection(endpoint, username="", password=""):
    parsed, scheme = parse_endpoint(endpoint, {"smtp", "smtps"})
    host = parsed.hostname
    port = parsed.port or (465 if scheme == "smtps" else 587)

    started = time.perf_counter()
    context = ssl.create_default_context()
    if scheme == "smtps":
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        server.ehlo()
        if scheme != "smtps":
            server.starttls(context=context)
            server.ehlo()
        if username and password:
            server.login(username, password)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return True, "Mail service connection succeeded.", elapsed


def test_connection(db, integration):
    """(ok, message, response_time_ms) for one registry row."""
    integration_type = integration["integration_type"]
    endpoint = (integration["endpoint"] or "").strip()
    username = (integration["username"] or "").strip()

    if not endpoint:
        return False, "Set the endpoint before testing the connection.", None

    password = get_credential(db, integration["id"],
                              "password", "service_account_password", "client_secret")
    token = get_credential(db, integration["id"],
                           "access_token", "api_key")

    try:
        if integration_type == "Active Directory / LDAP":
            from services.directory import integration_ldap_spec
            from services.directory import ldap_test_bind
            return ldap_test_bind(integration_ldap_spec(db, integration))
        if integration_type == "Email / SMTP":
            return test_smtp_connection(endpoint, username, password)
        if integration_type in ("REST API", "SAP ERP", "Biometric Attendance",
                                "SMS Gateway"):
            return test_http_connection(endpoint, token or password)
        raise ValueError("Connection testing is not available for {}.".format(
            integration_type))
    except Exception as exc:
        return False, "{}: {}".format(type(exc).__name__, str(exc)[:200]), None
