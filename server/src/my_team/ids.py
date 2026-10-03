import os
import re

from my_team.clock import now_ms

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_id() -> str:
    value = (now_ms() << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(value >> (5 * i)) & 31] for i in reversed(range(26)))
