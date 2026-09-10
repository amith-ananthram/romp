"""The kernel answers WebSocket pings (RFC 6455 §5.5.3). Browsers never ping, but websocket
LIBRARIES do by default (e.g. python-websockets pings every 20s and hangs up when no pong arrives
within its timeout) — the kernel's old ping→ignore made it silently unusable from any standard
non-browser client: the link died every ping_interval+ping_timeout with close code 1006."""
import inspect
import io
import os
import threading
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


class WsPong(unittest.TestCase):
    def _pong_bytes(self, payload):
        buf = io.BytesIO()
        km._ws_pong(buf, threading.Lock(), payload)
        return buf.getvalue()

    def test_pong_frame_echoes_the_ping_payload(self):
        out = self._pong_bytes(b"hello")
        self.assertEqual(out[0], 0x8A, "FIN + pong opcode")
        self.assertEqual(out[1], 5, "unmasked length byte (server frames are never masked)")
        self.assertEqual(out[2:], b"hello")

    def test_empty_ping_gets_an_empty_pong(self):
        self.assertEqual(self._pong_bytes(b""), bytes([0x8A, 0x00]))

    def test_oversized_payload_is_clamped_to_the_control_frame_limit(self):
        # control frames carry at most 125 bytes; a longer payload must not overflow into the
        # mask bit of the length byte (0x80) and corrupt the frame
        out = self._pong_bytes(b"x" * 300)
        self.assertEqual(out[1], 125)
        self.assertEqual(len(out), 2 + 125)

    def test_the_ws_handler_answers_pings_instead_of_ignoring_them(self):
        src = inspect.getsource(km.Handler._ws)
        self.assertIn("_ws_pong(self.wfile, lock, payload or b\"\")", src)
        self.assertNotIn("ping → we ignore", src, "the old ignore comment (and behavior) is gone")


if __name__ == "__main__":
    unittest.main()
