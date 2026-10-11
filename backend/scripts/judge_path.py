"""Walk a judge's path through the playground against a running API, step by step, with timings,
and say which steps pass. It reads no settings and touches no database: only the API, as a
judge's browser would.

python scripts/judge_path.py --api https://api.example.com --web https://example.com

Each step is one of:
- pass;
- fail: broken. It stops the walk, since every later step needs it;
- late: it works, but not within its target, or it has not run yet (anchoring has its own
  schedule);
- missing: not built yet.

Exit status 1 only when a step fails, so a deploy check passes on what exists.
"""

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

COOKIE = "tamga_session"
OUTCOMES = ("pass", "late", "missing", "fail")


@dataclass
class Step:
    name: str
    outcome: str  # "pass", "fail", "late" or "missing"
    seconds: float
    detail: str


class Walk:
    def __init__(self, api: str, web: str) -> None:
        # The session cookie is Secure, so it is sent by hand: a local API on http would not get it.
        self.http = httpx.Client(base_url=api, timeout=30, headers={"Origin": web})
        self.steps: list[Step] = []

    def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.request(method, path, **kwargs)

    def wait(self, ask: Callable[[], Any], within: float, every: float = 2) -> tuple[Any, float]:
        """The first truthy answer of ``ask`` and how long it took; None when none came."""
        started = time.monotonic()
        while True:
            answer = ask()
            seconds = time.monotonic() - started
            if answer or seconds > within:
                return answer, seconds
            time.sleep(every)

    def note(self, name: str, outcome: str, seconds: float, detail: str) -> bool:
        self.steps.append(Step(name, outcome, round(seconds, 1), detail))
        print(f"{outcome:>7}  {seconds:6.1f}s  {name}: {detail}", flush=True)
        return outcome == "pass"


def items(response: httpx.Response) -> list[dict[str, Any]]:
    return response.json()["items"] if response.status_code == 200 else []


def walk(api: str, web: str, within: dict[str, float]) -> list[Step]:
    w = Walk(api, web)

    started = time.monotonic()
    ready = w.call("GET", "/health/ready")
    if not w.note(
        "API ready",
        "pass" if ready.status_code == 200 else "fail",
        time.monotonic() - started,
        f"HTTP {ready.status_code}",
    ):
        return w.steps

    started = time.monotonic()
    guest = w.call("POST", "/sessions/guest")
    cookie = guest.cookies.get(COOKIE)
    if not w.note(
        "guest sandbox",
        "pass" if guest.status_code == 201 and cookie else "fail",
        time.monotonic() - started,
        f"HTTP {guest.status_code}",
    ):
        return w.steps
    w.http.headers["Cookie"] = f"{COOKIE}={cookie}"
    company = guest.json()["company"]["id"]

    found, seconds = w.wait(lambda: items(w.call("GET", "/projects")), within["seed"])
    if not w.note(
        "sandbox seeded",
        "pass" if found else "fail",
        seconds,
        f"{len(found or [])} project(s) in {company}",
    ):
        return w.steps

    first, seconds = w.wait(
        lambda: [c for c in items(w.call("GET", "/chats")) if c["origin"] == "tamga"],
        within["speak"],
    )
    w.note(
        "Tamga writes first",
        "pass" if first else "late",
        seconds,
        first[0]["title"] if first else "no chat Tamga began",
    )

    waiting, seconds = w.wait(
        lambda: items(w.call("GET", "/decisions", params={"state": "awaiting_owner"})),
        # Evaluated every few minutes, and held until the sandbox's vault account is open and
        # its payees' timelock has run out.
        within["decide"],
    )
    held = items(w.call("GET", "/decisions", params={"state": "held"}))
    if not w.note(
        "a bill waits, another is held",
        "pass" if waiting and held else "fail",
        seconds,
        f"{len(waiting or [])} waiting, {len(held)} held",
    ):
        return w.steps

    run = w.call("GET", "/payment-runs/current")
    w.note(
        "payment run proposed",
        "pass" if run.status_code == 200 else "missing",
        0,
        f"HTTP {run.status_code}",
    )

    decision = waiting[0]
    started = time.monotonic()
    approved = w.call(
        "PATCH",
        f"/decisions/{decision['id']}",
        json={"action": "approve", "evaluation": decision["evaluation"]},
    )
    if not w.note(
        "approve",
        "pass" if approved.status_code == 200 else "fail",
        time.monotonic() - started,
        f"HTTP {approved.status_code} {approved.text[:120]}",
    ):
        return w.steps

    def settled() -> dict[str, Any] | None:
        for payment in items(w.call("GET", "/payments", params={"decision_id": decision["id"]})):
            if payment["state"] in ("settled", "failed"):
                return payment
        return None

    payment, seconds = w.wait(settled, within["pay"], every=3)
    ok = payment is not None and payment["state"] == "settled" and payment["explorer_url"]
    if not w.note(
        "paid on Arc",
        "pass" if ok else "fail",
        seconds,
        (payment or {}).get("explorer_url")
        or (payment or {}).get("failure")
        or "no payment settled",
    ):
        return w.steps

    chain = w.call("GET", "/decision-chain").json()
    anchor = chain.get("anchor") or {}
    w.note(
        "log holds",
        "pass" if chain.get("broken_at") is None and chain.get("length") else "fail",
        0,
        f"{chain.get('length')} rows, broken at {chain.get('broken_at')}",
    )
    w.note(
        "log anchored on Arc",
        "pass" if anchor.get("on_arc") else "late",
        0,
        anchor.get("explorer_url") or "no anchor yet: anchoring runs on its own schedule",
    )

    money = w.call("GET", "/money/accounts")
    w.note(
        "money view",
        "pass" if money.status_code == 200 else "missing",
        0,
        f"HTTP {money.status_code}",
    )
    return w.steps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="the API's base URL")
    parser.add_argument("--web", required=True, help="the web app's origin, sent as Origin")
    # Seconds each wait allows: the targets a judge should meet, but `decide`, which is how long
    # a sandbox takes today.
    parser.add_argument("--seed-within", type=float, default=120)
    parser.add_argument("--speak-within", type=float, default=120)
    parser.add_argument("--decide-within", type=float, default=600)
    parser.add_argument("--pay-within", type=float, default=90)
    args = parser.parse_args()
    within = {
        "seed": args.seed_within,
        "speak": args.speak_within,
        "decide": args.decide_within,
        "pay": args.pay_within,
    }
    steps = walk(args.api.rstrip("/"), args.web.rstrip("/"), within)
    counts = {kind: sum(step.outcome == kind for step in steps) for kind in OUTCOMES}
    print("\n" + ", ".join(f"{count} {kind}" for kind, count in counts.items()))
    sys.exit(1 if counts["fail"] else 0)


if __name__ == "__main__":
    main()
