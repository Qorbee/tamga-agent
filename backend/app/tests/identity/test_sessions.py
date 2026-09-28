import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies.models import CompanyKind
from app.modules.identity import service

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery"  # noqa: S105 (a test account's password)


async def invite_token(db: AsyncSession, *, company: str, email: str) -> str:
    created = await service.bootstrap_company(
        db, name=company, kind=CompanyKind.PILOT, owner_email=email
    )
    return created.invite_url.split("#", 1)[1]


async def test_an_invite_becomes_an_owner_session(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="Owner@Example.com")

    preview = await client.post("/invites/lookup", json={"token": token})
    assert preview.status_code == 200
    assert preview.json() == {
        "company_name": "Acme Lojistik",
        "email": "owner@example.com",
        "role": "owner",
        "has_account": False,
    }

    accepted = await client.post(
        "/invites/accept", json={"token": token, "password": PASSWORD, "name": "Owner"}
    )
    assert accepted.status_code == 200
    cookie = accepted.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    body = accepted.json()
    assert body["role"] == "owner"
    assert body["company"]["kind"] == "pilot"
    assert body["company"]["mode"] == "shadow"  # a pilot starts sending nothing (ADR-0009)

    me = await client.get("/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@example.com"

    again = await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    assert again.status_code == 410


async def test_sign_in_and_out(db: AsyncSession, client: httpx.AsyncClient) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    client.cookies.clear()

    wrong = await client.post(
        "/sessions", json={"email": "owner@example.com", "password": "not the password"}
    )
    unknown = await client.post(
        "/sessions", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    # The same answer either way, so a response never says which emails have accounts.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"

    signed_in = await client.post(
        "/sessions", json={"email": " OWNER@example.com ", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    assert (await client.get("/me")).status_code == 200

    signed_out = await client.delete("/sessions/current")
    assert signed_out.status_code == 204
    client.cookies.clear()  # the browser drops it on the expired Set-Cookie; the jar must be told

    assert (await client.get("/me")).status_code == 401


async def test_a_spent_session_token_no_longer_works(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    session_token = client.cookies["tamga_session"]

    await client.delete("/sessions/current")
    client.cookies.set("tamga_session", session_token, domain="testserver")

    assert (await client.get("/me")).status_code == 401


async def test_only_an_owner_invites(db: AsyncSession, client: httpx.AsyncClient) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})

    invited = await client.post("/invites", json={"email": "ops@example.com", "role": "ops"})
    assert invited.status_code == 201
    assert invited.json()["url"].startswith("http://localhost:3000/invite#")
    ops_token = invited.json()["url"].split("#", 1)[1]

    client.cookies.clear()
    joined = await client.post("/invites/accept", json={"token": ops_token, "password": PASSWORD})
    assert joined.json()["role"] == "ops"

    refused = await client.post("/invites", json={"email": "x@example.com", "role": "viewer"})
    assert refused.status_code == 403


async def test_an_existing_account_joins_with_its_own_password(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    first = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": first, "password": PASSWORD})
    client.cookies.clear()
    second = await invite_token(db, company="Beta Nakliyat", email="owner@example.com")

    preview = await client.post("/invites/lookup", json={"token": second})
    assert preview.json()["has_account"] is True

    wrong = await client.post(
        "/invites/accept", json={"token": second, "password": "a different password"}
    )
    assert wrong.status_code == 401
    # The failed attempt rolled its claim back: the invite is still good.
    right = await client.post("/invites/accept", json={"token": second, "password": PASSWORD})
    assert right.status_code == 200
    assert right.json()["company"]["name"] == "Beta Nakliyat"
    assert len(right.json()["memberships"]) == 2


async def test_a_guest_owns_a_fresh_sandbox(client: httpx.AsyncClient) -> None:

    first = await client.post("/sessions/guest")
    client.cookies.clear()
    second = await client.post("/sessions/guest")

    assert first.status_code == second.status_code == 201
    one, two = first.json(), second.json()
    assert one["user"]["is_guest"] is True
    assert one["role"] == "owner"
    assert one["company"]["kind"] == "sandbox"
    assert one["company"]["mode"] == "live"  # simulated channels only (ADR-0009)
    assert one["company"]["id"] != two["company"]["id"]


async def test_guest_sandboxes_are_rate_limited_per_client(client: httpx.AsyncClient) -> None:
    allowed = 5  # GUEST_SESSIONS_PER_CLIENT_PER_HOUR's default

    statuses = [(await client.post("/sessions/guest")).status_code for _ in range(allowed + 1)]

    assert statuses == [201] * allowed + [429]


async def test_password_attempts_are_rate_limited_per_client(client: httpx.AsyncClient) -> None:
    allowed = 20  # PASSWORD_ATTEMPTS_PER_CLIENT_PER_HOUR's default
    body = {"email": "nobody@example.com", "password": "not the password"}

    statuses = [(await client.post("/sessions", json=body)).status_code for _ in range(allowed + 1)]

    assert statuses == [401] * allowed + [429]


async def test_a_sandbox_cannot_invite_anyone(client: httpx.AsyncClient) -> None:
    # A guest owns their sandbox; an invite would turn it into a lasting account.
    await client.post("/sessions/guest")

    refused = await client.post("/invites", json={"email": "friend@example.com", "role": "owner"})

    assert refused.status_code == 403


async def test_a_member_of_two_companies_switches_between_them(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    first = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": first, "password": PASSWORD})
    second = await invite_token(db, company="Beta Nakliyat", email="owner@example.com")
    await client.post("/invites/accept", json={"token": second, "password": PASSWORD})
    client.cookies.clear()
    signed_in = await client.post(
        "/sessions", json={"email": "owner@example.com", "password": PASSWORD}
    )
    beta = next(
        m["company"]["id"]
        for m in signed_in.json()["memberships"]
        if m["company"]["name"] == "Beta Nakliyat"
    )

    switched = await client.put("/sessions/current/company", json={"company_id": beta})
    assert switched.status_code == 200
    assert (await client.get("/me")).json()["company"]["name"] == "Beta Nakliyat"

    elsewhere = await client.put(
        "/sessions/current/company", json={"company_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert elsewhere.status_code == 404
