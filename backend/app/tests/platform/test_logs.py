import logging

import httpx
import pytest

from app.platform import logs


@pytest.fixture(autouse=True)
def restore_levels() -> object:
    names = ("httpx", "httpcore")
    before = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


def test_a_requests_url_and_the_token_in_it_stay_out_of_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logs.configure("INFO")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"result": "0x"}))

    with httpx.Client(transport=transport) as client:
        client.post("https://rpc.example/v1/secret-token", json={})

    assert "secret-token" not in caplog.text


def test_their_warnings_still_show(caplog: pytest.LogCaptureFixture) -> None:
    logs.configure("INFO")

    logging.getLogger("httpx").warning("the pool is full")

    assert "the pool is full" in caplog.text
