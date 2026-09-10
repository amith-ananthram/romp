#!/usr/bin/env python3
"""An SDK session that /clear-forks must READ its new transcript (the user 2026-07-10).

`/clear` on an SDK session mints a new transcript fsid under the same romp sid; the SDK backend
records it as the registry's lastSid (from the CLI's own init message — the authoritative source).
discover() previously associated forks only by custom-title (a tmux-ism the SDK never writes), so
every surface stayed pinned to the dead anchor file: the chat showed pre-clear history forever and
the timeline drew the anchor's unsettled tail as an ever-growing work bar. Now the anchor entry's
PATH follows lastSid (the sid stays stable — goals/captions/chat key on it), the discover
fingerprint signs the lastSid VALUE (a registry rewrite alone must bust the cache), and the parse
wrappers put the anchor file among the candidates so a resume-style fork that back-links across
files keeps its history while a /clear fork (parentUuid null) starts fresh.

All fixtures are SYNTHETIC (invented text, placeholder UUIDs, hostname TESTHOST).
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
NAME = "TESTHOST-session"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class SdkClearForkBase(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, stem, records):
        p = self.proj / (stem + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return p

    def _write_reg(self, last_sid, bump=0):
        p = jd.SDKDIR / (SID + ".json")
        p.write_text(json.dumps({"sid": SID, "name": NAME, "lastSid": last_sid, "alive": True}))
        # same-second rewrites share an mtime on coarse filesystems — bump it so the memo re-reads
        os.utime(p, (self.now + bump, self.now + bump))

    def _anchor_records(self):
        t = self.now - 600
        return [uline(t, "pre-clear ask about the widget", "u1"),
                aline(t + 5, "pre-clear widget answer", "a1", parent="u1")]


class DiscoverFollowsLastSid(SdkClearForkBase):
    def test_a_diverged_lastSid_moves_the_anchor_entrys_path_to_the_fork(self):
        self._write(SID, self._anchor_records())
        self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9")])
        self._write_reg(FORK)
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(len(rows), 1, "the session keeps ONE entry under its stable romp sid")
        fsid, path, anchor, name = rows[0]
        self.assertEqual(Path(path).stem, FORK, "the entry reads the CURRENT (lastSid) transcript")
        self.assertEqual((anchor, name), (SID, NAME))

    def test_an_undiverged_registry_keeps_the_anchor_path(self):
        self._write(SID, self._anchor_records())
        self._write_reg(SID)                              # lastSid == sid → no fork
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(Path(rows[0][1]).stem, SID)

    def test_a_lastSid_with_no_transcript_yet_falls_back_to_the_anchor(self):
        # the registry write can land a beat before the CLI creates the new file — never go dark
        self._write(SID, self._anchor_records())
        self._write_reg(FORK)
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(Path(rows[0][1]).stem, SID)

    def test_a_registry_only_lastSid_flip_busts_the_discover_cache(self):
        # the fork file already exists (it bumped the dir mtime long ago); ONLY the registry then
        # flips lastSid — no new directory entry, so the VALUE in the fingerprint must carry it
        self._write(SID, self._anchor_records())
        self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9")])
        a = jd.discover(self.now)
        self.assertEqual(Path([r for r in a if r[0] == SID][0][1]).stem, SID)
        self._write_reg(FORK, bump=1)
        b = jd.discover(self.now)
        self.assertIsNot(b, a, "the lastSid flip re-fingerprints")
        self.assertEqual(Path([r for r in b if r[0] == SID][0][1]).stem, FORK)


class ForkParseCandidates(SdkClearForkBase):
    def _texts(self, session):
        # atoms carry the raw record (text nested in message content) — serialize for a plain contains-check
        return "\n".join(json.dumps(t.get("atoms", [])) for t in session["turns"])

    def test_a_clear_fork_parses_FRESH_pre_clear_history_drops(self):
        self._write(SID, self._anchor_records())
        leaf = self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9"),
                                  aline(self.now - 55, "post-clear reply", "a9", parent="u9")])
        session = jd.parsed_session(SID, [str(leaf)], self.now)
        texts = self._texts(session)
        self.assertIn("post-clear fresh ask", texts)
        self.assertNotIn("pre-clear", texts, "/clear cut the chain — the transcript reads brand-new")

    def test_a_resume_style_fork_that_backlinks_keeps_its_history(self):
        self._write(SID, self._anchor_records())
        # the fork's head links to a1 IN THE ANCHOR FILE — the walk must cross files via the
        # anchor-sibling candidate parsed_session adds on its own
        leaf = self._write(FORK, [uline(self.now - 60, "post-resume ask", "u9", parent="a1"),
                                  aline(self.now - 55, "post-resume reply", "a9", parent="u9")])
        session = jd.parsed_session(SID, [str(leaf)], self.now)
        texts = self._texts(session)
        self.assertIn("post-resume ask", texts)
        self.assertIn("pre-clear ask", texts, "cross-file resume chain keeps pre-fork history")


class KernelParseMirrors(unittest.TestCase):
    """The kernel's _parse must mirror the anchor-sibling candidates (it has no jsdom-style harness —
    importing bin/romp-kernel runs the SDK boot reconcile against LIVE state — so this pins source)."""
    def test_kernel_parse_adds_the_anchor_sibling_candidate(self):
        src = (Path(BIN) / "romp-kernel").read_text()
        self.assertIn('anchor = os.path.join(os.path.dirname(path), sid + ".jsonl")', src)
        self.assertIn('if os.path.basename(path) != sid + ".jsonl" and os.path.exists(anchor):', src)
        self.assertIn("candidate_files=cands", src)


if __name__ == "__main__":
    unittest.main()
