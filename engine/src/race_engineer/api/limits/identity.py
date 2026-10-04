"""Who is asking: a visitor code made from the client's address, with no address kept.

    visitor = HMAC-SHA256(salt_day, ip)[:32 hex]
    salt_day = HMAC-SHA256(RACE_ENGINEER_CHAT_SECRET, "re-limits-v1:" + UTC day)

`ip` is the IPv4 address, or the /64 network of an IPv6 address (one home can rotate through a
/64), an IPv4-mapped IPv6 address counting as its IPv4 address. The salt changes at 00:00 UTC, so
a code says nothing about the same connection on another day, and without the secret it can't be
turned back into an address. Neither the address nor the salt is stored or logged.

The client's address is the ASGI scope's (`scope["client"]`), which the host fills from the
connection (Modal does; a client-sent X-Forwarded-For header is ignored). Behind proxies that
append to X-Forwarded-For (Cloud Run), RACE_ENGINEER_TRUSTED_PROXY_HOPS says how many to trust:
the address is that many entries from the right, because the left end is whatever the client
sent.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from race_engineer.api.limits.policy import utc_day

UNKNOWN = "unknown"  # the shared visitor for requests without an address, on loopback only
SALT_LABEL = b"re-limits-v1:"
TAG_LENGTH = 6


def _forwarded(scope: Mapping[str, Any]) -> list[str]:
    """Every X-Forwarded-For entry, in order, across repeated headers."""
    entries: list[str] = []
    for name, value in scope.get("headers") or ():
        if name.lower() == b"x-forwarded-for":
            entries += [e.strip() for e in value.decode("latin-1").split(",") if e.strip()]
    return entries


def normalise(address: str) -> str:
    """An address as it is counted: IPv4 as it is, IPv6 as its /64 network, an IPv4-mapped IPv6
    address as the IPv4 address. Anything that isn't an address (a test client's name) is kept,
    lower-cased."""
    text = address.strip().strip("[]")
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return text.lower()[:100]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(scope: Mapping[str, Any], hops: int = 0) -> str | None:
    """The client's address as counted (`normalise`), or None when the request has none.

    hops 0: the connection's own address. hops N: the Nth X-Forwarded-For entry from the right,
    falling back to the connection's address when there are fewer entries."""
    if hops > 0:
        entries = _forwarded(scope)
        if len(entries) >= hops:
            return normalise(entries[-hops])
    client = scope.get("client")
    if not client or not client[0]:
        return None
    return normalise(str(client[0]))


def daily_salt(secret: bytes, day: str) -> bytes:
    return hmac.new(secret, SALT_LABEL + day.encode(), hashlib.sha256).digest()


def visitor_id(ip: str, secret: bytes, day: str) -> str:
    """The visitor's code for `day`: 32 hex characters."""
    return hmac.new(daily_salt(secret, day), ip.encode(), hashlib.sha256).hexdigest()[:32]


def visitor_tag(visitor: str) -> str:
    """The first 6 hex characters of a visitor's code, shown to that visitor only (by
    `GET /api/chat/status`), so a deploy check can tell two connections apart."""
    return visitor[:TAG_LENGTH]


@dataclass(frozen=True)
class Identity:
    """How this server names visitors: the secret, the trusted proxy hops and whether it is
    bound to loopback (where a request without an address counts as "unknown")."""

    secret: bytes = field(repr=False)
    hops: int = 0
    loopback: bool = True

    def visitor(self, scope: Mapping[str, Any], now: float) -> str | None:
        """The visitor's code, or None when a public server sees no client address."""
        ip = client_ip(scope, self.hops)
        if ip is None:
            if not self.loopback:
                return None
            ip = UNKNOWN
        return visitor_id(ip, self.secret, utc_day(now))
