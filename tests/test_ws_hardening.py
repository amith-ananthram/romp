#!/usr/bin/env python3
"""Hardening on the WebSocket frame reader, beyond tests/test_ws_fragmentation.py.

That file covers reassembly itself (fragments, pings, strays, the cap). Pinned HERE:

- EOF mid-frame reads as a clean close — a connection that dies inside a frame's length
  extension or payload must return (None, None, True), never crash the reader thread with a
  struct.error mid-read.
- the big-int XOR unmask — the per-byte Python loop takes SECONDS on a multi-MB attachment
  frame, blocking that client's reader thread the whole time; the multi-megabyte round trip
  below runs against the reference per-byte XOR the frame() helper encodes with.
- the reassembly cap actually clears the composer's shipped-attachment ceiling (render.ts
  SHIP_MAX_BYTES base64-inflated), or the client-side cap would lie.
"""
import io
import os
import struct
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wsh", os.path.join(BIN, "romp-kernel"))

MASK = b"\x11\x22\x33\x44"


def frame(payload, opcode, fin=True, mask=MASK):
    """One client (masked) frame, wire-encoded — unmasking done per byte, the reference the
    kernel's big-int fast path must agree with."""
    b0 = (0x80 if fin else 0x00) | opcode
    ln = len(payload)
    if ln < 126:
        hdr = bytes([b0, 0x80 | ln])
    elif ln < 65536:
        hdr = bytes([b0, 0x80 | 126]) + struct.pack(">H", ln)
    else:
        hdr = bytes([b0, 0x80 | 127]) + struct.pack(">Q", ln)
    return hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))


def fragmented(payload, chunk, opcode=0x1):
    """A message split into continuation frames of `chunk` bytes, as a browser would send it."""
    parts = [payload[i:i + chunk] for i in range(0, len(payload), chunk)] or [b""]
    out = b""
    for i, p in enumerate(parts):
        out += frame(p, opcode if i == 0 else 0x0, fin=(i == len(parts) - 1))
    return out


def recv_message(wire):
    pings = []
    return km._ws_recv_message(io.BytesIO(wire), pings.append)


class WsRecvHardening(unittest.TestCase):
    def test_multi_megabyte_payload_round_trips(self):
        # a realistic attachment: ~6 MB of base64-ish bytes in browser-sized (~128 KB) fragments —
        # exercises the big-int unmask fast path against frame()'s reference per-byte XOR
        body = (b'{"type":"dropFile","b64":"' + os.urandom(3 * 1024 * 1024).hex().encode() + b'"}')
        op, payload = recv_message(fragmented(body, chunk=128 * 1024))
        self.assertEqual((op, payload), (0x1, body))

    def test_unmask_agrees_with_the_reference_at_awkward_lengths(self):
        # lengths straddling the 4-byte mask stride, where a repeat-and-slice bug would show
        for ln in (0, 1, 3, 4, 5, 125, 126, 127, 65535, 65536, 65537):
            body = os.urandom(ln)
            op, payload, fin = km._ws_recv(io.BytesIO(frame(body, 0x2)))
            self.assertEqual((op, payload, fin), (0x2, body, True), "length %d" % ln)

    def test_eof_mid_frame_reads_as_dead_connection_not_a_crash(self):
        whole = frame(b'{"type":"ready"}', 0x1)
        for cut in (1, 3, 9, len(whole) - 1):          # header, extension, mask, payload
            self.assertEqual(km._ws_recv(io.BytesIO(whole[:cut])), (None, None, True),
                             "truncated at byte %d" % cut)
        big = frame(b"x" * 70000, 0x1)                 # 2-byte length extension, cut inside it
        self.assertEqual(km._ws_recv(io.BytesIO(big[:3])), (None, None, True))
        huge = frame(b"x" * 70000, 0x1)                # …and the same for a truncated payload
        self.assertEqual(km._ws_recv(io.BytesIO(huge[:2000])), (None, None, True))

    def test_eof_mid_message_reads_as_dead_connection(self):
        wire = fragmented(b'{"type":"ready"}', chunk=4)[:9]   # truncated mid-payload
        self.assertEqual(recv_message(wire), (None, None))

    def test_the_cap_covers_the_composers_shipped_attachment_ceiling(self):
        # render.ts refuses attachments over SHIP_MAX_BYTES (50 MB); base64 inflates by 4/3, so the
        # receiver's ceiling must clear ~67 MB of payload plus JSON framing or the client-side cap lies
        self.assertGreater(km._WS_MAX_MESSAGE, 50 * 1024 * 1024 * 4 // 3 + 4096)
        render = open(os.path.join(ROOT, "ui", "webview", "render.ts"), encoding="utf-8").read()
        self.assertIn("SHIP_MAX_BYTES = 50 * 1024 * 1024", render)


if __name__ == "__main__":
    unittest.main()
