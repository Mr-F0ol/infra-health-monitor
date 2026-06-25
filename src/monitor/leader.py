"""Redis-backed leader election for safe multi-replica deployment.

The scheduler runs in-process, so two replicas would otherwise double every
check and alert. With HA enabled, every replica competes for a single Redis lock
and only the holder (the *leader*) runs the check jobs; the others stand by and
take over within one TTL if the leader dies. This turns the deployment from
"exactly one instance" into "one active + N warm standbys".

The lock is a single key with a per-instance value and a TTL. Renew and release
are owner-checked via Lua so an instance can never extend or delete a lock that
a peer has already taken over (no split-brain).
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

# Extend the TTL only while we still own the key (compare-and-expire).
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
# Delete only our own lock (compare-and-delete).
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class LeaderLock:
    """A single-holder, TTL'd lock used to elect one active scheduler."""

    def __init__(
        self,
        redis: Redis[bytes],
        key: str = "monitor:leader",
        instance_id: str | None = None,
        ttl: int = 30,
    ) -> None:
        self._redis = redis
        self._key = key
        self._id = instance_id or uuid.uuid4().hex
        self._ttl = max(1, ttl)

    @property
    def instance_id(self) -> str:
        return self._id

    async def acquire_or_renew(self) -> bool:
        """Become (or remain) leader. ``True`` if this instance holds the lock."""
        acquired = await self._redis.set(self._key, self._id, nx=True, ex=self._ttl)
        if acquired:
            return True
        # Lock is held — extend it only if it is ours.
        renewed = await self._redis.eval(  # type: ignore[no-untyped-call]
            _RENEW_LUA, 1, self._key, self._id, str(self._ttl)
        )
        return bool(renewed)

    async def release(self) -> None:
        """Give up leadership if we hold it, so a standby can take over at once."""
        await self._redis.eval(  # type: ignore[no-untyped-call]
            _RELEASE_LUA, 1, self._key, self._id
        )
