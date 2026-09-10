#!/usr/bin/env python3
"""Static bundles carry a validator and a gzip, so `no-cache` can do the revalidation it asks for.

The bundles were served with Cache-Control: no-cache and NO ETag/Last-Modified. no-cache means
"revalidate", but with nothing to revalidate against the browser had no conditional to send, so every
dashboard open re-downloaded the whole of dist — measured at 2.2MB uncompressed (render.js alone 773,663
bytes), and the server offered no compression at any Accept-Encoding. Over a tailnet that was the load time.

The freshness rule is deliberately unchanged: the tag is (mtime_ns, size), which moves on every rebuild
exactly like the ?v= stamp the urls carry, so a same-name rebuild still can never serve a stale body.
Synthetic only — files this test writes itself."""
import gzip as gziplib
import io
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_dist_etag", os.path.join(BIN, "romp-kernel"))

JS = (b"// a bundle-shaped blob that gzips like real code\n"
      b"function render(state){return state.sessions.map(function(s){return s.name;});}\n" * 400)


class DistBody(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        km._dist_body_cache.clear()

    def _write(self, name, data):
        p = self.dir / name
        p.write_bytes(data)
        return p

    def test_plain_when_the_client_offers_no_gzip(self):
        p = self._write("render.js", JS)
        body, enc, tag = km._dist_body(p, "")
        self.assertEqual(body, JS)
        self.assertEqual(enc, "")
        self.assertTrue(tag.startswith('"') and tag.endswith('"'), tag)

    def test_gzip_when_offered_and_it_round_trips(self):
        p = self._write("render.js", JS)
        body, enc, tag = km._dist_body(p, "gzip, br")
        self.assertEqual(enc, "gzip")
        self.assertEqual(gziplib.decompress(body), JS, "the compressed body must be the same asset")
        self.assertLess(len(body), len(JS) // 2, "a code-shaped bundle should compress well past 2x")

    def test_the_two_forms_carry_different_etags(self):
        p = self._write("render.js", JS)
        _, _, plain_tag = km._dist_body(p, "")
        _, _, gz_tag = km._dist_body(p, "gzip")
        self.assertNotEqual(plain_tag, gz_tag,
                            "one etag across both encodings lets a cache hand a gzip body to a client "
                            "that never asked for one")

    def test_the_etag_moves_when_the_file_changes(self):
        p = self._write("render.js", JS)
        _, _, first = km._dist_body(p, "")
        p.write_bytes(JS + b"// rebuilt\n")
        os.utime(p, (1, 1))                      # a rebuild that also moves mtime
        _, _, second = km._dist_body(p, "")
        self.assertNotEqual(first, second, "a rebuild must invalidate — this IS the no-cache guarantee")

    def test_a_rebuilt_file_serves_the_new_bytes(self):
        p = self._write("render.js", JS)
        km._dist_body(p, "gzip")                 # prime the cache
        p.write_bytes(b"// totally different\n" * 200)
        body, enc, _ = km._dist_body(p, "gzip")
        self.assertEqual(gziplib.decompress(body), p.read_bytes(), "the cache must not pin the old body")

    def test_small_files_are_not_compressed(self):
        p = self._write("tiny.js", b"var a=1;\n")
        body, enc, _ = km._dist_body(p, "gzip")
        self.assertEqual(enc, "", "under the floor the framing costs more than it saves")
        self.assertEqual(body, b"var a=1;\n")

    def test_already_compressed_types_are_not_recompressed(self):
        for name in ("logo.png", "font.woff2", "font.ttf"):
            p = self._write(name, os.urandom(4096))
            _, enc, _ = km._dist_body(p, "gzip")
            self.assertEqual(enc, "", "%s is already compressed" % name)

    def test_the_gzip_is_made_once_per_version(self):
        p = self._write("render.js", JS)
        first, _, _ = km._dist_body(p, "gzip")
        second, _, _ = km._dist_body(p, "gzip")
        self.assertIs(first, second, "the kernel is one process for every pane — compress once, not per request")

    def test_a_compression_failure_still_serves_the_asset(self):
        p = self._write("render.js", JS)
        saved = km.gzip.compress
        km.gzip.compress = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            km._dist_body_cache.clear()
            body, enc, _ = km._dist_body(p, "gzip")
        finally:
            km.gzip.compress = saved
        self.assertEqual(enc, "", "fail toward serving the bundle, never toward a broken dashboard")
        self.assertEqual(body, JS)


class RouteBehavior(unittest.TestCase):
    """The /dist route's half of the contract, asked of the REAL do_GET over a fake socket (the pattern
    tests/test_kernel_auth_hardening.py uses): a conditional request answers 304 with no body, both responses
    carry the tag and Vary, and an encoding-crossing conditional never 304s. Asserting that the lines exist
    in the source cannot catch a route that has them and still misbehaves."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "render.js").write_bytes(JS)
        self.saved_dist = km.DIST
        km.DIST = self.dir                       # the route reads the module global at request time
        km._dist_body_cache.clear()

    def tearDown(self):
        km.DIST = self.saved_dist

    def _get(self, path, headers=None):
        h = km.Handler.__new__(km.Handler)
        h.client_address = ("127.0.0.1", 0)
        hdrs = {"X-Romp-Token": km.TOKEN}        # the route is token-gated like every page fetch
        hdrs.update(headers or {})
        h.headers = hdrs
        h.path = path
        h.command = "GET"
        h.request_version = "HTTP/1.1"
        h.wfile = io.BytesIO()
        h.rfile = io.BytesIO()
        h.close_connection = True
        got = {"headers": {}}
        h.send_response = lambda code, *a: got.__setitem__("status", code)
        h.send_header = lambda k, v: got["headers"].__setitem__(k, v)
        h.end_headers = lambda: None
        h.log_message = lambda *a: None
        h.do_GET()
        return got.get("status"), got["headers"], h.wfile.getvalue()

    def test_the_first_fetch_carries_the_tag_vary_and_no_cache(self):
        status, hdrs, body = self._get("/dist/render.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, JS)
        self.assertRegex(hdrs.get("ETag", ""), r'^"[0-9a-f]+-[0-9a-f]+"$')
        self.assertEqual(hdrs.get("Vary"), "Accept-Encoding")
        self.assertEqual(hdrs.get("Cache-Control"), "no-cache", "revalidation is still demanded, only made cheap")
        self.assertNotIn("Content-Encoding", hdrs)

    def test_a_matching_conditional_answers_304_with_no_body(self):
        _, first, _ = self._get("/dist/render.js")
        status, hdrs, body = self._get("/dist/render.js", {"If-None-Match": first["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"", "a 304 carries no body: that is the whole saving")
        self.assertEqual(hdrs.get("ETag"), first["ETag"])
        self.assertEqual(hdrs.get("Vary"), "Accept-Encoding")
        self.assertEqual(hdrs.get("Cache-Control"), "no-cache")

    def test_a_stale_tag_gets_the_full_body_again(self):
        status, _, body = self._get("/dist/render.js", {"If-None-Match": '"0-0"'})
        self.assertEqual(status, 200)
        self.assertEqual(body, JS)

    def test_a_same_name_rebuild_defeats_the_old_tag(self):
        _, first, _ = self._get("/dist/render.js")
        p = self.dir / "render.js"
        p.write_bytes(JS + b"// rebuilt\n")
        os.utime(p, (1, 1))
        status, hdrs, body = self._get("/dist/render.js", {"If-None-Match": first["ETag"]})
        self.assertEqual(status, 200, "a same-name rebuild must never 304 against the old tag")
        self.assertEqual(body, JS + b"// rebuilt\n")
        self.assertNotEqual(hdrs["ETag"], first["ETag"])

    def test_gzip_is_served_under_its_own_tag_and_never_crosses_encodings(self):
        _, plain, _ = self._get("/dist/render.js")
        status, hdrs, body = self._get("/dist/render.js", {"Accept-Encoding": "gzip, br"})
        self.assertEqual(status, 200)
        self.assertEqual(hdrs.get("Content-Encoding"), "gzip")
        self.assertEqual(gziplib.decompress(body), JS)
        self.assertNotEqual(hdrs["ETag"], plain["ETag"])
        # the gzip tag revalidates the gzip form...
        status, _, body = self._get("/dist/render.js", {"Accept-Encoding": "gzip", "If-None-Match": hdrs["ETag"]})
        self.assertEqual((status, body), (304, b""))
        # ...but a plain-form tag from a client now offering gzip gets a body, never a 304 for the wrong form
        status, h2, body = self._get("/dist/render.js", {"Accept-Encoding": "gzip", "If-None-Match": plain["ETag"]})
        self.assertEqual(status, 200)
        self.assertEqual(h2.get("Content-Encoding"), "gzip")
        self.assertEqual(gziplib.decompress(body), JS)

    def test_the_traversal_guard_still_holds(self):
        status, _, _ = self._get("/dist/../kernel.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
