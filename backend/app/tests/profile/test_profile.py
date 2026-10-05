import email.message
import uuid
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.sources import Taxpayer
from app.modules.companies.models import Company
from app.modules.conversations import service as conversations
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.conversations.models import MailFolder, Message
from app.modules.facts import service as facts
from app.modules.facts.service import NewFact
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind, PartyKind
from app.modules.parties.service import Identifier
from app.modules.profile import service
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.identity.test_sessions import PASSWORD, invite_token

# The mail these tests store goes to the storage service, in the bucket the tests make.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

OWN = "ops@acme.example"
# Tax numbers whose check digits hold, so they pass with or without a country's check.
FIRMS = "1111111114"
OURS = "2222222224"
OTHER = "9876543217"
SIGNED = "5554443334"


def mail(sender: str, subject: str, body: str = "Hello") -> bytes:
    message = email.message.EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = "Mon, 03 Aug 2026 09:00:00 +0300"
    message["Message-ID"] = f"<{uuid.uuid4()}@test>"
    message.set_content(body)
    return message.as_bytes()


async def store(
    company_id: uuid.UUID, sender: str, *, sent: bool, tax_id: str | None = None
) -> uuid.UUID:
    """A mail the company received, or sent from its Sent folder, carrying ``tax_id`` as a fact."""
    subject = f"mail {uuid.uuid4()}"
    async with open_session() as db:
        await conversations.ingest_mail(
            db,
            company_id=company_id,
            place=conversations.FolderPlace(
                mailbox=OWN, folder="Sent" if sent else "INBOX", uidvalidity=1, sent=sent
            ),
            fetched=FetchedMail(
                uid=uuid.uuid4().int % 10**9, raw=mail(sender, subject), internaldate=None
            ),
            own_addresses={OWN},
        )
        message_id = await db.scalar(select(Message.id).where(Message.subject == subject))
        assert message_id is not None
        if tax_id:
            await facts.record_facts(
                db,
                company_id=company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=message_id,
                facts=[NewFact("tax_id", tax_id, tax_id, 1.0, "test")],
            )
        await db.commit()
    return message_id


async def on_a_file(company_id: uuid.UUID, tax_id: str) -> None:
    async with open_session() as db:
        await facts.record_facts(
            db,
            company_id=company_id,
            item_kind=ItemKind.DOCUMENT,
            item_id=uuid.uuid4(),
            facts=[NewFact("tax_id", tax_id, tax_id, 1.0, "test")],
        )
        await db.commit()


async def read_mailbox(company_id: uuid.UUID, address: str = OWN) -> None:
    async with open_session() as db:
        db.add(
            MailFolder(
                company_id=company_id, mailbox=address, folder="INBOX", uidvalidity=1, last_uid=0
            )
        )
        await db.commit()


async def firm(company_id: uuid.UUID, name: str, *identifiers: Identifier) -> None:
    """A customer card in the books."""
    async with open_session() as db:
        await parties.sync_party(
            db,
            company_id=company_id,
            source="books",
            source_id=name,
            name=name,
            kind=PartyKind.ORGANISATION,
            identifiers=identifiers,
        )
        await db.commit()


def registry(answers: Mapping[str, str] | None) -> Any:
    """The tax registry as the books source asks it; None when no source can."""

    async def look_up(db: Any, company_id: uuid.UUID, tax_id: str) -> Taxpayer | None:
        if answers is None:
            raise books.RegistryUnavailable("no source")
        title = answers.get(tax_id)
        return Taxpayer(tax_id=tax_id, title=title) if title else None

    return look_up


def domains(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {own["domain"]: own for own in profile["domains"]}


async def test_the_mail_shows_the_companys_own_domains(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await read_mailbox(company_id)
    await store(company_id, "Ali <ali@acme-group.example>", sent=True)
    await store(company_id, "Me <me@gmail.com>", sent=True)
    await store(company_id, "Ada <ada@stone.example>", sent=False)

    profile = (await client.get("/company/profile")).json()

    found = domains(profile)
    # A public provider's domain is never the company's, though it sent from it.
    assert set(found) == {"acme.example", "acme-group.example"}
    assert found["acme.example"] == {
        "domain": "acme.example",
        "ours": True,
        "sources": ["mailbox"],
        "sent": 0,
        "received": 0,
        "removable": False,
    }
    assert found["acme-group.example"]["sources"] == ["sent_mail"]
    assert found["acme-group.example"]["sent"] == 1 and found["acme-group.example"]["removable"]
    async with open_session() as db:
        ourselves = await service.who_we_are(db, company_id)
    assert ourselves.writes_from("Kim@Ops.Acme.example")
    assert not ourselves.writes_from("ada@stone.example")
    assert not ourselves.writes_from("ali@notacme.example")


async def test_a_domain_the_owner_adds_is_checked(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await read_mailbox(company_id)
    await firm(company_id, "Stone Ltd", Identifier(IdentifierKind.EMAIL, "ada@stone.example"))
    await firm(company_id, "STONE LOJİSTİK TİC. LTD. ŞTİ.")
    await store(company_id, "Kim <kim@partner.example>", sent=False)

    async def add(domain: str, *, confirm: bool = False) -> dict[str, Any]:
        response = await client.post(
            "/company/profile/domains", json={"domain": domain, "confirm": confirm}
        )
        assert response.status_code == 200, response.text
        answer: dict[str, Any] = response.json()
        return answer

    def said(answer: dict[str, Any]) -> tuple[bool, str, list[dict[str, Any]]]:
        check = answer["check"]
        findings = [{k: v for k, v in f.items() if v is not None} for f in check["findings"]]
        return answer["saved"], check["verdict"], findings

    assert said(await add("not a domain")) == (False, "invalid", [{"reason": "not_valid"}])
    assert said(await add("gmail.com", confirm=True)) == (
        False,
        "refused",
        [{"reason": "shared_domain"}],
    )
    # The books hold a firm's address at it: confirming changes nothing.
    assert said(await add("https://www.stone.example/contact", confirm=True)) == (
        False,
        "refused",
        [{"reason": "firms_address", "firm": "Stone Ltd"}],
    )
    # Mail from it only comes in.
    assert said(await add("partner.example")) == (
        False,
        "confirm",
        [{"reason": "only_received", "count": 1}],
    )
    # A firm's name starts with the domain's, and no mail shows it.
    doubted = await add("info@stonelojistik.example")
    assert said(doubted) == (
        False,
        "confirm",
        [{"reason": "firms_name", "firm": "STONE LOJİSTİK TİC. LTD. ŞTİ."}, {"reason": "no_mail"}],
    )
    assert "stonelojistik.example" not in domains(doubted["profile"])
    # Written with an accent, where the plain spelling is the company's mailbox.
    accented = await add("acmé.example")
    assert said(accented)[2][0] == {"reason": "spelling", "suggestion": "acme.example"}

    confirmed = await add("stonelojistik.example", confirm=True)
    assert confirmed["saved"]
    added = domains(confirmed["profile"])["stonelojistik.example"]
    assert (added["ours"], added["sources"], added["removable"]) == (True, ["owner"], True)
    async with open_session() as db:
        assert (await service.who_we_are(db, company_id)).domains == {
            "acme.example",
            "stonelojistik.example",
        }


async def test_the_owner_can_say_a_domain_is_not_theirs(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await read_mailbox(company_id)
    await store(company_id, "Ali <ali@acme-group.example>", sent=True)
    added = await client.post(
        "/company/profile/domains", json={"domain": "acme.example.org", "confirm": True}
    )
    assert added.json()["saved"]

    removed = await client.delete("/company/profile/domains/acme-group.example")

    assert removed.status_code == 200
    # Its sent mail stays on view, with the owner's answer.
    group = domains(removed.json())["acme-group.example"]
    assert (group["ours"], group["sources"]) == (False, ["sent_mail"])
    again = await client.post("/company/profile/domains", json={"domain": "acme-group.example"})
    assert again.json()["saved"] and domains(again.json()["profile"])["acme-group.example"]["ours"]
    # One the owner added and then refused stays refused, so mail found later does not undo it.
    gone = await client.delete("/company/profile/domains/acme.example.org")
    await store(company_id, "Ali <ali@acme.example.org>", sent=True)
    refused = domains((await client.get("/company/profile")).json())["acme.example.org"]
    assert (refused["ours"], refused["sources"]) == (False, ["sent_mail"])
    assert gone.status_code == 200
    # A mailbox's domain stays the company's.
    kept = await client.delete("/company/profile/domains/acme.example")
    assert kept.status_code == 409


async def test_a_tax_number_is_checked_before_it_is_saved(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    await read_mailbox(company_id)
    await firm(company_id, "Stone Ltd", Identifier(IdentifierKind.TAX_ID, FIRMS))
    # The card the company keeps for itself in its books.
    await firm(company_id, "ACME LOJİSTİK", Identifier(IdentifierKind.TAX_ID, OURS))
    titles = {OURS: "ACME LOJİSTİK TİCARET LTD. ŞTİ.", OTHER: "BAŞKA NAKLİYAT A.Ş."}
    monkeypatch.setattr(books, "look_up_taxpayer", registry(titles))

    async def put(tax_id: str, **body: Any) -> dict[str, Any]:
        response = await client.put("/company/profile/tax-id", json={"tax_id": tax_id, **body})
        assert response.status_code == 200, response.text
        answer: dict[str, Any] = response.json()
        return answer

    invalid = await put("12")
    assert (invalid["saved"], invalid["check"]["verdict"]) == (False, "invalid")
    refused = await put(FIRMS, confirm=True)
    assert (refused["saved"], refused["check"]["findings"][0]["firm"]) == (False, "Stone Ltd")
    # The registry names someone else: shown to the owner, saved only once they confirm.
    doubted = await put(OTHER)
    assert (doubted["saved"], doubted["check"]["verdict"]) == (False, "confirm")
    assert doubted["check"]["findings"][0]["title"] == "BAŞKA NAKLİYAT A.Ş."
    assert doubted["profile"]["tax_id"] is None
    confirmed = await put(OTHER, confirm=True)
    assert confirmed["saved"] and confirmed["profile"]["legal_name"] == "BAŞKA NAKLİYAT A.Ş."

    # Registered under a name that starts as the company's domain does: no doubt, and its own
    # card in the books is no other firm's.
    ours = await put(f"{OURS[:3]} {OURS[3:6]} {OURS[6:]}")
    assert ours["saved"] and ours["check"]["value"] == OURS
    profile = ours["profile"]
    assert profile["legal_name"] == "ACME LOJİSTİK TİCARET LTD. ŞTİ."
    assert profile["tax_id"]["registry"] == "found" and profile["tax_id"]["checked_at"]

    # Saved again while the registry cannot be asked: its last answer stands.
    monkeypatch.setattr(books, "look_up_taxpayer", registry(None))
    again = await put(OURS, legal_name="Something else")
    assert again["saved"] and again["profile"]["tax_id"] == profile["tax_id"]
    assert again["profile"]["legal_name"] == "ACME LOJİSTİK TİCARET LTD. ŞTİ."


async def test_a_number_the_registry_does_not_know_needs_the_companys_records(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        document_id = uuid.uuid4()
        await facts.record_facts(
            db,
            company_id=company_id,
            item_kind=ItemKind.DOCUMENT,
            item_id=document_id,
            facts=[NewFact("tax_id", SIGNED, SIGNED, 1.0, "test")],
        )
        await db.commit()
    monkeypatch.setattr(books, "look_up_taxpayer", registry({}))

    seen = await client.put(
        "/company/profile/tax-id", json={"tax_id": SIGNED, "legal_name": "Acme"}
    )

    # On the company's files, unknown to the registry: saved, with the name the owner typed.
    assert seen.json()["saved"]
    tax_id = seen.json()["profile"]["tax_id"]
    assert (tax_id["registry"], tax_id["evidence"]["papers"]) == ("not_found", 1)
    assert seen.json()["profile"]["legal_name"] == "Acme"

    # Neither the registry nor the records know it, and the registry cannot be asked.
    monkeypatch.setattr(books, "look_up_taxpayer", registry(None))
    unseen = await client.put("/company/profile/tax-id", json={"tax_id": OTHER})
    assert unseen.json()["check"]["findings"] == [
        {"reason": "not_seen", "firm": None, "title": None, "count": None, "suggestion": None}
    ]
    saved = await client.put("/company/profile/tax-id", json={"tax_id": OTHER, "confirm": True})
    after = saved.json()["profile"]
    # A new number drops the name given for the old one.
    assert (after["tax_id"]["registry"], after["legal_name"]) == ("not_asked", None)


async def test_the_companys_own_sent_mail_proposes_its_tax_number(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await firm(company_id, "Stone Ltd", Identifier(IdentifierKind.TAX_ID, FIRMS))
    await store(company_id, "Ali <ali@acme.example>", sent=True, tax_id=SIGNED)
    await store(company_id, "Ali <ali@acme.example>", sent=True, tax_id=SIGNED)
    # A firm's number the company wrote, and one it only received.
    await store(company_id, "Ali <ali@acme.example>", sent=True, tax_id=FIRMS)
    await store(company_id, "Ada <ada@stone.example>", sent=False, tax_id=OTHER)

    proposal = (await client.get("/company/profile")).json()["proposal"]

    assert proposal == {
        "tax_id": SIGNED,
        "evidence": {"papers": 0, "sent_mails": 2, "received_mails": 0},
    }
    # Two numbers written as often: no guess, and a file carrying one does not break the tie.
    await store(company_id, "Ali <ali@acme.example>", sent=True, tax_id=OURS)
    await store(company_id, "Ali <ali@acme.example>", sent=True, tax_id=OURS)
    await on_a_file(company_id, OURS)
    assert (await client.get("/company/profile")).json()["proposal"] is None


async def test_a_short_name_still_knows_its_own_card(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        await db.execute(update(Company).where(Company.id == company_id).values(name="ABC"))
        await db.commit()
    await firm(company_id, "ABC", Identifier(IdentifierKind.TAX_ID, OURS))
    await on_a_file(company_id, OURS)
    monkeypatch.setattr(books, "look_up_taxpayer", registry(None))

    saved = await client.put("/company/profile/tax-id", json={"tax_id": OURS})

    assert saved.json()["saved"]


async def test_the_mail_is_read_as_the_owner_answered_it(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await read_mailbox(company_id)
    await store(company_id, "Ali <ali@group.acme.example>", sent=True)
    # A mail received first, whose copy in the Sent folder was kept as a copy of it.
    original = await store(company_id, "Can <can@acme-tr.example>", sent=False)
    copy = await store(company_id, "Can <can@acme-tr.example>", sent=True)
    async with open_session() as db:
        await db.execute(update(Message).where(Message.id == copy).values(copy_of_id=original))
        await db.commit()
    await firm(company_id, "Yüzbaşı Ltd", Identifier(IdentifierKind.EMAIL, "ada@yüzbaşı.example"))

    await client.delete("/company/profile/domains/group.acme.example")
    # A firm's address kept in Unicode is found by the domain typed either way.
    typed = await client.post(
        "/company/profile/domains", json={"domain": "xn--yzba-0ra53a5k.example"}
    )

    async with open_session() as db:
        ourselves = await service.who_we_are(db, company_id)
    assert ourselves.domains == {"acme.example", "acme-tr.example"}
    # The subdomain the owner said is not theirs stays apart from the domain above it.
    assert not ourselves.writes_from("ali@group.acme.example")
    assert ourselves.writes_from("kim@ops.acme.example")
    assert typed.json()["check"]["findings"][0]["firm"] == "Yüzbaşı Ltd"


async def test_only_the_owner_reads_the_tax_number_whole(
    db: AsyncSession, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    monkeypatch.setattr(books, "look_up_taxpayer", registry(None))
    saved = await client.put("/company/profile/tax-id", json={"tax_id": OURS, "confirm": True})
    assert saved.json()["saved"]
    invited = await client.post("/invites", json={"email": "v@example.com", "role": "viewer"})
    client.cookies.clear()
    viewer = invited.json()["url"].split("#", 1)[1]
    await client.post("/invites/accept", json={"token": viewer, "password": PASSWORD})

    read = (await client.get("/company/profile")).json()

    # A sole trader's tax number is their own identity number.
    assert read["tax_id"]["tax_id"] == "••••••••24"
    refused = await client.put("/company/profile/tax-id", json={"tax_id": OURS})
    assert refused.status_code == 403
