"""Port of CrEncryptedSerialize from the games' Global.dll.

On-disk .db layout: 4-byte little-endian length, then the encoded payload.
Encoding = pseudo-random block swaps (MSVC rand seeded with the byte sum),
followed by adding (i * 0xB3) & 0xFF to every byte.

ICC 2000/2002 seed rand() with the byte sum; ICC 2006 seeds it with the byte
sum plus the payload length (CrEncryptedSerialize::StartSession/EndSession).
"""
import struct


class _MsvcRand:
    def __init__(self, seed):
        self.state = seed & 0xFFFFFFFF

    def __call__(self):
        self.state = (self.state * 214013 + 2531011) & 0xFFFFFFFF
        return (self.state >> 16) & 0x7FFF


def _swaps(buf, seed_with_length=False):
    """Yield the (a, b, length) block swaps Encode performs, in order."""
    n = len(buf)
    rand = _MsvcRand(sum(buf) + (n if seed_with_length else 0))
    count = rand() % 1000 + 500
    max_len = n // 10
    for _ in range(count):
        length = rand() % max_len
        a = rand() % n - length
        b = rand() % n - length
        if a < 0 or b < 0 or length <= 0:
            continue
        # Same overlap rejection as the original (inclusive bounds).
        if b <= a <= b + length:
            continue
        if b <= a + length <= b + length:
            continue
        if a <= b <= a + length:
            continue
        if a <= b + length <= a + length:
            continue
        yield a, b, length


def _swap(buf, a, b, length):
    buf[a:a + length], buf[b:b + length] = buf[b:b + length], buf[a:a + length]


def encode(data, seed_with_length=False):
    buf = bytearray(data)
    if len(buf) < 1000:
        return bytes(buf)
    for a, b, length in list(_swaps(buf, seed_with_length)):
        _swap(buf, a, b, length)
    for i in range(len(buf)):
        buf[i] = (buf[i] + i * 0xB3) & 0xFF
    return bytes(buf)


def decode(data, seed_with_length=False):
    buf = bytearray(data)
    if len(buf) < 1000:
        return bytes(buf)
    for i in range(len(buf)):
        buf[i] = (buf[i] + i * 0x4D) & 0xFF
    for a, b, length in reversed(list(_swaps(buf, seed_with_length))):
        _swap(buf, a, b, length)
    return bytes(buf)


def read_db(path, seed_with_length=False):
    """Read an encrypted .db file and return the decoded payload."""
    with open(path, 'rb') as f:
        raw = f.read()
    (size,) = struct.unpack_from('<I', raw)
    return decode(raw[4:4 + size], seed_with_length)


def write_db(path, payload, seed_with_length=False):
    """Encode a payload and write it as an encrypted .db file."""
    with open(path, 'wb') as f:
        f.write(struct.pack('<I', len(payload)))
        f.write(encode(payload, seed_with_length))
