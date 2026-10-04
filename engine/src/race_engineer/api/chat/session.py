"""Conversations held by the browser: signing, checking and counting them.

The server stores no transcripts. After each question it sends the browser the whole history
(every message as the API saw it, thinking blocks unchanged) with an HMAC signature, and the
browser sends both back with the next question. Checking the signature blocks forged tool
results and edited turns, and keeps the history append-only, which preserved thinking needs.

A signature is "<prompt version>.<hex HMAC-SHA256 of the version and the canonical history>".
Carrying the version lets a history from before a prompt or tool change be told apart from a
forged one: the first is "start a new conversation" (409), the second a bad request (400).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from typing import Any

MAX_HISTORY_BYTES = 512 * 1024  # canonical JSON; about 8 questions with tool calls fit easily
# Messages a history may hold: 8 questions of up to 6 API calls, each call adding an assistant
# turn and a tool-result turn, plus the questions themselves (8 * 13 = 104), rounded up.
MAX_HISTORY_MESSAGES = 128


class ChatRejected(Exception):
    """A chat request refused before any answer is streamed: an HTTP status, an error code and
    a message for the user, sent as the API's plain JSON error body. The limits' refusals
    (`api/limits/guard.py`) add `retry_after_s` (also sent as Retry-After) and `extra` fields
    for the body, such as `limit`."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after_s: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s
        self.extra = extra


def _normalise(value: Any) -> Any:
    """`value` with whole-number floats as ints: the browser's JSON turns 2.0 into 2, and the
    signature must survive that round trip."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def canonical(history: Sequence[Any]) -> bytes:
    """The history as signed: sorted keys, no spaces, UTF-8."""
    text = json.dumps(
        _normalise(list(history)), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return text.encode()


def _digest(history_bytes: bytes, secret: bytes, version: str) -> str:
    return hmac.new(secret, version.encode() + b"." + history_bytes, hashlib.sha256).hexdigest()


def sign(history: Sequence[Any], secret: bytes, version: str) -> str:
    """The signature the browser sends back with `history`."""
    return f"{version}.{_digest(canonical(history), secret, version)}"


def verify(history: Sequence[Any], signature: str | None, secret: bytes, version: str) -> None:
    """Raise ChatRejected unless `signature` is this server's for `history` under `version`.
    An empty history (a new conversation) needs no signature."""
    if history:
        _verify(canonical(history), signature, secret, version)


def _verify(history_bytes: bytes, signature: str | None, secret: bytes, version: str) -> None:
    signed_version, _, digest = (signature or "").partition(".")
    if not signed_version or not digest:
        raise ChatRejected(
            400, "invalid_history", "The conversation history is unsigned; start a new one."
        )
    expected = _digest(history_bytes, secret, signed_version)
    if not hmac.compare_digest(expected, digest):
        raise ChatRejected(
            400,
            "invalid_history",
            "The conversation history doesn't match its signature; start a new conversation.",
        )
    if signed_version != version:
        raise ChatRejected(
            409,
            "conversation_expired",
            "The chat has been updated since this conversation began; start a new conversation.",
        )


def is_question(message: Any) -> bool:
    """Whether a message is a user's question: a user turn holding text, not only tool
    results."""
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "text" for block in content
    )


def count_questions(history: Sequence[Any]) -> int:
    """Questions asked so far in a conversation."""
    return sum(1 for message in history if is_question(message))


def check_history(
    history: Sequence[Any],
    signature: str | None,
    *,
    secret: bytes,
    version: str,
    max_turns: int,
) -> int:
    """Everything checked before a question is answered, in order: size (413), signature (400),
    prompt version (409) and the question cap (409). Returns the questions already asked."""
    history_bytes = canonical(history)
    if len(history_bytes) > MAX_HISTORY_BYTES:
        raise ChatRejected(
            413,
            "history_too_large",
            f"The conversation is larger than {MAX_HISTORY_BYTES // 1024} KB; start a new one.",
        )
    if history:
        _verify(history_bytes, signature, secret, version)
    asked = count_questions(history)
    if asked >= max_turns:
        raise ChatRejected(
            409,
            "turn_limit",
            f"This conversation has reached its limit of {max_turns} questions; start a new one.",
        )
    return asked
