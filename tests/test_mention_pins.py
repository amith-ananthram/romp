#!/usr/bin/env python3
"""A chat message's embedded image keeps its MENTION-TIME bytes — a later overwrite of the same
filename cannot rewrite history (the user 2026-08-16: an agent re-generating a plot under the same
name silently changed the picture inside every older message that had embedded it).

The mechanism: when a message's path token first RESOLVES (the pathLinks latch — the closest
observable moment to the mention), the kernel snapshots the image's bytes content-addressed into a
bounded mention-pins store and latches the pin with the link. The chat event ships pathPins as a
SIBLING map (pathLinks values must stay strings for older clients), the embed requests
/file?...&pin=<id>, and the federation relay forwards the query untouched so remote sessions pin the
same way. A pin whose blob was evicted falls back to the live file — today's behavior, a nicety
lost, never an error. A NEW message mentioning the regenerated file pins the NEW bytes, so the
agent's naming freedom is untouched. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

TOKEN = os.environ["ROMP_SERVE_TOKEN"]
SID = "11111111-2222-3333-4444-cccccccccccc"

PNG_V1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082")
PNG_V2 = PNG_V1 + b"\x00\x01\x02\x03"               # different bytes — a regenerated plot


class MentionPins(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cwd = Path(self.td.name)
        self.plot = self.cwd / "plot.png"
        self.plot.write_bytes(PNG_V1)
        km._PATH_LINK_CACHE.clear()
        km._SPACE_PATH_CACHE.clear()
        self._saved_cwd_of = km._cwd_of
        km._cwd_of = lambda sid: str(self.cwd)

    def tearDown(self):
        km._cwd_of = self._saved_cwd_of
        self.td.cleanup()

    def _links(self, md, uuid):
        return km._path_links(md, SID, uuid, {})

    def test_the_resolve_latch_pins_the_bytes_and_an_overwrite_cannot_move_them(self):
        self.assertEqual(self._links("see plot.png", "u1"), {"plot.png": "plot.png"})
        pins = km._path_pins(SID, "u1")
        pid = pins.get("plot.png")
        self.assertTrue(pid, "an image mention pins at the resolve moment")
        blob = (km._pin_dir() / pid).read_bytes()
        self.assertEqual(blob, PNG_V1, "the pin holds the mention-time bytes")
        self.plot.write_bytes(PNG_V2)                       # the agent regenerates the plot
        self._links("see plot.png", "u1")                   # a later rebuild of the same message
        self.assertEqual(km._path_pins(SID, "u1").get("plot.png"), pid,
                         "the pin is latched with the link — history cannot move")
        self.assertEqual((km._pin_dir() / pid).read_bytes(), PNG_V1)

    def test_a_new_message_mentioning_the_regenerated_file_pins_the_new_bytes(self):
        self._links("see plot.png", "u1")
        old = km._path_pins(SID, "u1")["plot.png"]
        self.plot.write_bytes(PNG_V2)
        self._links("here is plot.png again", "u2")
        new = km._path_pins(SID, "u2")["plot.png"]
        self.assertNotEqual(old, new, "each message keeps the picture it actually showed")
        self.assertEqual((km._pin_dir() / new).read_bytes(), PNG_V2)

    def test_space_paths_pin_too(self):
        spaced = self.cwd / "my plot.png"
        spaced.write_bytes(PNG_V1)
        got = km._space_paths("made `" + str(spaced) + "` for you", SID, "u3")
        self.assertEqual(got, [str(spaced)])
        self.assertIn(str(spaced), km._path_pins(SID, "u3"))

    def test_non_images_do_not_pin(self):
        (self.cwd / "notes.md").write_text("hello\n")
        self._links("see notes.md", "u4")
        self.assertEqual(km._path_pins(SID, "u4"), {}, "pins are for renderable images only")


class PinServing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path, pin=None, rng=None):
        url = "http://127.0.0.1:%d/file?path=%s&token=%s" % (self.port, urllib.parse.quote(path), TOKEN)
        if pin:
            url += "&pin=" + urllib.parse.quote(pin)
        req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_a_pinned_fetch_serves_the_snapshot_not_the_live_file(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "plot.png"
            live.write_bytes(PNG_V1)
            pid = km._pin_mention(str(live))
            live.write_bytes(PNG_V2)                        # overwritten on disk
            code, body = self._get(str(live), pin=pid)
            self.assertEqual(code, 200)
            self.assertEqual(body, PNG_V1, "the embed keeps the mention-time pixels")
            code, body = self._get(str(live))
            self.assertEqual(body, PNG_V2, "an unpinned fetch (the file viewer, a new message) is live")

    def test_an_evicted_pin_falls_back_to_the_live_file(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "plot.png"
            live.write_bytes(PNG_V2)
            ghost = "0" * 64 + ".png"                       # shape-valid, blob absent
            code, body = self._get(str(live), pin=ghost)
            self.assertEqual(code, 200)
            self.assertEqual(body, PNG_V2, "a lost snapshot degrades to today's behavior, never an error")

    def test_pin_ids_are_shape_gated_no_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "plot.png"
            live.write_bytes(PNG_V1)
            code, body = self._get(str(live), pin="../../serve-token")
            self.assertEqual(code, 200)
            self.assertEqual(body, PNG_V1, "a malformed pin is ignored outright — never joined to the store")

    def test_range_resume_works_on_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "plot.png"
            live.write_bytes(PNG_V1)
            pid = km._pin_mention(str(live))
            live.write_bytes(PNG_V2)
            code, body = self._get(str(live), pin=pid, rng="bytes=10-")
            self.assertEqual(code, 206)
            self.assertEqual(body, PNG_V1[10:], "flaky-wifi resume and history pins compose")


if __name__ == "__main__":
    unittest.main()
