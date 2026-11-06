
"""
Finance Flask route tests.

Run:
    pytest departments/finance/tests/test_routes.py -v

Important:
    Adapt the application factory, login process, and permissions
    to your existing Flask application.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )

    with flask_app.test_client() as test_client:
        yield test_client


# ---------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------

def test_finance_dashboard_requires_authentication(client):
    response = client.get(
        "/finance/",
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)


def test_finance_accounts_requires_authentication(client):
    response = client.get(
        "/finance/accounts",
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)


def test_finance_journals_requires_authentication(client):
    response = client.get(
        "/finance/journals",
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)


# ---------------------------------------------------------
# AUTHORIZED USER ACCESS
# ---------------------------------------------------------

@pytest.fixture
def authenticated_client(client):
    """
    Replace this test session with your actual login fixture.

    Example only:
    """

    with client.session_transaction() as session:
        session["user_id"] = 1

    return client


@pytest.mark.integration
def test_finance_dashboard_loads(authenticated_client):
    response = authenticated_client.get("/finance/")

    assert response.status_code == 200


@pytest.mark.integration
def test_finance_accounts_page_loads(authenticated_client):
    response = authenticated_client.get("/finance/accounts")

    assert response.status_code == 200


@pytest.mark.integration
def test_finance_journals_page_loads(authenticated_client):
    response = authenticated_client.get("/finance/journals")

    assert response.status_code == 200


@pytest.mark.integration
def test_general_ledger_page_loads(authenticated_client):
    response = authenticated_client.get("/finance/general-ledger")

    assert response.status_code == 200


@pytest.mark.integration
def test_finance_integrations_page_loads(authenticated_client):
    response = authenticated_client.get("/finance/integrations")

    assert response.status_code == 200


@pytest.mark.integration
def test_finance_audit_logs_page_loads(authenticated_client):
    response = authenticated_client.get("/finance/audit-logs")

    assert response.status_code == 200


# ---------------------------------------------------------
# ROUTE SECURITY
# ---------------------------------------------------------

@pytest.mark.integration
def test_unknown_finance_route_returns_404(authenticated_client):
    response = authenticated_client.get(
        "/finance/route-that-does-not-exist"
    )

    assert response.status_code == 404


@pytest.mark.integration
def test_finance_routes_do_not_expose_debug_errors(client):
    response = client.get(
        "/finance/route-that-does-not-exist"
    )

    assert b"Traceback" not in response.data


# ---------------------------------------------------------
# POST ROUTE PROTECTION
# ---------------------------------------------------------

def test_journal_post_endpoint_rejects_unauthenticated_request(client):
    response = client.post(
        "/finance/journals/1/post",
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)


def test_journal_approval_endpoint_rejects_unauthenticated_request(client):
    response = client.post(
        "/finance/journals/1/approve",
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)


def test_journal_reversal_endpoint_rejects_unauthenticated_request(client):
    response = client.post(
        "/finance/journals/1/reverse",
        data={
            "reason": "Test reversal"
        },
        follow_redirects=False
    )

    assert response.status_code in (302, 401, 403)
