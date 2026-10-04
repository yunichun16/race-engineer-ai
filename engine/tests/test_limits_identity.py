"""Visitors: the client's address as counted, the daily salted code, the tag, the trusted proxy
hops, a missing address, and that no address reaches a response or a log line."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from race_engineer.api.app import create_app
from race_engineer.api.limits.guard import CLIENT_MISSING
from race_engineer.api.limits.identity import (
    UNKNOWN,
    Identity,
    client_ip,
    normalise,
    visitor_id,
    visitor_tag,
)
from race_engineer.api.settings import Settings
from race_engineer.tools.synthetic import sample_comparison_session, write_session

SECRET = b"test-secret"
DAY = "2026-10-04"


def scope(
    client: tuple[str, int] | None = ("198.51.100.1", 5000), **headers: str
) -> dict[str, Any]:
    raw = [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]
    return {"type": "http", "client": client, "headers": raw}


def test_ipv4_ipv6_and_mapped_addresses() -> None:
    assert client_ip(scope(("203.0.113.7", 1))) == "203.0.113.7"
    assert normalise("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert normalise("[2001:db8:1:2::9]") == "2001:db8:1:2::/64"
    assert normalise("::ffff:203.0.113.7") == "203.0.113.7"
    assert normalise("testclient") == "testclient"  # not an address: kept as a name
    # Two addresses in one /64 are one visitor; another /64 is another.
    a = visitor_id(normalise("2001:db8:1:2::1"), SECRET, DAY)
    b = visitor_id(normalise("2001:db8:1:2:ffff::1"), SECRET, DAY)
    c = visitor_id(normalise("2001:db8:1:3::1"), SECRET, DAY)
    assert a == b != c


def test_no_client_address() -> None:
    assert client_ip(scope(None)) is None
    assert client_ip({"type": "http", "headers": []}) is None
    now = datetime(2026, 10, 4, 12, tzinfo=UTC).timestamp()
    on_loopback = Identity(SECRET, loopback=True)
    assert on_loopback.visitor(scope(None), now) == visitor_id(UNKNOWN, SECRET, DAY)
    assert Identity(SECRET, loopback=False).visitor(scope(None), now) is None


def test_hops_zero_ignores_forwarding_headers() -> None:
    spoofed = scope(x_forwarded_for="203.0.113.7", x_real_ip="198.51.100.9")
    assert client_ip(spoofed, hops=0) == "198.51.100.1"


def test_hops_count_from_the_right() -> None:
    forwarded = scope(x_forwarded_for="6.6.6.6, 192.0.2.10, 192.0.2.20")
    assert client_ip(forwarded, hops=1) == "192.0.2.20"  # the proxy's own entry
    assert client_ip(forwarded, hops=2) == "192.0.2.10"
    # A client can prepend anything; only the entries the trusted proxies added count.
    assert client_ip(scope(x_forwarded_for="10.9.9.9, 192.0.2.20"), hops=1) == "192.0.2.20"
    # Fewer entries than hops: the connection's own address.
    assert client_ip(scope(x_forwarded_for="192.0.2.20"), hops=2) == "198.51.100.1"
    # Repeated headers are one list.
    raw = {
        "type": "http",
        "client": ("198.51.100.1", 1),
        "headers": [
            (b"x-forwarded-for", b"6.6.6.6"),
            (b"x-forwarded-for", b"192.0.2.30"),
        ],
    }
    assert client_ip(raw, hops=1) == "192.0.2.30"


def test_the_salt_changes_at_midnight_utc() -> None:
    identity = Identity(SECRET, loopback=False)
    before = datetime(2026, 10, 4, 23, 59, 59, tzinfo=UTC).timestamp()
    after = datetime(2026, 10, 5, 0, 0, 0, tzinfo=UTC).timestamp()
    request = scope(("203.0.113.7", 1))
    assert identity.visitor(request, before) == identity.visitor(request, before - 3600)
    assert identity.visitor(request, before) != identity.visitor(request, after)
    # Another secret, another code.
    assert Identity(b"other", loopback=False).visitor(request, before) != identity.visitor(
        request, before
    )


def test_the_code_and_the_tag() -> None:
    visitor = visitor_id("203.0.113.7", SECRET, DAY)
    assert re.fullmatch(r"[0-9a-f]{32}", visitor)
    assert re.fullmatch(r"[0-9a-f]{6}", visitor_tag(visitor))
    assert visitor.startswith(visitor_tag(visitor))


@pytest.fixture(scope="module")
def processed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("identity")
    (root / "processed").mkdir()
    (root / "results").mkdir()
    write_session(sample_comparison_session(), root / "processed")
    return root / "processed", root / "results"


def public_app(processed: tuple[Path, Path], **changes: Any) -> Any:
    values = {
        "chat": "fake",
        "mount_mcp": False,
        "host": "0.0.0.0",
        "limits": "on",
        "limits_store": "memory",
        "chat_secret_set": True,
    } | changes
    return create_app(Settings(processed=processed[0], results=processed[1], **values))


def test_a_public_server_refuses_a_request_without_an_address(
    processed: tuple[Path, Path],
) -> None:
    app = public_app(processed)
    with TestClient(app) as client:
        # TestClient always names a client, so the guard is shown requests without one.
        guard = app.state.guard
        assert not guard.identity.loopback
        setattr(guard, "identity", _Addressless(guard.identity))  # noqa: B010
        response = client.post("/api/chat", json={"message": "Who won the Sample GP?"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "chat_unavailable"
        assert response.json()["error"]["retry_after_s"] == 60
        health = client.get("/api/health").json()
        assert CLIENT_MISSING in health["limits"]["problems"]
        assert CLIENT_MISSING not in health["problems"]  # limit problems stay out of the data's
        assert client.get("/api/chat/status").json()["visitor"] is None


class _Addressless:
    """An Identity that sees requests as if the host had given no client address."""

    def __init__(self, identity: Identity) -> None:
        self.identity = identity

    def visitor(self, scope: dict[str, Any], now: float) -> str | None:
        return self.identity.visitor({**scope, "client": None}, now)


def test_no_address_reaches_a_response_or_the_log(
    processed: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    address = "203.0.113.77"
    caplog.set_level(logging.DEBUG)
    app = public_app(processed, rate_hour=2)
    seen: list[str] = []
    with TestClient(app, client=(address, 5000)) as client:
        for _ in range(3):
            response = client.post("/api/chat", json={"message": "Who won the Sample GP?"})
            seen.append(response.text)
        seen.append(client.get("/api/chat/status").text)
        seen.append(client.get("/api/health").text)
    status = seen[-2]
    tag = re.search(r'"visitor":"([0-9a-f]{6})"', status)
    assert tag is not None
    for text in [*seen, caplog.text]:
        assert address not in text
    assert "quota=1/24" in caplog.text and "quota=0/23" in caplog.text  # the log line's quota
    visitor_codes = re.findall(r"[0-9a-f]{32}", caplog.text)
    assert not visitor_codes  # nor the visitor's code
