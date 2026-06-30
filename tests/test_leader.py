"""Tests for Redis-backed leader election."""

from __future__ import annotations

from unittest.mock import AsyncMock

from monitor.leader import LeaderLock


def _lock(set_result: object, eval_result: object = 0) -> tuple[LeaderLock, AsyncMock]:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=set_result)
    redis.eval = AsyncMock(return_value=eval_result)
    return LeaderLock(redis, instance_id="me", ttl=10), redis


async def test_acquires_lock_when_free() -> None:
    lock, redis = _lock(set_result=True)

    assert await lock.acquire_or_renew() is True
    redis.set.assert_awaited_once_with("monitor:leader", "me", nx=True, ex=10)
    redis.eval.assert_not_awaited()  # no renew needed — we just took it


async def test_renews_lock_we_already_hold() -> None:
    lock, redis = _lock(set_result=None, eval_result=1)

    assert await lock.acquire_or_renew() is True
    # NX failed (someone holds it); the owner-checked renew succeeded.
    redis.eval.assert_awaited_once()
    assert redis.eval.call_args.args[2:] == ("monitor:leader", "me", "10")


async def test_not_leader_when_peer_holds_lock() -> None:
    lock, redis = _lock(set_result=None, eval_result=0)

    assert await lock.acquire_or_renew() is False


async def test_release_deletes_only_own_lock() -> None:
    lock, redis = _lock(set_result=True)

    await lock.release()
    redis.eval.assert_awaited_once()
    assert redis.eval.call_args.args[2:] == ("monitor:leader", "me")


def test_instance_id_is_stable() -> None:
    lock, _ = _lock(set_result=True)
    assert lock.instance_id == "me"
