import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
    random_bits = secrets.randbits(74)
    value = (timestamp << 80) | (0x7 << 76) | ((random_bits >> 62) << 64)
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)