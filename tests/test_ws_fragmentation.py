"""Fragmented client WebSocket messages reassemble into ONE message (RFC 6455 §5.4).

Browsers fragment large sends — Chrome splits at ~128 KB — and the kernel's old loop handed each
FRAME to json.loads: a phone photo's dropFile (a multi-MB base64 message) dissolved into one
unparseable JSON prefix plus a train of silently dropped continuation frames, so the chat's 📎 pick
looked like it did nothing (2026-08-10, Chrome on a phone; small desktop files sit under the
threshold, which is why it never surfaced). _ws_recv now reports the FIN bit and _ws_recv_message
reassembles, answering interleaved pings and refusing unbounded buffering.
"""
import io
import os
import struct
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

MASK = b"\x11\x22\x33\x44"


def frame(op, payload, fin=True):
    """One masked client frame, as a browser would send it."""
    b0 = (0x80 if fin else 0x00) | op
    n = len(payload)
    if n < 126:
        hdr = bytes([b0, 0x80 | n])
    elif n < 65536:
        hdr = bytes([b0, 0x80 | 126]) + struct.pack(">H", n)
    else:
        hdr = bytes([b0, 0x80 | 127]) + struct.pack(">Q", n)
    return hdr + MASK + bytes(c ^ MASK[i % 4] for i, c in enumerate(payload))


def read_message(raw, pings=None):
    on_ping = (pings.append if pings is not None else lambda p: None)
    return km._ws_recv_message(io.BytesIO(raw), on_ping)


class WsFragmentation(unittest.TestCase):
    def test_a_whole_message_in_one_frame_still_passes(self):
        op, payload = read_message(frame(0x1, b'{"type":"ready"}'))
        self.assertEqual((op, payload), (0x1, b'{"type":"ready"}'))

    def test_fragments_reassemble_into_one_message(self):
        # the Chrome shape: text frame with FIN clear, continuations, FIN on the last
        body = b'{"type":"dropFile","b64":"' + b"A" * 300 + b'"}'
        raw = (frame(0x1, body[:100], fin=False)
               + frame(0x0, body[100:200], fin=False)
               + frame(0x0, body[200:], fin=True))
        op, payload = read_message(raw)
        self.assertEqual(op, 0x1, "the assembled message keeps the OPENING frame's opcode")
        self.assertEqual(payload, body)

    def test_a_ping_between_fragments_is_answered_and_does_not_corrupt(self):
        body = b"hello world, in three parts"
        pings = []
        raw = (frame(0x1, body[:5], fin=False)
               + frame(0x9, b"ka")                       # interleaved control frame (spec-legal)
               + frame(0x0, body[5:16], fin=False)
               + frame(0x0, body[16:], fin=True))
        op, payload = read_message(raw, pings)
        self.assertEqual(payload, body)
        self.assertEqual(pings, [b"ka"], "the ping was answered mid-message")

    def test_a_stray_continuation_is_dropped_and_the_next_message_parses(self):
        raw = frame(0x0, b"orphan", fin=True) + frame(0x1, b'{"type":"ready"}')
        op, payload = read_message(raw)
        self.assertEqual((op, payload), (0x1, b'{"type":"ready"}'))

    def test_close_and_eof_end_the_read(self):
        self.assertEqual(read_message(frame(0x8, b"")), (None, None))
        self.assertEqual(read_message(b""), (None, None))
        # EOF mid-message (the socket died between fragments) ends it too, not a hang/garbage return
        self.assertEqual(read_message(frame(0x1, b"half", fin=False)), (None, None))

    def test_reassembly_is_capped_not_unbounded(self):
        saved = km._WS_MAX_MESSAGE
        km._WS_MAX_MESSAGE = 64
        try:
            raw = frame(0x1, b"x" * 50, fin=False) + frame(0x0, b"y" * 50, fin=False) \
                + frame(0x0, b"z" * 50, fin=True)
            self.assertEqual(read_message(raw), (None, None),
                             "a client streaming past the cap ends the read instead of buffering forever")
        finally:
            km._WS_MAX_MESSAGE = saved

    def test_the_handler_loop_reads_messages_not_frames(self):
        import inspect
        src = inspect.getsource(km.Handler._ws)
        self.assertIn("_ws_recv_message(", src, "the loop must consume ASSEMBLED messages")
        self.assertIn('_ws_pong(self.wfile, lock, payload or b"")', src, "pings still answered")


if __name__ == "__main__":
    unittest.main()
