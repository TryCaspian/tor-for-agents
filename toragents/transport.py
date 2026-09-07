"""Length-prefixed framing and JSON message helpers.

Wire format: a 4-byte big-endian unsigned length header, then that many
bytes of UTF-8 JSON. This is the machine-native surface agents speak over
a Tor stream. It is deliberately dumb and has no network dependency so it
can be tested in isolation.

A "stream" here is any object with ``sendall(bytes)`` and ``recv(n) -> bytes``,
which covers both real sockets and the in-memory pipes used in tests.
"""
import json

HEADER = 4  # bytes
MAX_FRAME = 16 * 1024 * 1024  # 16 MiB ceiling, guards against a hostile header


class FrameError(Exception):
    """Raised on a malformed, truncated, or oversize frame."""


def write_frame(stream, payload: bytes) -> None:
    if len(payload) > MAX_FRAME:
        raise FrameError(f"frame of {len(payload)} bytes exceeds {MAX_FRAME}")
    stream.sendall(len(payload).to_bytes(HEADER, "big") + payload)


def _recv_exact(stream, n: int) -> bytes:
    """Read exactly n bytes or raise. Loops because recv may return short."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.recv(n - len(buf))
        if not chunk:
            raise FrameError(
                f"stream closed after {len(buf)} of {n} expected bytes"
            )
        buf.extend(chunk)
    return bytes(buf)


def read_frame(stream) -> bytes:
    header = _recv_exact(stream, HEADER)
    length = int.from_bytes(header, "big")
    if length > MAX_FRAME:
        raise FrameError(f"declared frame of {length} bytes exceeds {MAX_FRAME}")
    return _recv_exact(stream, length)


def send_message(stream, message) -> None:
    """Serialize a JSON-compatible object and write it as one frame."""
    write_frame(stream, json.dumps(message, separators=(",", ":")).encode("utf-8"))


def recv_message(stream):
    """Read one frame and decode it as JSON."""
    raw = read_frame(stream)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"payload is not valid JSON: {exc}") from exc
