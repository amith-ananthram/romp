#!/usr/bin/env python3
"""The file preview popover's kernel half (T351 stage 1): the slice cache and its rule.

Hovering a local file link in the chat shows the file's rendered head, or the section a `path#slug` link names. The
kernel keeps the TEXT of recently linked markdown and code files with a heading index, keyed on (path, mtime_ns),
bounded in entries and bytes, warmed on the pusher's path when _path_links verifies such a path in a message about to
ship, and GET /file?slice=1 serves one slice of it. A hover is a gesture the user did not choose, so the popover may
fetch only a path under the session's folder or the user's home, never a secrets-shaped name, only the kinds it can
show, under the caps; the verdict ships as pathPreview beside pathLinks, and a link absent from it is text plus "open"
with no request. The heading-slug rule is the viewer's own (md-links.ts), pinned over tests/fixtures/heading_slugs.json
on both sides. Synthetic fixtures only; hermetic state.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only pytest runs conftest's floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_fileslice", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
FIX = json.load(open(os.path.join(HERE, "fixtures", "heading_slugs.json"), encoding="utf-8"))

DOC = """# Guide

intro line

## Install

step one

### Details

fine print

## Fold Rules

about folds

```
# not a heading: fenced
```

## Install

the second install section
"""


class SlugParity(unittest.TestCase):
    def test_the_slug_rule_is_the_viewers_over_the_shared_fixture(self):
        for text, slug in FIX["slugs"]:
            self.assertEqual(km._heading_slug(text), slug, repr(text))
        for slugs_in, slugs_out in FIX["unique"]:
            self.assertEqual(km._unique_slugs(slugs_in), slugs_out, repr(slugs_in))

    def test_a_headings_text_reads_as_the_rendered_heading(self):
        self.assertEqual(km._heading_text("`code` and **bold** and [a link](x.md) and _em_"), "code and bold and a link and em")


class Headings(unittest.TestCase):
    def test_the_index_skips_fenced_code_and_numbers_duplicates_githubs_way(self):
        hs = km._slice_headings(DOC)
        self.assertEqual([(h["level"], h["text"], h["slug"]) for h in hs],
                         [(1, "Guide", "guide"), (2, "Install", "install"), (3, "Details", "details"), (2, "Fold Rules", "fold-rules"),
                          (2, "Install", "install-1")])
        self.assertEqual([h["line"] for h in hs], [0, 4, 8, 12, 20], "the line each heading sits on")
        self.assertEqual(km._slice_headings("no headings here\n"), [])
        self.assertEqual(len(km._slice_headings("# " + "h" * 5000 + "\n")[0]["text"]), 200, "a heading's text is capped, so the index is bounded in bytes")


class Slices(unittest.TestCase):
    def entry(self, text=DOC):
        return {"text": text, "headings": km._slice_headings(text), "size": len(text), "mtime_ns": 1}

    def test_a_section_runs_through_the_next_heading_of_the_same_or_a_higher_level(self):
        text, found, heading, truncated = km._slice_of(self.entry(), "install")
        self.assertTrue(found and not truncated)
        self.assertEqual(heading["slug"], "install")
        self.assertEqual(text, "## Install\n\nstep one\n\n### Details\n\nfine print\n", "a lower-level heading inside stays; the next ## ends it")
        text, found, _, _ = km._slice_of(self.entry(), "fold-rules")
        self.assertEqual(text.splitlines()[0], "## Fold Rules")
        self.assertIn("about folds", text); self.assertNotIn("the second install", text)
        text, found, heading, _ = km._slice_of(self.entry(), "install-1")
        self.assertTrue(found); self.assertEqual(text, "## Install\n\nthe second install section\n", "the duplicate by its numbered slug, to the end")
        text, found, heading, _ = km._slice_of(self.entry(), "guide")
        self.assertEqual(text, DOC, "a top-level section with nothing at its level after it runs to the end")

    def test_no_anchor_is_the_head_and_a_missing_anchor_falls_back_to_it(self):
        text, found, heading, truncated = km._slice_of(self.entry(), "")
        self.assertEqual((text, found, heading, truncated), (DOC, True, None, False))
        text, found, heading, _ = km._slice_of(self.entry(), "nope")
        self.assertEqual((text, found, heading), (DOC, False, None), "the head, and found says the anchor was not there")

    def test_the_slice_is_capped_at_a_popovers_worth(self):
        big = "# H\n" + ("x" * 100 + "\n") * 2000
        text, found, _, truncated = km._slice_of(self.entry(big), "")
        self.assertTrue(truncated and found)
        self.assertLessEqual(len(text.encode("utf-8")), km._SLICE_MAX_BYTES)
        self.assertTrue(text.endswith("x" * 100), "cut on a line boundary")


class Cache(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.mkdtemp(prefix="fileslice-")
        km._SLICE_CACHE.clear()

    def test_a_miss_reads_and_indexes_a_hit_serves_the_same_entry_a_rewrite_replaces_it(self):
        fp = os.path.join(self.lab, "a.md")
        Path(fp).write_text("# A\n\nbody\n")
        e1, hit, why = km._slice_load(fp)
        self.assertEqual((hit, why), (False, "")); self.assertEqual(e1["headings"][0]["slug"], "a"); self.assertEqual(e1["size"], len("# A\n\nbody\n"))
        e2, hit, _ = km._slice_load(fp)
        self.assertTrue(hit); self.assertIs(e2, e1)
        os.utime(fp, ns=(e1["mtime_ns"] + 10 ** 9, e1["mtime_ns"] + 10 ** 9))
        Path(fp).write_text("# B\n")
        st = os.stat(fp)
        e3, hit, _ = km._slice_load(fp)
        self.assertFalse(hit); self.assertEqual(e3["headings"][0]["slug"], "b"); self.assertEqual(e3["mtime_ns"], st.st_mtime_ns)
        self.assertEqual([k[0] for k in km._SLICE_CACHE], [fp], "one entry per path: the old mtime's is gone")

    def test_binary_and_oversize_files_are_not_cached(self):
        fp = os.path.join(self.lab, "b.md")
        Path(fp).write_bytes(b"\x00\x01\x02 not text")
        self.assertEqual(km._slice_load(fp), (None, False, "not text"))
        cap = km._TEXT_MAX_BYTES
        try:
            km._TEXT_MAX_BYTES = 16
            fp2 = os.path.join(self.lab, "c.md"); Path(fp2).write_text("x" * 40)
            self.assertEqual(km._slice_load(fp2), (None, False, "too large to show"))
        finally:
            km._TEXT_MAX_BYTES = cap
        self.assertEqual(len(km._SLICE_CACHE), 0)

    def test_the_content_belt_refuses_text_shaped_like_a_secret_and_never_caches_it(self):
        # every probe is ASSEMBLED here, never a credential-shaped literal in the repo (the scanner reads this file too)
        key_block = "-----BEGIN " + "RSA PRIVATE KEY" + "-----\nMIIE" + "x" * 40 + "\n-----END RSA PRIVATE KEY-----\n"
        assignment = "# notes\n\n" + "api" + "_key" + " = " + "Q" * 24 + "\n"
        provider = "token: " + "sk-" + "a" * 32 + "\n"
        jwt = "bearer " + ".".join("e" + "y" + "J" + "b" * 20 for _ in range(3)) + "\n"
        for i, text in enumerate((key_block, assignment, provider, jwt)):
            self.assertTrue(km._looks_secret(text), repr(text[:30]))
            fp = os.path.join(self.lab, "s%d.md" % i); Path(fp).write_text(text)
            self.assertEqual(km._slice_load(fp), (None, False, "looks like a secret"), repr(text[:30]))
        self.assertFalse(km._looks_secret("# Guide\n\nthe token bucket algorithm limits a rate; a password field is masked\n"), "the words alone are prose")
        self.assertEqual(len(km._SLICE_CACHE), 0, "a refused text is never cached")

    def test_the_cache_is_bounded_in_entries_and_bytes_least_recently_read_first(self):
        ents, byts = km._SLICE_CACHE_ENTRIES, km._SLICE_CACHE_BYTES
        try:
            km._SLICE_CACHE_ENTRIES = 2
            fps = []
            for i in range(3):
                fp = os.path.join(self.lab, "e%d.md" % i); Path(fp).write_text("# %d\n" % i); fps.append(fp); km._slice_load(fp)
            self.assertEqual([k[0] for k in km._SLICE_CACHE], fps[1:], "the first read went")
            km._slice_load(fps[1])                       # a read refreshes
            fp3 = os.path.join(self.lab, "e3.md"); Path(fp3).write_text("# 3\n"); km._slice_load(fp3)
            self.assertEqual([k[0] for k in km._SLICE_CACHE], [fps[1], fp3], "the least recently READ went, not the oldest")
            km._SLICE_CACHE_ENTRIES = 64
            km._SLICE_CACHE_BYTES = 30
            big = os.path.join(self.lab, "big.md"); Path(big).write_text("# big\n" + "y" * 20)
            km._slice_load(big)
            self.assertEqual([k[0] for k in km._SLICE_CACHE], [fp3, big], "the byte cap evicts the least recently read until the rest fits (4 + 26 = 30)")
        finally:
            km._SLICE_CACHE_ENTRIES, km._SLICE_CACHE_BYTES = ents, byts


class Allowed(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.mkdtemp(prefix="fileslice-allow-")
        self.cwd = os.path.join(self.lab, "proj"); os.makedirs(os.path.join(self.cwd, "docs"))
        os.makedirs(os.path.join(self.lab, "outside"))
        self._saved_cwd_of = km._cwd_of                       # the session's folder, the way test_path_links.py seams it
        km._cwd_of = lambda sid: self.cwd if sid == SID else ""
        km._SLICE_CACHE.clear()

    def tearDown(self):
        km._cwd_of = self._saved_cwd_of

    def w(self, rel, text="# T\n"):
        fp = os.path.join(self.lab, rel); Path(fp).write_text(text); return fp

    def test_a_symlink_is_judged_by_what_it_points_at(self):
        # the review on this change: a symlink named report.md pointing at a secret passed the name rule (which read the
        # link's name) while the confinement followed the link; the warm then read the secret into the cache at build time
        target = self.w("proj/docs/.env", "SETTING=" + "not-a-real-value" + "\n")
        link = os.path.join(self.cwd, "docs", "report.md"); os.symlink(target, link)
        self.assertEqual(km._slice_allowed(link, SID), (None, "a secrets-shaped name"), "the real name is the one judged")
        zipped = self.w("proj/docs/bundle.zip", "PK"); link2 = os.path.join(self.cwd, "docs", "notes.md"); os.symlink(zipped, link2)
        self.assertEqual(km._slice_allowed(link2, SID), (None, "not a kind the preview shows"), "a refused kind behind a markdown name")
        plain = self.w("proj/docs/real.md", "# R\n"); link3 = os.path.join(self.cwd, "docs", "alias.md"); os.symlink(plain, link3)
        self.assertEqual(km._slice_allowed(link3, SID), ("markdown", ""), "a link to a markdown file of the same kind is fine")
        code = self.w("proj/src.py", "print(1)\n"); link4 = os.path.join(self.cwd, "docs", "dressed.md"); os.symlink(code, link4)
        self.assertEqual(km._slice_allowed(link4, SID), (None, "a link dressed as another kind"), "a code file behind a markdown name")
        out = self.w("outside/o.md"); link5 = os.path.join(self.cwd, "docs", "escape.md"); os.symlink(out, link5)
        self.assertEqual(km._slice_allowed(link5, SID), (None, "outside the session's folder and your home"), "a symlink out of the roots")
        self.assertEqual(km._path_previews({"docs/report.md": "docs/report.md", "docs/alias.md": "docs/alias.md"}, SID), {"docs/alias.md": "markdown"},
                         "the map omits the dressed link; only the honest one is warmed")
        self.assertEqual([k[0] for k in km._SLICE_CACHE], [os.path.realpath(plain)], "the cache holds the real path of the honest link, nothing of the secret")

    def test_dotted_secret_stores_and_secret_directories_are_refused_by_the_real_path(self):
        for name in (".credentials.json", ".git-credentials", "credentials", "my-credentials.txt"):
            fp = self.w("proj/docs/" + name, "x")
            self.assertEqual(km._slice_allowed(fp, SID)[0], None, name)
            self.assertTrue(km._secret_path(fp), name)
        # a directory that holds nothing but secrets: judged on the real path's components under the home
        saved = os.path.expanduser
        try:
            os.path.expanduser = lambda p: self.lab if p == "~" else saved(p)
            for rel in (".config/gh/hosts.yml", ".ssh/known_hosts", ".aws/config", ".docker/config.json", ".gnupg/pubring.kbx", ".kube/config",
                        ".SSH/known_hosts", ".Config/GCloud/configurations/config_default", ".config/gcloud/application_default.json"):   # case-free, and gcloud's real home
                fp = os.path.join(self.lab, rel); os.makedirs(os.path.dirname(fp), exist_ok=True); Path(fp).write_text("x")
                self.assertTrue(km._secret_path(fp), rel)
            self.assertFalse(km._secret_path(os.path.join(self.lab, ".config", "nvim", "init.lua")), "an ordinary dot-directory is not a store")
        finally:
            os.path.expanduser = saved

    def test_the_route_answers_whole_as_status_payload_type(self):
        # the HTTP shape, pure of the socket: 403 with why for every refusal, 415 for bytes that are not text, 200 with hit
        g = self.w("proj/docs/g.md", "# G\n\nintro\n\n## Keys\n\nplain words\n")
        s, b, ct = km._slice_body(g, SID, "")
        self.assertEqual((s, ct, b["allowed"], b["kind"], b["hit"], b["found"]), (200, "application/json", True, "markdown", False, True))
        self.assertIn("intro", b["text"]); self.assertNotIn("headings", b, "the index stays on the kernel's side")
        s, b, _ = km._slice_body(g, SID, "keys")
        self.assertEqual((s, b["hit"], b["heading"]["slug"], b["text"].strip()), (200, True, "keys", "## Keys\n\nplain words"))
        s, b, _ = km._slice_body(g, SID, "nope")
        self.assertEqual((s, b["found"]), (200, False), "a missing anchor: the head, said so")
        for path, why in ((self.w("outside/o.md"), "outside the session's folder and your home"), (self.w("proj/docs/.env", "x"), "a secrets-shaped name"),
                          (os.path.join(self.cwd, "docs", "missing.md"), "not a file"), (self.w("proj/docs/b.zip", "PK"), "not a kind the preview shows")):
            s, b, ct = km._slice_body(path, SID, "")
            self.assertEqual((s, ct, b["allowed"], b["why"]), (403, "application/json", False, why), path)
            self.assertNotIn("text", b, "a refusal carries no text")
        link = os.path.join(self.cwd, "docs", "report.md"); os.symlink(os.path.join(self.cwd, "docs", ".env"), link)
        self.assertEqual(km._slice_body(link, SID, "")[1]["why"], "a secrets-shaped name", "the symlink is judged by its target")
        bad = self.w("proj/docs/bin.md"); Path(bad).write_bytes(b"\x00\x01 no")
        s, b, ct = km._slice_body(bad, SID, "")
        self.assertEqual((s, ct), (415, "text/plain")); self.assertIsInstance(b, str)
        png = self.w("proj/p.png", "\x89PNG")
        s, b, _ = km._slice_body(png, SID, "")
        self.assertEqual((s, b["kind"], b["allowed"]), (200, "image", True)); self.assertIn("mtimeNs", b); self.assertNotIn("text", b, "the bytes ride the plain route")

    def test_the_belt_reads_the_served_slice_so_a_section_past_the_first_64_kb_is_refused_too(self):
        # the review: the load scanned the head; an anchored section can lie past it
        prose = "".join("paragraph %d of ordinary words about folds and days.\n\n" % i for i in range(1500))
        self.assertGreater(len(prose), 64 * 1024)
        key_block = "-----BEGIN " + "RSA PRIVATE KEY" + "-----\nMIIE" + "y" * 40 + "\n-----END RSA PRIVATE KEY-----\n"
        fp = self.w("proj/docs/long.md", "# Long\n\n" + prose + "## Keys\n\n" + key_block)
        s, b, _ = km._slice_body(fp, SID, "")
        self.assertEqual((s, b["truncated"]), (200, True), "the head is clean and served")
        s, b, _ = km._slice_body(fp, SID, "keys")
        self.assertEqual((s, b["allowed"], b["why"]), (403, False, "looks like a secret"), "the section itself is scanned")
        self.assertNotIn("text", b)

    def test_kinds_by_name(self):
        self.assertEqual(km._slice_kind("a.md"), "markdown"); self.assertEqual(km._slice_kind("a.MARKDOWN"), "markdown")
        self.assertEqual(km._slice_kind("p.png"), "image"); self.assertEqual(km._slice_kind("r.pdf"), "pdf")
        self.assertEqual(km._slice_kind("s.py"), "code"); self.assertEqual(km._slice_kind("Makefile"), "code")
        self.assertEqual(km._slice_kind("x.zip"), None); self.assertEqual(km._slice_kind("bin.so"), None)

    def test_inside_the_sessions_folder_is_allowed_outside_it_and_home_is_text_only(self):
        inside = self.w("proj/docs/g.md"); outside = self.w("outside/g.md")
        self.assertEqual(km._slice_allowed(inside, SID), ("markdown", ""))
        kind, why = km._slice_allowed(outside, SID)
        self.assertEqual((kind, why), (None, "outside the session's folder and your home"))
        self.assertEqual(km._slice_allowed(inside, None)[0], None, "no session: only home confines, and the lab is not under it")
        self.assertEqual(km._slice_allowed(os.path.join(self.cwd, "docs"), SID), (None, "not a file"), "a directory")
        self.assertEqual(km._slice_allowed(os.path.join(self.cwd, "docs", "missing.md"), SID), (None, "not a file"))
        self.assertEqual(km._slice_allowed(self.w("proj/docs/x.zip"), SID), (None, "not a kind the preview shows"))

    def test_secrets_shaped_names_are_never_fetched(self):
        for name in (".env", ".env.local", "id_rsa", "id_ed25519.pub", "api-token.md", "my_secret.md", "server.pem", "site.key",
                     "credentials.json", ".netrc", "passwords.md", "keystore.jks"):
            fp = self.w("proj/docs/" + name, "# s\n")
            kind, why = km._slice_allowed(fp, SID)
            self.assertEqual((kind, why), (None, "a secrets-shaped name"), "%s: the name rule speaks first, whatever the kind" % name)
        self.assertEqual(km._slice_allowed(self.w("proj/docs/tokens-of-appreciation.md"), SID), (None, "a secrets-shaped name"),
                         "conservative on purpose: a name carrying the word is refused too; it is still text plus open")
        self.assertEqual(km._slice_allowed(self.w("proj/docs/notes.md"), SID), ("markdown", ""), "an ordinary name passes")

    def test_the_size_caps_refuse_loudly_with_the_reason(self):
        fp = self.w("proj/docs/big.md", "x" * 100)
        cap = km._TEXT_MAX_BYTES
        try:
            km._TEXT_MAX_BYTES = 50
            self.assertEqual(km._slice_allowed(fp, SID), (None, "too large to show"))
        finally:
            km._TEXT_MAX_BYTES = cap

    def test_the_preview_map_names_the_allowed_links_by_kind_and_warms_the_markdown(self):
        g = self.w("proj/docs/g.md", "# G\n"); s = self.w("proj/src.py", "print(1)\n"); o = self.w("outside/o.md")
        Path(os.path.join(self.cwd, "p.png")).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        links = {"docs/g.md": "docs/g.md", "src.py": "src.py", "p.png": "p.png", "../outside/o.md": o, "notes.zip": "notes.zip"}
        before = dict(km._PERF_STATS.snapshot()["fileSlice"])
        secretish = self.w("proj/docs/leak.md", "# L\n\n" + "api" + "_key" + " = " + "Z" * 24 + "\n")
        links["docs/leak.md"] = "docs/leak.md"
        pv = km._path_previews(links, SID)
        self.assertEqual(pv, {"docs/g.md": "markdown", "src.py": "code", "p.png": "image"},
                         "the outside path and the zip are absent (text-only, no request), and so is the markdown whose text looks like a secret (the belt at warm time)")
        self.assertEqual([k[0] for k in km._SLICE_CACHE], [g], "the honest markdown was warmed; the secret-shaped one never entered the cache; code waits for a hover")
        after = km._PERF_STATS.snapshot()["fileSlice"]
        self.assertEqual(after["warm"], before["warm"] + 1)
        km._path_previews(links, SID)
        self.assertEqual(km._PERF_STATS.snapshot()["fileSlice"]["warm"], before["warm"] + 1, "already warm: no second read")


class Perf(unittest.TestCase):
    def test_the_slice_counters_ride_the_perf_snapshot(self):
        before = dict(km._PERF_STATS.snapshot()["fileSlice"])
        km._PERF_STATS.file_slice(True, 10); km._PERF_STATS.file_slice(False, 5); km._PERF_STATS.file_slice(False, 0, warm=True)
        after = km._PERF_STATS.snapshot()["fileSlice"]
        self.assertEqual((after["hit"] - before["hit"], after["miss"] - before["miss"], after["bytes"] - before["bytes"], after["warm"] - before["warm"]), (1, 1, 15, 1))


if __name__ == "__main__":
    unittest.main()
