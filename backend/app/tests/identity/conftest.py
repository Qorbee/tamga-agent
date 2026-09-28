from collections.abc import Iterator

import pytest

from app.modules.identity import service


@pytest.fixture(autouse=True)
def fresh_rate_limits() -> Iterator[None]:
    # Every test client is 127.0.0.1, so one test's attempts would count against the next.
    service._password_limiter.cache_clear()
    service._guest_limiter.cache_clear()
    yield
