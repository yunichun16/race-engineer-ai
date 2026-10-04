"""The limits' store: one contract (`api/limits/contract.py`) run on the memory store and on the
Upstash store over a fake of Upstash's REST API (`fake_upstash.py`), plus the Upstash client's
handling of the API's answers and failures.
"""

from __future__ import annotations

import logging

import httpx2
import pytest
from fake_upstash import TOKEN, URL, FakeUpstash

from race_engineer.api.limits import contract
from race_engineer.api.limits.contract import CASES, POLICY, Clock, run_case
from race_engineer.api.limits.memory import MemoryStore, MiniRedis
from race_engineer.api.limits.store import KEY_PREFIX, CommandStore, StoreError, oldest_ms
from race_engineer.api.limits.upstash import UpstashClient, UpstashStore

pytestmark = pytest.mark.anyio


def memory(clock: Clock, name: str) -> CommandStore:
    return MemoryStore(clock=clock)


def upstash(clock: Clock, name: str) -> CommandStore:
    return FakeUpstash(clock).store(clock)


@pytest.mark.parametrize("factory", [memory, upstash], ids=["memory", "upstash"])
@pytest.mark.parametrize("case", CASES, ids=[c.__name__ for c in CASES])
async def test_the_contract(case: contract.Case, factory: contract.Factory) -> None:
    await run_case(case, factory)


async def test_an_admission_is_one_transaction_of_thirteen_commands() -> None:
    fake = FakeUpstash(Clock())
    store = fake.store()
    admission = await store.admit("v" * 32, contract.BASE, POLICY)
    assert admission.admitted and fake.calls == [("/multi-exec", 13)]
    await store.settle(admission, 1_234, refund_quota=False)
    assert fake.calls[-1] == ("/multi-exec", 4)
    await store.settle(admission, 0, refund_quota=True)  # (a second settle is the guard's job)
    assert fake.calls[-1] == ("/multi-exec", 7)
    await store.status("v" * 32, contract.BASE, POLICY)
    assert fake.calls[-1] == ("/pipeline", 3)  # the page's status read
    await store.status(None, contract.BASE, POLICY)
    assert fake.calls[-1] == ("/pipeline", 1)  # the health check's MGET
    await store.aclose()


async def test_keys_and_their_expiries() -> None:
    clock = Clock(contract.BASE)
    fake = FakeUpstash(clock)
    store = fake.store()
    await store.admit("abc", clock(), POLICY)
    keys = sorted(fake.redis.values)
    assert keys == [
        f"{KEY_PREFIX}d:2031-03-04:abc",
        f"{KEY_PREFIX}h:abc",
        f"{KEY_PREFIX}q:2031-03-04",
        f"{KEY_PREFIX}reserved:2031-03-04",
    ]
    assert fake.ttl(f"{KEY_PREFIX}h:abc") == 3600
    assert fake.ttl(f"{KEY_PREFIX}q:2031-03-04") == 48 * 3600
    # The visitor's day count lives until an hour after the UTC day ends: 12 h + 1 h from noon.
    assert fake.ttl(f"{KEY_PREFIX}d:2031-03-04:abc") == 13 * 3600
    await store.aclose()


async def test_a_discarded_transaction_is_a_store_error() -> None:
    fake = FakeUpstash(Clock())
    client = UpstashClient(URL, TOKEN, transport=fake.transport())
    with pytest.raises(StoreError, match=r"refused the request \(400\): ERR unknown command"):
        await client.multi_exec([["GET", "a"], ["FLUSHALL"]])
    assert fake.calls == []  # nothing ran
    await client.aclose()


async def test_a_failed_command_is_a_store_error() -> None:
    fake = FakeUpstash(Clock())
    client = UpstashClient(URL, TOKEN, transport=fake.transport())
    await client.multi_exec([["SET", "k", "text"]])
    with pytest.raises(StoreError, match="refused a command: ERR value is not an integer"):
        await client.multi_exec([["INCR", "k"]])
    await client.aclose()


async def test_errors_never_carry_the_url_or_the_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)  # httpx2 logs each request's URL at INFO: filtered out
    fake = FakeUpstash(Clock())
    wrong = UpstashClient(URL, "not-the-token", transport=fake.transport())
    with pytest.raises(StoreError) as refused:
        await wrong.pipeline([["GET", "k"]])
    assert "401" in str(refused.value) and "WRONGPASS" in str(refused.value)
    fake.down = True
    with pytest.raises(StoreError) as down:
        await wrong.pipeline([["GET", "k"]])
    assert str(down.value) == "Upstash couldn't be reached (ConnectError)"
    for text in (str(refused.value), str(down.value), caplog.text):
        assert URL not in text and "fake-upstash" not in text and "not-the-token" not in text
    await wrong.aclose()


async def test_no_retries_and_a_timeout() -> None:
    attempts = 0

    def refuse(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        raise httpx2.ConnectError("refused", request=request)

    client = UpstashClient(URL, TOKEN, transport=httpx2.MockTransport(refuse))
    with pytest.raises(StoreError):
        await client.multi_exec([["INCR", "q"]])
    assert attempts == 1  # a lost answer may have run: never sent twice
    await client.aclose()

    fake = FakeUpstash(Clock())
    fake.hang = True
    slow = UpstashClient(URL, TOKEN, transport=fake.transport())
    with pytest.raises(StoreError, match="didn't answer in time"):
        await slow.multi_exec([["INCR", "q"]])
    await slow.aclose()


async def test_unexpected_bodies_are_store_errors() -> None:
    answers = iter(
        [
            httpx2.Response(200, json={"result": 1}),  # not an array
            httpx2.Response(200, json=[{"result": 1}]),  # one reply for two commands
            httpx2.Response(502, text="Bad gateway"),
            httpx2.Response(500, json=[{"result": 1}, {"result": 2}]),
        ]
    )
    client = UpstashClient(URL, TOKEN, transport=httpx2.MockTransport(lambda r: next(answers)))
    for _ in range(4):
        with pytest.raises(StoreError):
            await client.multi_exec([["GET", "a"], ["GET", "b"]])
    await client.aclose()


async def test_a_single_command() -> None:
    fake = FakeUpstash(Clock())
    client = UpstashClient(URL, TOKEN, transport=fake.transport())
    assert await client.command("SET", "k", "1") == "OK"
    assert await client.command("INCRBY", "k", 4) == 5
    assert await client.command("GET", "missing") is None
    await client.aclose()


def test_scores_come_back_as_strings_and_parse_either_way() -> None:
    redis = MiniRedis()
    redis.run([["ZADD", "h", 1796385600000, "1796385600000:ab"]], 0)
    (flat,) = redis.run([["ZRANGE", "h", 0, 0, "WITHSCORES"]], 0)
    assert flat == ["1796385600000:ab", "1796385600000"]  # RESP2, as Upstash's REST API sends
    assert oldest_ms(flat) == 1796385600000
    assert oldest_ms([["1796385600000:ab", 1796385600000.0]]) == 1796385600000  # RESP3 pairs
    assert oldest_ms(["1796385600000:ab"]) == 1796385600000  # no score: the member's prefix
    assert oldest_ms([]) is None


async def test_the_memory_store_reports_a_bad_command() -> None:
    store = MemoryStore(clock=Clock())
    with pytest.raises(StoreError, match="unknown command"):
        await store.run([["FLUSHALL"]], atomic=True)


async def test_upstash_store_from_credentials(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeUpstash(Clock())
    store = UpstashStore.from_credentials(URL, TOKEN, transport=fake.transport())
    await store.ping()
    await store.set_paused(True)
    assert (await store.status(None, contract.BASE, POLICY)).paused
    await store.aclose()
    assert TOKEN not in caplog.text
