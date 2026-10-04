"""The controller's opinion (05, Decisions today; ADR-0002): each evaluation that waits for the
owner is weighed once, within the company's budget, its bill fenced as outside data; the opinion
is kept with that evaluation and in the log, moves nothing, and a new evaluation is weighed
anew."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.messages import UserPromptPart as UserPrompt
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import text

from app.config import get_settings
from app.modules.decisions import advising, service
from app.packs.logistics import sandbox
from app.platform import llm
from app.platform.db import open_session
from app.tests.decisions.test_decisions import Vault, a_sandbox, answer, by_bill
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


class Controller:
    """A stand-in for the controller: what it was sent, and what it answers each time."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.answers: list[dict[str, Any]] = []


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Controller]:
    """Puts a stand-in in the controller's place, with a budget of ``budget`` bills a day,
    answering ``answers`` in turn and the last one after."""
    monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(respond))
    stand_in = Controller()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[0]
        assert isinstance(request, ModelRequest)
        stand_in.prompts.append(
            "".join(
                item
                for part in request.parts
                if isinstance(part, UserPrompt)
                for item in part.content
                if isinstance(item, str)
            )
        )
        reply = stand_in.answers[min(len(stand_in.prompts), len(stand_in.answers)) - 1]
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=reply)])

    def install(*answers: dict[str, Any], budget: int = 5) -> Controller:
        monkeypatch.setattr(get_settings(), "advice_per_day", budget)
        stand_in.answers = list(answers)
        return stand_in

    return install


PART = {"proposal": "pay_part", "amount": "950.00", "reason": "One of two trucks is proven."}


async def test_a_waiting_bill_is_weighed_once_and_the_opinion_moves_nothing(
    client: httpx.AsyncClient, vault: Vault, controller: Callable[..., Controller]
) -> None:
    stand_in = controller(PART)
    await a_sandbox(client)
    await service.evaluate_pending()

    assert await advising.advise_pending() == 1

    # Only the bill that waits for the owner is weighed; the held one is the rules'.
    [prompt] = stand_in.prompts
    assert "BEGIN OUTSIDE DATA" in prompt and sandbox.DELIVERED_BILL in prompt
    assert "approval_limit: propose" in prompt.split("BEGIN OUTSIDE DATA")[0]
    decisions = await by_bill(client)
    delivered, moving = decisions[sandbox.DELIVERED_BILL], decisions[sandbox.MOVING_BILL]
    assert delivered["advice"] == {
        "evaluation": 1,
        "proposal": "pay_part",
        "amount": "950.00",
        "reason": "One of two trucks is proven.",
        "model": llm.model_name("controller"),
    }
    assert delivered["state"] == "awaiting_owner" and delivered["amount"] == "1900.00"
    assert moving["advice"] is None
    events = (await client.get("/decision-events")).json()["items"]
    assert [event["event"] for event in events] == ["proposed", "proposed", "advised"]
    assert events[-1]["body"]["proposal"] == "pay_part"
    assert (await client.get("/decision-chain")).json()["broken_at"] is None

    # Weighed once: the next pass sends nothing.
    assert await advising.advise_pending() == 0 and len(stand_in.prompts) == 1

    # The owner's amount is a new evaluation, weighed anew.
    edited = (await answer(client, delivered, "edit", amount="950.00")).json()
    assert edited["advice"] is None
    stand_in.answers = [{"proposal": "pay", "reason": "The proven truck's share."}]
    assert await advising.advise_pending() == 1
    weighed = (await client.get(f"/decisions/{delivered['id']}")).json()
    assert (weighed["advice"]["evaluation"], weighed["advice"]["proposal"]) == (2, "pay")
    assert weighed["advice"]["amount"] is None


async def test_an_unusable_answer_is_not_asked_again_and_every_request_counts(
    client: httpx.AsyncClient, vault: Vault, controller: Callable[..., Controller]
) -> None:
    # A part as large as the bill, twice: the answer cannot be used.
    too_much = {**PART, "amount": "1900.00"}
    stand_in = controller(too_much, budget=2)
    await a_sandbox(client)
    await service.evaluate_pending()

    assert await advising.advise_pending() == 0
    assert len(stand_in.prompts) == 2
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["advice"] is None
    events = (await client.get("/decision-events")).json()["items"]
    assert "advised" not in [event["event"] for event in events]
    # The request stands: asked again for the same, it would cost again for the same.
    assert await advising.advise_pending() == 0 and len(stand_in.prompts) == 2

    # It counted: a new evaluation takes the day's last request, and the one after it waits.
    stand_in.answers = [PART]
    edited = (await answer(client, delivered, "edit", amount="1000.00")).json()
    assert await advising.advise_pending() == 1
    await answer(client, edited, "edit", amount="900.00")
    assert await advising.advise_pending() == 0 and len(stand_in.prompts) == 3
    # A budget lowered below what was spent sends nothing either.
    get_settings().advice_per_day = 1
    assert await advising.advise_pending() == 0 and len(stand_in.prompts) == 3


async def test_what_the_controller_was_shown_changing_is_weighed_again(
    client: httpx.AsyncClient,
    vault: Vault,
    controller: Callable[..., Controller],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stand_in = controller({"proposal": "hold", "reason": "Due in a month."})
    company_id = await a_sandbox(client)
    await service.evaluate_pending()
    assert await advising.advise_pending() == 1

    # The books say more of the bill, which no rule reads: the same evaluation, weighed again.
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE invoices SET description = 'Second of two loads' "
                "WHERE company_id = :id AND number = :number"
            ),
            {"id": company_id, "number": sandbox.DELIVERED_BILL},
        )
        await db.commit()
    stand_in.answers = [{"proposal": "ask", "reason": "Which load is this?"}]
    assert await advising.advise_pending() == 1
    assert "Second of two loads" in stand_in.prompts[-1]
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert (delivered["evaluation"], delivered["advice"]["proposal"]) == (1, "ask")
    advised = [
        event
        for event in (await client.get("/decision-events")).json()["items"]
        if event["event"] == "advised"
    ]
    assert [event["body"]["proposal"] for event in advised] == ["hold", "ask"]
    assert advised[-1]["body"]["trace_id"] is None and len(advised[-1]["body"]["inputs"]) == 64

    # Nothing changed, but time does: an opinion that old is weighed again.
    assert await advising.advise_pending() == 0
    monkeypatch.setattr(advising, "ADVICE_FRESH", timedelta(0))
    assert await advising.advise_pending() == 1 and len(stand_in.prompts) == 3


async def test_without_a_budget_no_bill_is_sent(
    client: httpx.AsyncClient, vault: Vault, controller: Callable[..., Controller]
) -> None:
    stand_in = controller(PART, budget=0)
    await a_sandbox(client)
    await service.evaluate_pending()

    assert await advising.advise_pending() == 0 and stand_in.prompts == []
