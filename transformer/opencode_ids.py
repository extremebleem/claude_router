"""
Session and message ids in opencode's own shape.

Zen's anonymous free tier refuses any request whose ``x-opencode-session`` it does not recognise with
``403 FreeTierError``, and it recognises nothing but an id built the way the opencode client builds
one. Ported from ``packages/schema/src/identifier.ts``:

    const length = 26
    const chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    const current = BigInt(Date.now()) * 0x1000n + BigInt(counter)
    const value = descending ? ~current : current
    // 6 bytes of the low 48 bits, hex, then `length - 12` random characters

So an id is 12 hex characters followed by 14 characters of that alphabet - no signature, and nothing
that has to be kept in sync with the gateway beyond the format itself. Sessions are minted descending
and messages ascending, which is why a real session id ends in ``ffe`` and a message id in ``001``.

The gateway does not look at how old the embedded timestamp is (verified 2026-10-14: a day-old stamp is
accepted), so an id is stamped once per conversation and then reused. That keeps ``x-opencode-session``
and ``x-session-affinity`` stable across the turns of one Claude Code conversation, which is what the
prompt cache keys on.
"""

import hashlib
import os
import threading
import time
from typing import Dict, Optional

ID_LENGTH = 26
ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
TIME_CHARS = 12
RANDOM_CHARS = ID_LENGTH - TIME_CHARS  # 14
COUNTER_SLOTS = 0x1000  # the counter is packed into the low 12 bits

# One entry per conversation. The bound keeps a long-lived proxy from growing the map without limit;
# dropping an entry costs one re-stamp, which changes that conversation's id once.
_MAX_STAMPS = 512
_stamps: Dict[str, int] = {}
_stamp_lock = threading.Lock()


def opencode_identifier(
    descending: bool = True,
    counter: int = 1,
    timestamp_ms: Optional[int] = None,
    random_bytes: Optional[bytes] = None,
) -> str:
    """One identifier, built exactly as the opencode client builds it."""
    now = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    value = now * COUNTER_SLOTS + (counter % COUNTER_SLOTS)
    if descending:
        # ~value on an unbounded int is -value - 1; the sign extension of the arithmetic shift that
        # follows is irrelevant because every byte is taken with a mask.
        value = ~value
    stamp = "".join(f"{(value >> (40 - 8 * index)) & 0xFF:02x}" for index in range(TIME_CHARS // 2))
    raw = os.urandom(RANDOM_CHARS) if random_bytes is None else random_bytes
    return stamp + "".join(ID_ALPHABET[byte % len(ID_ALPHABET)] for byte in raw[:RANDOM_CHARS])


def _stamp(key: str) -> int:
    """Wall-clock time assigned to a conversation the first time it asks for an id.

    Eviction drops the oldest entry rather than emptying the map: a long-lived proxy talks to many
    conversations, and wiping the live ones would re-stamp them mid-conversation, which changes the id
    under a request that is already being built.
    """
    now = int(time.time() * 1000)
    with _stamp_lock:
        known = _stamps.get(key)
        if known is not None:
            return known
        while len(_stamps) >= _MAX_STAMPS:
            del _stamps[next(iter(_stamps))]
        _stamps[key] = now
        return now


def _tail(seed: str) -> bytes:
    """Deterministic stand-in for the random tail, so one conversation keeps one id."""
    return hashlib.sha256(seed.encode("utf-8")).digest()[:RANDOM_CHARS]


def session_identifier(seed: str) -> str:
    """`ses_` + an id the way opencode mints sessions (descending), stamped once per conversation."""
    return "ses_" + opencode_identifier(descending=True, counter=1,
                                        timestamp_ms=_stamp(f"ses:{seed}"),
                                        random_bytes=_tail(f"ses:{seed}"))


def message_identifier(seed: str) -> str:
    """`msg_` + an id the way opencode mints messages (ascending).

    One per turn, so it is stamped with the current time instead of being remembered: keeping those
    would push live conversations out of the stamp cache.
    """
    return "msg_" + opencode_identifier(descending=False, counter=1,
                                        random_bytes=_tail(f"msg:{seed}"))