"""Transport framing tests. Pure, no network.

The wire format is a length-prefixed frame: a 4-byte big-endian unsigned
length header followed by that many bytes of msgpack-free JSON payload.
Agents speak a machine-native request/response protocol on top of it.
"""
import io
import socket
import threading

import pytest

from anet.transport import (
    FrameError,
    read_frame,
    write_frame,
    send_message,
    recv_message,
    MAX_FRAME,
)


class Pipe:
    """A bidirectional in-memory stream pair with a socket-like recv/sendall."""

    def __init__(self):
        self._buf = bytearray()
        self._lock = threading.Lock()

    def sendall(self, data):
        with self._lock:
            self._buf.extend(data)

    def recv(self, n):
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        return chunk


def test_write_then_read_roundtrip():
    p = Pipe()
    write_frame(p, b"hello agents")
    assert read_frame(p) == b"hello agents"


def test_empty_frame_roundtrip():
    p = Pipe()
    write_frame(p, b"")
    assert read_frame(p) == b""


def test_multiple_frames_in_order():
    p = Pipe()
    write_frame(p, b"one")
    write_frame(p, b"two")
    write_frame(p, b"three")
    assert read_frame(p) == b"one"
    assert read_frame(p) == b"two"
    assert read_frame(p) == b"three"


def test_read_handles_partial_recv():
    """recv may return fewer bytes than requested; read_frame must loop."""

    class DribblePipe:
        def __init__(self, data):
            self._data = data

        def recv(self, n):
            # Hand back a single byte at a time to stress the read loop.
            if not self._data:
                return b""
            b, self._data = self._data[:1], self._data[1:]
            return b

    real = Pipe()
    write_frame(real, b"streamed payload")
    wire = bytes(real._buf)
    dribble = DribblePipe(wire)
    assert read_frame(dribble) == b"streamed payload"


def test_truncated_header_raises():
    class ClosedPipe:
        def recv(self, n):
            return b""  # peer closed immediately

    with pytest.raises(FrameError):
        read_frame(ClosedPipe())


def test_truncated_body_raises():
    class HalfPipe:
        def __init__(self):
            # A valid 10-byte length header, then EOF before the body.
            self._data = (10).to_bytes(4, "big")

        def recv(self, n):
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    with pytest.raises(FrameError):
        read_frame(HalfPipe())


def test_oversize_frame_rejected_on_write():
    p = Pipe()
    with pytest.raises(FrameError):
        write_frame(p, b"x" * (MAX_FRAME + 1))


def test_oversize_frame_rejected_on_read():
    class BigHeaderPipe:
        def __init__(self):
            self._data = (MAX_FRAME + 1).to_bytes(4, "big")

        def recv(self, n):
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    with pytest.raises(FrameError):
        read_frame(BigHeaderPipe())


def test_message_roundtrip_json():
    p = Pipe()
    msg = {"op": "call", "tool": "reverse", "args": {"s": "abc"}}
    send_message(p, msg)
    assert recv_message(p) == msg


def test_message_unicode():
    p = Pipe()
    msg = {"note": "anonymous corner ✨ 你好"}
    send_message(p, msg)
    assert recv_message(p) == msg


def test_over_real_socketpair():
    a, b = socket.socketpair()
    try:
        send_message(a, {"ping": 1})
        assert recv_message(b) == {"ping": 1}
        send_message(b, {"pong": 2})
        assert recv_message(a) == {"pong": 2}
    finally:
        a.close()
        b.close()
