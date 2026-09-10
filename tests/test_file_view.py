#!/usr/bin/env python3
"""Serving SOURCE/TEXT over /file, so a file link can be FOLLOWED from a remote dashboard.

Clicking a path in the chat used to post `openFile`, which the kernel served by handing the path to an
opener on ITS machine. Two things were wrong with that (the user 2026-08-08). The opener was bare macOS
`open`, so off macOS the OSError was swallowed and the click did nothing whatsoever — no reply, no error,
a link that looked alive. And even repaired it opens the file on the KERNEL's screen, which is the wrong
machine entirely when the dashboard is being read over the internet from somewhere else.

The bytes have to reach the browser. /file already did that for images and PDFs; these tests cover the
text half of it — the allowlist, the size cap the user asked for, the binary sniff behind it — plus the
opener now reporting that it cannot open anything instead of failing mute.

Synthetic files in a temp dir only.
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_fileview", os.path.join(BIN, "romp-kernel"))


class TextAllowlist(unittest.TestCase):
    def test_source_and_prose_extensions_are_text(self):
        for p in ["/a/kernel.py", "/a/render.ts", "/a/notes.md", "/a/server.log", "/a/c.json",
                  "/a/x.yaml", "/a/run.sh", "/a/Main.java", "/a/q.sql", "/a/t.bats", "/a/d.patch"]:
            self.assertTrue(km._is_text_path(p), p)

    def test_extensionless_conventions_count_and_case_does_not(self):
        for p in ["/a/Makefile", "/a/Dockerfile", "/a/LICENSE", "/a/.gitignore", "/a/README"]:
            self.assertTrue(km._is_text_path(p), p)
        self.assertTrue(km._is_text_path("/a/MAKEFILE"))
        self.assertTrue(km._is_text_path("/a/KERNEL.PY"))

    def test_media_and_binaries_are_not_text(self):
        # images/PDF keep their OWN branch (bytes, not decoded); a real binary is not offered at all
        for p in ["/a/plot.png", "/a/report.pdf", "/a/x.zip", "/a/lib.so", "/a/photo.jpeg", "/a/db.sqlite"]:
            self.assertFalse(km._is_text_path(p), p)


class DecodeText(unittest.TestCase):
    def test_utf8_round_trips_and_latin1_never_loses_the_file(self):
        self.assertEqual(km._decode_text("héllo\n".encode("utf-8")), "héllo\n")
        # one bad byte in an otherwise fine log costs one odd glyph, not the whole file
        self.assertEqual(km._decode_text(b"ok \xff done"), "ok \xff done".replace("\xff", "ÿ"))

    def test_a_nul_byte_means_this_is_not_text(self):
        self.assertIsNone(km._decode_text(b"\x7fELF\x00\x00binary"))
        self.assertIsNone(km._decode_text(b"text then \x00 a nul"))
        # …and the sniff only reads the head, so a NUL past it is not what decides
        self.assertIsNotNone(km._decode_text(b"a" * 9000 + b"\x00"))


class HumanBytes(unittest.TestCase):
    def test_it_reads_like_a_size(self):
        self.assertEqual(km._human_bytes(512), "512 bytes")
        self.assertEqual(km._human_bytes(2048), "2.0 KB")
        self.assertEqual(km._human_bytes(3 * (1 << 20)), "3.0 MB")

    def test_the_text_cap_reads_as_the_round_number_it_means(self):
        # 2_000_000 divided by 1<<20 read "limit 1.9 MB" — a cap that sounds miscopied. The constant
        # is a power of two now, so the 413 says "2.0 MB" (the user 2026-08-09).
        self.assertEqual(km._TEXT_MAX_BYTES, 2 * 1024 * 1024)
        self.assertEqual(km._human_bytes(km._TEXT_MAX_BYTES), "2.0 MB")


class _Route(unittest.TestCase):
    """/file driven through the real Handler method, with _send captured."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.h = object.__new__(km.Handler)
        self.sent = []
        self.h._send = lambda *a, **k: self.sent.append(a)

    def get(self, path, **q):
        qs = {"path": [path]}
        qs.update({k: [v] for k, v in q.items()})
        km.Handler._file_preview(self.h, qs)
        return self.sent[-1]

    def write(self, name, data):
        fp = os.path.join(self.tmp, name)
        with open(fp, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        return fp


class ServeText(_Route):
    def test_a_source_file_comes_back_as_text_the_browser_can_show(self):
        fp = self.write("kernel.py", "def hi():\n    return 1\n")
        status, body, mime = self.get(fp)[:3]
        self.assertEqual(status, 200)
        self.assertEqual(body, "def hi():\n    return 1\n")
        self.assertTrue(mime.startswith("text/plain"), mime)
        self.assertIn("charset=utf-8", mime)

    def test_html_is_served_as_text_not_as_a_page(self):
        # the viewer highlights source; serving text/html would make the kernel a hosting origin
        fp = self.write("page.html", "<b>hi</b>")
        status, body, mime = self.get(fp)[:3]
        self.assertEqual(status, 200)
        self.assertTrue(mime.startswith("text/plain"), mime)

    def test_a_type_we_do_not_view_415s_naming_the_path_so_the_client_can_offer_the_download(self):
        # was a 404, indistinguishable from "no such file" — but the truths differ and the client acts
        # on the difference: 404 means give up, 415 means offer ?download=1 (the user 2026-08-09)
        fp = self.write("archive.zip", b"PK\x03\x04stuff")
        status, body = self.get(fp)[:2]
        self.assertEqual(status, 415)
        self.assertIn("not viewable in the browser", body)
        self.assertIn(km._tilde(fp), body, "every /file error names the resolved path")

    def test_a_missing_file_404s_naming_the_path_it_tried(self):
        # a bare "not found" told the user nothing about WHAT was tried — a relative link resolves
        # against the session's cwd, which is exactly the part they can't see (the user 2026-08-09)
        fp = os.path.join(self.tmp, "nope.py")
        status, body = self.get(fp)[:2]
        self.assertEqual(status, 404)
        self.assertIn(km._tilde(fp), body)

    def test_a_binary_wearing_a_text_name_is_refused_rather_than_served_as_garbage(self):
        fp = self.write("weird.log", b"\x00\x01\x02 binary pretending")
        status, body = self.get(fp)[:2]
        self.assertEqual(status, 415)
        self.assertIn("not a text file", body)
        self.assertIn(km._tilde(fp), body, "every /file error names the resolved path")

    def test_oversize_text_413s_and_the_message_names_the_path_size_and_cap(self):
        fp = self.write("huge.log", b"x" * (km._TEXT_MAX_BYTES + 1))
        status, body = self.get(fp)[:2]
        self.assertEqual(status, 413)
        self.assertIn("too large", body)
        self.assertIn(km._tilde(fp), body, "every /file error names the resolved path")
        self.assertIn("2.0 MB", body, "the cap reads as the round number it means")
        self.assertIn(km._human_bytes(km._TEXT_MAX_BYTES), body, "say what the limit IS, not just that one exists")

    def test_the_text_cap_is_its_own_and_far_under_the_media_one(self):
        # a 50 MB log dragged down a tunnel to a phone helps nobody (the user asked for a size cap)
        self.assertLess(km._TEXT_MAX_BYTES, km._PREVIEW_MAX_BYTES)
        img = self.write("plot.png", b"\x89PNG\r\n\x1a\n" + b"p" * (km._TEXT_MAX_BYTES + 1))
        self.assertEqual(self.get(img)[0], 200, "the media cap is untouched by the text one")

    def test_a_relative_path_still_resolves_against_the_session_cwd(self):
        self.write("rel.md", "# hi\n")
        with mock.patch.object(km, "_cwd_of", lambda sid: self.tmp):
            status, body = self.get("rel.md", sid="11111111-2222-3333-4444-555555555555")[:2]
        self.assertEqual(status, 200)
        self.assertEqual(body, "# hi\n")


class OpenerReportsInsteadOfSwallowing(unittest.TestCase):
    """_open_file's silent-nothing was the same shape as the Browse… bug, and is fixed the same way."""

    def test_a_headless_machine_has_no_opener(self):
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None); os.environ.pop("WAYLAND_DISPLAY", None)
            self.assertIsNone(km._opener_cmd())
            self.assertFalse(km._open_file("/tmp/x.py"), "no desktop → says no, rather than pretending")

    def test_a_linux_desktop_uses_xdg_open_and_macos_keeps_open(self):
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.object(km.shutil, "which", lambda e: "/usr/bin/" + e), \
             mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
            self.assertEqual(km._opener_cmd(), "xdg-open")
        with mock.patch.object(km.sys, "platform", "darwin"):
            self.assertEqual(km._opener_cmd(), "open")

    def test_the_click_is_answered_when_the_kernel_cannot_serve_it(self):
        sent = []
        client = {"app": "chat", "alive": True, "send": lambda s: sent.append(json.loads(s))}
        with mock.patch.object(km, "_open_file", lambda *a, **k: False):
            km.Handler._dispatch_ws(object.__new__(km.Handler),
                                    {"type": "openFile", "path": "/tmp/notes.md"}, client)
        self.assertTrue(sent, "silence is the bug")
        self.assertEqual(sent[-1]["type"], "warn")
        self.assertIn("notes.md", sent[-1]["text"])
        self.assertIn("no desktop session", sent[-1]["text"])

    def test_a_kernel_that_can_open_it_says_nothing(self):
        sent = []
        client = {"app": "chat", "alive": True, "send": lambda s: sent.append(json.loads(s))}
        with mock.patch.object(km, "_open_file", lambda *a, **k: True):
            km.Handler._dispatch_ws(object.__new__(km.Handler),
                                    {"type": "openFile", "path": "/tmp/notes.md"}, client)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
