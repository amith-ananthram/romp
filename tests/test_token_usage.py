#!/usr/bin/env python3
"""Tests for build_timeline's token-usage split:
  _session_tokens  — per-session transcript token sums (the SESSIONS half), one row per API
                     response across the main transcript and its subagents' (_session_tok_rows)
  _judge_usage     — the judge PIPELINE rollup from judge-usage.jsonl (per-judge / per-tier)
Synthetic data only (placeholder usage numbers, a temp state dir)."""
import json
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

NOW = 1781100000


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _asst(usage, ts=None, model=None, mid=None):
    msg = {"role": "assistant", "content": [], "usage": usage}
    if model is not None:
        msg["model"] = model
    if mid is not None:
        msg["id"] = mid
    o = {"type": "assistant", "message": msg}
    if ts is not None:
        o["timestamp"] = ts
    return json.dumps(o)


class SessionTokens(unittest.TestCase):
    def test_sums_windowed_usage_across_assistant_messages(self):
        t0 = NOW - 3600
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(_asst({"input_tokens": 10, "output_tokens": 5,
                           "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200}, iso(NOW - 100)) + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")  # ignored
            f.write(_asst({"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 50}, iso(NOW - 50)) + "\n")  # missing cache_w
            f.write(_asst({"input_tokens": 999, "output_tokens": 999}, iso(NOW - 99999)) + "\n")  # OUTSIDE the window → dropped
            path = f.name
        try:
            self.assertEqual(km._session_tokens(path, t0),
                             {"in": 13, "out": 12, "cache_w": 100, "cache_r": 250})
        finally:
            os.unlink(path)

    def test_missing_file_returns_zeros(self):
        self.assertEqual(km._session_tokens("/no/such/transcript.jsonl", NOW - 3600),
                         {"in": 0, "out": 0, "cache_w": 0, "cache_r": 0})

    def test_records_sharing_a_message_id_count_once(self):
        """The CLI writes a multi-block response as several assistant records sharing one message.id;
        summing records counted each response 2.3-3.0x. In a MAIN transcript the records repeat one usage
        block (2,515 groups measured, none differing); subagent transcripts differ — see the next test.
        One row per id, the largest output count kept; an id-less record stands alone."""
        u = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            with open(p, "w") as f:
                for _ in range(3):
                    f.write(_asst(u, iso(NOW - 100), mid="msg_aaaa") + "\n")         # a 3-block response
                f.write(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 90)) + "\n")   # no id
                f.write(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 80)) + "\n")   # no id
                f.write(_asst({"input_tokens": 7, "output_tokens": 2}, iso(NOW - 70), mid="msg_bbbb") + "\n")
                # a split response whose LAST record carries a larger output count (a final tally): the
                # larger figure is the one kept, never both
                f.write(_asst({"input_tokens": 4, "output_tokens": 1}, iso(NOW - 60), mid="msg_cccc") + "\n")
                f.write(_asst({"input_tokens": 4, "output_tokens": 9}, iso(NOW - 59), mid="msg_cccc") + "\n")
            self.assertEqual(km._session_tokens(p, NOW - 3600),
                             {"in": 10 + 2 + 7 + 4, "out": 5 + 2 + 2 + 9, "cache_w": 100, "cache_r": 200})

    def test_subagent_same_id_records_keep_the_final_tally_whatever_their_order(self):
        """A SUBAGENT transcript's same-id records DIFFER: every record but the last carries the
        stream-start snapshot (a few output tokens) and the last the final tally — measured on one
        installation's subagent files, about nine in ten multi-record groups (88% and 94% on two
        samples), the last record the maximum in every one. The fold keeps the LARGEST output count, in either order, so a first-record fold (about
        a tenth of the subagents' output) or a last-record fold cannot quietly replace it."""
        first = {"input_tokens": 4000, "output_tokens": 3, "cache_read_input_tokens": 50000}    # stream start
        last = {"input_tokens": 4000, "output_tokens": 1950, "cache_read_input_tokens": 50000}  # final tally
        with tempfile.TemporaryDirectory() as d:
            for name, order in (("a.jsonl", (first, first, last)), ("b.jsonl", (last, first, first))):
                pth = os.path.join(d, name)
                with open(pth, "w") as f:
                    for k, u in enumerate(order):
                        f.write(_asst(u, iso(NOW - 100 + k), mid="msg_sub") + "\n")
                self.assertEqual(km._session_tokens(pth, NOW - 3600),
                                 {"in": 4000, "out": 1950, "cache_w": 0, "cache_r": 50000},
                                 name + ": one row, the final tally's output count")

    def test_subagent_transcripts_beside_the_main_one_count_and_refresh_the_cache(self):
        """`<sid>/subagents/agent-<id>.jsonl` holds each spawned agent's conversation; its tokens are the
        session's spend too. Each contributing file caches on its own stamp, so a subagent landing
        later refreshes the sum while the main file rests."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "11111111-2222-3333-4444-555555555555.jsonl")
            with open(p, "w") as f:
                f.write(_asst({"input_tokens": 10, "output_tokens": 5}, iso(NOW - 100), mid="m1") + "\n")
            sub = pathlib.Path(d) / "11111111-2222-3333-4444-555555555555" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-a1.jsonl").write_text(
                _asst({"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000},
                      iso(NOW - 90), mid="m2") + "\n"
                + _asst({"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000},
                        iso(NOW - 89), mid="m2") + "\n"                       # a split block, deduped too
                + _asst({"input_tokens": 999, "output_tokens": 999}, iso(NOW - 99999), mid="m3") + "\n")  # out of window
            (sub / "notes.txt").write_text("not a transcript\n")
            # a Workflow agent's transcript one level down (Claude Code 2.1.261 writes
            # subagents/workflows/wf_<id>/agent-<id>.jsonl): the walk is recursive, so it counts too
            wf = sub / "workflows" / "wf_1111-2222"
            wf.mkdir(parents=True)
            (wf / "agent-c3.jsonl").write_text(_asst({"input_tokens": 1000, "output_tokens": 500}, iso(NOW - 80), mid="m5") + "\n")
            # …but the walk never leaves the session's own tree: a symlinked directory is not followed
            other = pathlib.Path(d) / "elsewhere"
            other.mkdir()
            (other / "agent-zz.jsonl").write_text(_asst({"input_tokens": 7777, "output_tokens": 7777}, iso(NOW - 70), mid="m6") + "\n")
            os.symlink(other, sub / "linked")
            # …nor is a symlinked FILE: os.walk lists one like any other file (only DIRECTORY links go
            # unfollowed), so a reader that trusted the listing would open it wherever it points
            os.symlink(other / "agent-zz.jsonl", sub / "agent-link.jsonl")
            self.assertEqual(km._session_tokens(p, NOW - 3600), {"in": 1110, "out": 555, "cache_w": 0, "cache_r": 1000})
            # a second subagent lands; the main transcript's mtime has not moved
            mt = os.path.getmtime(p)
            (sub / "agent-b2.jsonl").write_text(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 10), mid="m4") + "\n")
            os.utime(p, (mt, mt))
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 1111, "the new subagent file refreshes the rows")
            # a NESTED file growing refreshes them too (its mtime is in the fingerprint like any other)
            with (wf / "agent-c3.jsonl").open("a") as f:
                f.write(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 5), mid="m7") + "\n")
            os.utime(p, (mt, mt))
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 1112, "a nested file's growth refreshes the rows")
            self.assertEqual(km._subagent_transcripts(p),
                             [str(sub / "agent-a1.jsonl"), str(sub / "agent-b2.jsonl"), str(wf / "agent-c3.jsonl")],
                             "sorted, nested files included, the symlinked directory not walked, the symlinked file skipped")
            self.assertEqual(km._subagent_transcripts(os.path.join(d, "no-such.jsonl")), [])
            self.assertEqual(km._subagent_transcripts(os.path.join(d, "x.txt")), [])

    def test_each_transcript_caches_on_its_own_stamp_so_one_file_moving_parses_only_itself(self):
        """The row cache is per FILE (path -> (stamp, rows)), not per session: a subagent file landing or
        growing parses that file alone while the main transcript's rows stay cached, and the main file
        moving leaves every subagent's rows in place. A per-session fingerprint re-parsed the whole tree
        (main transcript plus every subagent file) whenever any one of them moved — on a live session
        with Workflow agents running, that is every build, on the GIL, in the /analytics handler."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "11111111-2222-3333-4444-555555555555.jsonl")
            with open(p, "w") as f:
                f.write(_asst({"input_tokens": 10, "output_tokens": 5}, iso(NOW - 100), mid="m1") + "\n")
            sub = pathlib.Path(d) / "11111111-2222-3333-4444-555555555555" / "subagents"
            sub.mkdir(parents=True)
            a1 = sub / "agent-a1.jsonl"
            a1.write_text(_asst({"input_tokens": 100, "output_tokens": 50}, iso(NOW - 90), mid="m2") + "\n")
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 110)
            main_rows, a1_rows = km._session_tok_cache[p][1], km._session_tok_cache[str(a1)][1]
            self.assertEqual([r[1] for r in main_rows], [10], "the main transcript's own rows, cached under its path")
            self.assertEqual([r[1] for r in a1_rows], [100], "the subagent's own rows, cached under its path")
            # a second subagent lands: it parses, the two cached files do not
            mt = os.path.getmtime(p)
            (sub / "agent-b2.jsonl").write_text(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 10), mid="m4") + "\n")
            os.utime(p, (mt, mt))
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 111)
            self.assertIs(km._session_tok_cache[p][1], main_rows, "the main transcript was not re-parsed")
            self.assertIs(km._session_tok_cache[str(a1)][1], a1_rows, "the untouched subagent was not re-parsed")
            # the main transcript grows: it parses, the subagents' rows stay
            with open(p, "a") as f:
                f.write(_asst({"input_tokens": 1000, "output_tokens": 1}, iso(NOW - 5), mid="m8") + "\n")
            os.utime(p, (mt + 5, mt + 5))
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 1111)
            self.assertIsNot(km._session_tok_cache[p][1], main_rows, "the grown main transcript was re-parsed")
            self.assertIs(km._session_tok_cache[str(a1)][1], a1_rows, "…and the subagent's rows were not")
            # a same-mtime rewrite that changes the size still refreshes (the stamp is mtime AND size)
            b2_rows = km._session_tok_cache[str(sub / "agent-b2.jsonl")][1]
            bmt = os.path.getmtime(sub / "agent-b2.jsonl")
            with (sub / "agent-b2.jsonl").open("a") as f:
                f.write(_asst({"input_tokens": 1, "output_tokens": 1}, iso(NOW - 4), mid="m9") + "\n")
            os.utime(sub / "agent-b2.jsonl", (bmt, bmt))
            self.assertEqual(km._session_tokens(p, NOW - 3600)["in"], 1112)
            self.assertIsNot(km._session_tok_cache[str(sub / "agent-b2.jsonl")][1], b2_rows)

    def test_a_symlinked_subagents_directory_is_not_walked_either(self):
        """os.walk follows its TOP argument even when it is a symlink (followlinks governs the descent, not
        the root), so `<sid>/subagents` itself pointing elsewhere would read another tree wholesale."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "11111111-2222-3333-4444-555555555555.jsonl")
            with open(p, "w") as f:
                f.write(_asst({"input_tokens": 10, "output_tokens": 5}, iso(NOW - 100), mid="m1") + "\n")
            other = pathlib.Path(d) / "elsewhere"
            other.mkdir()
            (other / "agent-zz.jsonl").write_text(_asst({"input_tokens": 7777, "output_tokens": 7777}, iso(NOW - 70), mid="m6") + "\n")
            sid_dir = pathlib.Path(d) / "11111111-2222-3333-4444-555555555555"
            sid_dir.mkdir()
            os.symlink(other, sid_dir / "subagents")
            self.assertEqual(km._subagent_transcripts(p), [])
            self.assertEqual(km._session_tokens(p, NOW - 3600), {"in": 10, "out": 5, "cache_w": 0, "cache_r": 0})


class JudgeUsageIncrementalCache(unittest.TestCase):
    """_judge_usage_rows (2026-08-13): the append-only, never-rotated judge-usage.jsonl used to be
    re-read and re-parsed IN FULL by both the analytics modal and every timeline build (38.7 MB
    measured) — the term that froze the dashboard as the log grew. Rows now parse once, appends parse
    from the last byte offset, and the cache is keyed on the PATH too (a test repointing jd.STATE must
    never inherit another dir's offset — the overrides-sandbox lesson)."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = pathlib.Path(self.td.name)
        km._JUDGE_USAGE_CACHE.update(path=None, size=-1, mtime=0.0, rows=[])

    def tearDown(self):
        jd.STATE = self.saved
        km._JUDGE_USAGE_CACHE.update(path=None, size=-1, mtime=0.0, rows=[])
        self.td.cleanup()

    def _row(self, t, judge="captioner", **kw):
        return json.dumps({"t": t, "judge": judge, "tier": kw.get("tier", "index"),
                           "in": kw.get("i", 10), "out": kw.get("o", 5),
                           "cost": kw.get("cost", 0.01), "ms": 100})

    def test_appends_parse_incrementally_and_rollup_reads_the_cache(self):
        p = jd.STATE / "judge-usage.jsonl"
        p.write_text(self._row(NOW - 1800) + "\n")
        self.assertEqual(len(km._judge_usage_rows()), 1)
        u = km._judge_usage(NOW - 3600)
        self.assertEqual(u["total"]["calls"], 1)
        with p.open("a") as fh:                          # an append parses from the offset, not from zero
            fh.write(self._row(NOW - 600, judge="planner", tier="triage", i=70, o=30) + "\n")
        rows = km._judge_usage_rows()
        self.assertEqual(len(rows), 2)
        u = km._judge_usage(NOW - 3600)
        self.assertEqual(u["total"]["calls"], 2)
        self.assertEqual(u["byJudge"]["planner"]["in"], 70)

    def test_a_mid_append_partial_line_waits_for_its_newline(self):
        p = jd.STATE / "judge-usage.jsonl"
        p.write_text(self._row(NOW - 1800) + "\n")
        self.assertEqual(len(km._judge_usage_rows()), 1)
        with p.open("a") as fh:
            fh.write('{"t": ')                           # the writer is mid-append
        self.assertEqual(len(km._judge_usage_rows()), 1, "the fragment is left for the next read")
        with p.open("a") as fh:
            fh.write(str(NOW - 60) + ', "judge": "closer", "in": 1, "out": 1, "cost": 0, "ms": 1}\n')
        self.assertEqual(len(km._judge_usage_rows()), 2, "…and picked up whole once complete")

    def test_repointing_state_never_inherits_an_offset(self):
        (jd.STATE / "judge-usage.jsonl").write_text(self._row(NOW - 1800) + "\n")
        self.assertEqual(len(km._judge_usage_rows()), 1)
        td2 = tempfile.TemporaryDirectory()
        try:
            jd.STATE = pathlib.Path(td2.name)            # a different state dir (the sandbox trap)
            (jd.STATE / "judge-usage.jsonl").write_text(
                self._row(NOW - 300, judge="grouper") + "\n")
            rows = km._judge_usage_rows()
            self.assertEqual([r["judge"] for r in rows], ["grouper"], "fresh path → fresh parse")
        finally:
            jd.STATE = pathlib.Path(self.td.name)
            td2.cleanup()

    def test_truncation_resets_cleanly(self):
        p = jd.STATE / "judge-usage.jsonl"
        p.write_text(self._row(NOW - 1800) + "\n" + self._row(NOW - 900) + "\n")
        self.assertEqual(len(km._judge_usage_rows()), 2)
        p.write_text(self._row(NOW - 60) + "\n")         # rotated/truncated
        self.assertEqual(len(km._judge_usage_rows()), 1)


class JudgeUsageRollup(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = pathlib.Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def _write(self, rows):
        (jd.STATE / "judge-usage.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_empty_when_no_log(self):
        r = km._judge_usage(NOW - 3600)
        self.assertEqual(r["total"]["calls"], 0)
        self.assertEqual(r["byJudge"], {})
        self.assertEqual(r["byTier"], {})

    def test_rolls_up_total_byjudge_bytier_and_windows(self):
        self._write([
            {"t": NOW - 100, "judge": "captioner", "tier": "index", "in": 10, "out": 5, "cost": 0.01, "ms": 800},
            {"t": NOW - 50, "judge": "captioner", "tier": "index", "in": 20, "out": 8, "cost": 0.02, "ms": 900},
            {"t": NOW - 30, "judge": "planner", "tier": "triage", "in": 100, "out": 40, "cost": 0.3, "ms": 2500},
            {"t": NOW - 99999, "judge": "planner", "tier": "triage", "in": 999, "out": 999, "cost": 9.9, "ms": 9999},
        ])
        r = km._judge_usage(NOW - 3600)
        self.assertEqual(r["total"]["calls"], 3, "the out-of-window row is dropped")
        self.assertEqual(r["total"]["in"], 130)
        self.assertEqual(r["total"]["out"], 53)
        self.assertAlmostEqual(r["total"]["cost"], 0.33)
        self.assertEqual(r["byJudge"]["captioner"]["calls"], 2)
        self.assertEqual(r["byJudge"]["captioner"]["in"], 30)
        self.assertEqual(r["byJudge"]["planner"]["in"], 100)
        self.assertEqual(r["byTier"]["index"]["calls"], 2)
        self.assertEqual(r["byTier"]["triage"]["in"], 100)

    def test_garbled_lines_are_skipped(self):
        (jd.STATE / "judge-usage.jsonl").write_text(
            '{"t":%d,"judge":"closer","tier":"triage","in":5,"out":2}\nnot json\n' % NOW)
        r = km._judge_usage(NOW - 3600)
        self.assertEqual(r["total"]["calls"], 1)
        self.assertEqual(r["byJudge"]["closer"]["out"], 2)


class AttachRunUsage(unittest.TestCase):
    """_attach_run_usage greedily matches each judging mark to the judge's nearest real call in
    judge-usage.jsonl (same fsid+judge), so a band block's tooltip can sum members' ms + tokens."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = pathlib.Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_matches_marks_to_nearest_runs_same_session(self):
        (jd.STATE / "judge-usage.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"t": NOW - 95, "judge": "captioner", "fsid": "S1", "ms": 800, "in": 10, "out": 5},
            {"t": NOW - 45, "judge": "captioner", "fsid": "S1", "ms": 900, "in": 20, "out": 8},
            {"t": NOW - 40, "judge": "captioner", "fsid": "S2", "ms": 700, "in": 30, "out": 9},
        ]) + "\n")
        judging = [
            {"judge": "captioner", "sid": "S1", "t": NOW - 100, "kind": "segment", "text": "a"},
            {"judge": "captioner", "sid": "S1", "t": NOW - 50, "kind": "turn", "text": "b"},
            {"judge": "planner", "sid": "S1", "t": NOW - 50, "kind": "mint", "text": "c"},
        ]
        km._attach_run_usage(judging, NOW - 3600, {"S1", "S2"})
        self.assertEqual((judging[0]["ms"], judging[0]["in"], judging[0]["out"]), (800, 10, 5))
        self.assertEqual((judging[1]["ms"], judging[1]["in"], judging[1]["out"]), (900, 20, 8), "each run consumed once")
        self.assertEqual((judging[2]["ms"], judging[2]["in"], judging[2]["out"]), (0, 0, 0), "planner mark unmatched → zeros")

    def test_no_log_leaves_zeros(self):
        judging = [{"judge": "captioner", "sid": "S1", "t": NOW, "kind": "segment", "text": "x"}]
        km._attach_run_usage(judging, NOW - 3600, {"S1"})
        self.assertEqual((judging[0]["ms"], judging[0]["in"], judging[0]["out"]), (0, 0, 0))
        self.assertEqual((judging[0]["sent"], judging[0]["recv"]), (None, None), "no log → no API times")

    def test_attaches_literal_api_sent_recv_to_the_matched_mark(self):
        # The literal API call window (the user 2026-06-19): each judge-usage row carries `sent`/`recv`
        # floats (when the prompt went out / the response came back). _attach_run_usage copies them onto the
        # matched mark so the band's hover can show the judge's REAL run interval, distinct from the
        # work-time the mark sits at. An unmatched mark keeps None.
        (jd.STATE / "judge-usage.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"t": NOW - 44, "judge": "distiller", "fsid": "S1", "ms": 4200, "in": 50, "out": 12,
             "sent": NOW - 48.6, "recv": NOW - 44.4},
        ]) + "\n")
        judging = [{"judge": "distiller", "sid": "S1", "t": NOW - 46, "kind": "distill", "text": "k"},
                   {"judge": "distiller", "sid": "S1", "t": NOW - 900, "kind": "distill", "text": "old"}]
        km._attach_run_usage(judging, NOW - 3600, {"S1"})
        self.assertEqual((judging[0]["sent"], judging[0]["recv"]), (NOW - 48.6, NOW - 44.4),
                         "the matched mark carries the literal API send/response wall-clock")
        self.assertEqual(judging[0]["ms"], 4200)
        self.assertEqual((judging[1]["sent"], judging[1]["recv"]), (None, None), "far-off mark unmatched → None")


class TokenAnalytics(unittest.TestCase):
    """_token_analytics: ONE arbitrary window (the analytics modal's period picker) → the coding
    SESSIONS total vs the judge pipeline broken out per judge AND per tier. discover() supplies the
    session fleet; jd.STATE points at a temp judge-usage.jsonl."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state, self.saved_discover = jd.STATE, jd.discover
        self.saved_cfg, self.saved_refresh = km.PRICE_CONFIG, km._refresh_remote_prices
        jd.STATE = pathlib.Path(self.td.name)
        km.PRICE_CONFIG = pathlib.Path(self.td.name) / "no-prices.json"   # nonexistent → defaults only
        km._refresh_remote_prices = lambda now: None                     # no network in tests
        km._ANALYTICS_MEMO.clear()                                       # the TTL memo must not leak across tests
        km._JUDGE_USAGE_CACHE.update(path=None, size=-1, mtime=0.0, rows=[])

    def tearDown(self):
        jd.STATE, jd.discover = self.saved_state, self.saved_discover
        km.PRICE_CONFIG, km._refresh_remote_prices = self.saved_cfg, self.saved_refresh
        self.td.cleanup()

    def test_window_splits_sessions_vs_per_judge_and_tier(self):
        p1 = pathlib.Path(self.td.name) / "s1.jsonl"
        p1.write_text(_asst({"input_tokens": 100, "output_tokens": 20}, iso(NOW - 1800)) + "\n" +     # in window
                      _asst({"input_tokens": 999, "output_tokens": 999}, iso(NOW - 99999)) + "\n")     # outside → dropped
        p2 = pathlib.Path(self.td.name) / "s2.jsonl"
        p2.write_text(_asst({"input_tokens": 30, "output_tokens": 8}, iso(NOW - 600)) + "\n")
        jd.discover = lambda now, window=None, forks=True: [("fs1", p1, "a1", "s1"), ("fs2", p2, "a2", "s2")]
        (jd.STATE / "judge-usage.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"t": NOW - 900, "judge": "captioner", "tier": "index", "in": 10, "out": 4, "cost": 0.01, "ms": 50},
            {"t": NOW - 800, "judge": "archiver", "tier": "index", "in": 6, "out": 2, "cost": 0.01, "ms": 40},
            {"t": NOW - 700, "judge": "planner", "tier": "triage", "in": 70, "out": 30, "cost": 0.2, "ms": 300},
            {"t": NOW - 50000, "judge": "planner", "tier": "triage", "in": 999, "out": 999, "cost": 9, "ms": 9},  # >1h → dropped
        ]) + "\n")
        a = km._token_analytics(NOW, 3600)
        self.assertEqual(a["window"], 3600)
        self.assertEqual((a["sessions"]["in"], a["sessions"]["out"]), (130, 28), "both sessions summed, windowed")
        self.assertEqual(a["judges"]["total"]["in"], 86, "10+6+70; the >1h planner call dropped")
        self.assertEqual(set(a["judges"]["byJudge"]), {"captioner", "archiver", "planner"})
        self.assertEqual(a["judges"]["byJudge"]["planner"]["out"], 30)
        self.assertEqual(a["judges"]["byTier"]["index"]["in"], 16, "captioner+archiver share the index tier")
        self.assertEqual(a["judges"]["byTier"]["triage"]["in"], 70)

    def test_empty_fleet_and_no_log_is_zero_but_shaped(self):
        jd.discover = lambda now, window=None, forks=True: []
        a = km._token_analytics(NOW, 86400)
        self.assertEqual((a["sessions"]["in"], a["sessions"]["out"], a["sessions"]["cost"]), (0, 0, 0.0))
        self.assertEqual(a["judges"]["total"]["calls"], 0)
        self.assertEqual(a["judges"]["byJudge"], {})


class CostWeighting(unittest.TestCase):
    """The cost-weighted analytics: SESSIONS priced tokens × _model_prices (defaults < remote feed <
    ~/.config override); JUDGES use claude's exact logged cost. The remote feed is monkeypatched off so
    these never touch the network."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_cfg, self.saved_refresh = km.PRICE_CONFIG, km._refresh_remote_prices
        self.saved_remote = dict(km._price_cache.get("remote", {}))
        self.saved_state, self.saved_discover = jd.STATE, jd.discover
        km.PRICE_CONFIG = pathlib.Path(self.td.name) / "model-prices.json"
        km._refresh_remote_prices = lambda now: None      # no network in tests → defaults/config only
        km._price_cache["remote"] = {}
        jd.STATE = pathlib.Path(self.td.name)
        km._ANALYTICS_MEMO.clear()                        # the TTL memo must not leak across tests
        km._JUDGE_USAGE_CACHE.update(path=None, size=-1, mtime=0.0, rows=[])

    def tearDown(self):
        km.PRICE_CONFIG, km._refresh_remote_prices = self.saved_cfg, self.saved_refresh
        km._price_cache["remote"] = self.saved_remote
        jd.STATE, jd.discover = self.saved_state, self.saved_discover
        self.td.cleanup()

    def test_price_for_exact_then_family_then_none(self):
        prices = {"claude-opus-4-8": {"in": 1, "out": 2, "cache_w": 3, "cache_r": 4}}
        self.assertEqual(km._price_for("claude-opus-4-8", prices)["out"], 2, "exact id")
        self.assertEqual(km._price_for("claude-opus-4-8-20990101", prices)["out"], 2, "same-family fallback")
        self.assertIsNone(km._price_for("some-other-model", prices), "unknown family → uncounted")

    def test_fable_and_sonnet5_are_priced_not_uncounted(self):
        # Regression: both signed as None AND matched no family, so their tokens costed $0 silently.
        prices = km._model_prices(NOW)
        for mid, rate in (("claude-fable-5", 50e-6), ("claude-sonnet-5", 15e-6)):
            row = km._price_for(mid, prices)
            self.assertIsNotNone(row, mid + " must be priced, not uncounted")
            self.assertEqual(row["out"], rate, mid + " output rate")
        self.assertEqual(km._price_for("claude-fable-5", prices)["in"], 10e-6, "fable input is 2x opus")
        self.assertEqual(km._price_for("claude-opus-4-8", prices)["in"], 5e-6, "opus rate unchanged")

    def test_fable_5_1_has_its_own_row_with_the_quarter_rate_cache_read(self):
        # Before the row existed the family fallback handed Fable 5.1 Fable 5's $1/Mtok cache read — 4x
        # its list rate (Claude Code 2.1.261's catalog: tier_10_50_cache_read_0_25), and the remote feed
        # could never correct it because only exact (family, major, minor) signatures match.
        prices = km._model_prices(NOW)
        row = km._price_for("claude-fable-5-1", prices)
        self.assertEqual((row["in"], row["out"], row["cache_w"], row["cache_r"]), (10e-6, 50e-6, 12.5e-6, 0.25e-6))
        self.assertEqual(km._price_for("claude-fable-5", prices)["cache_r"], 1e-6, "Fable 5 keeps its own rate")
        self.assertEqual(km._price_sig("claude-fable-5-1"), ("fable", "5", "1"), "signs distinctly, so the feed can reach it")
        self.assertEqual(km._price_for("claude-fable-5-1-20261201", prices)["cache_r"], 0.25e-6,
                         "…and a dated id of the same model lands on the fable-5-1 row by signature, not on the first fable row")

    def test_price_sig_signs_single_number_ids_and_ignores_date_suffixes(self):
        self.assertEqual(km._price_sig("claude-opus-4-8"), ("opus", "4", "8"), "X-Y pair still signs")
        self.assertEqual(km._price_sig("claude-haiku-4-5-20251001"), ("haiku", "4", "5"), "date after a minor")
        self.assertEqual(km._price_sig("claude-sonnet-5"), ("sonnet", "5", None), "single-number id")
        self.assertEqual(km._price_sig("claude-fable-5"), ("fable", "5", None), "fable family")
        self.assertEqual(km._price_sig("claude-sonnet-5-20260101"), ("sonnet", "5", None), "date is not a minor")
        self.assertIsNone(km._price_sig("gpt-4o"), "non-Anthropic id")

    def test_remote_feed_can_reach_every_baked_in_model(self):
        # _refresh_remote_prices builds `want` from DEFAULT_MODEL_PRICES; an id that cannot sign is
        # invisible to the feed forever, which is how fable/sonnet-5 would have stayed stale.
        want = {km._price_sig(k): k for k in km.DEFAULT_MODEL_PRICES if km._price_sig(k)}
        self.assertEqual(len(want), len(km.DEFAULT_MODEL_PRICES),
                         "every baked-in model must sign, or the feed can never refresh it")

    def test_config_overrides_default_price(self):
        km.PRICE_CONFIG.write_text(json.dumps(
            {"claude-opus-4-8": {"in": 9e-6, "out": 40e-6, "cache_w": 1e-6, "cache_r": 1e-7}}))
        pr = km._model_prices(NOW)
        self.assertEqual(pr["claude-opus-4-8"]["in"], 9e-6, "config overrides the baked-in default")
        self.assertEqual(pr["claude-sonnet-4-6"]["out"], 15e-6, "an unconfigured model keeps its default")

    def test_session_cost_prices_per_message_model_and_all_token_classes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, dir=self.td.name) as f:
            f.write(_asst({"input_tokens": 1000, "output_tokens": 500,
                           "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 40000},
                          iso(NOW - 100), model="claude-opus-4-8") + "\n")
            f.write(_asst({"input_tokens": 9, "output_tokens": 9}, iso(NOW - 99999),
                          model="claude-opus-4-8") + "\n")     # outside the window → not priced
            path = f.name
        prices = {"claude-opus-4-8": {"in": 5e-6, "out": 25e-6, "cache_w": 6.25e-6, "cache_r": 0.5e-6}}
        cost = km._session_cost(path, NOW - 3600, prices)
        expected = 1000 * 5e-6 + 500 * 25e-6 + 2000 * 6.25e-6 + 40000 * 0.5e-6   # cache reads count too
        self.assertAlmostEqual(cost, expected, places=9)

    def test_analytics_carries_cost_both_sides(self):
        p1 = pathlib.Path(self.td.name) / "s1.jsonl"
        p1.write_text(_asst({"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 100000},
                            iso(NOW - 600), model="claude-opus-4-8") + "\n")
        jd.discover = lambda now, window=None, forks=True: [("fs1", p1, "a", "s1")]
        (jd.STATE / "judge-usage.jsonl").write_text(json.dumps(
            {"t": NOW - 500, "judge": "captioner", "tier": "index", "in": 10, "out": 4,
             "cost": 0.0123, "ms": 50}) + "\n")
        a = km._token_analytics(NOW, 3600)
        # sessions: priced from defaults (opus $5/$25/Mtok + $0.5/Mtok cache read)
        exp_sess = 1000 * 5e-6 + 200 * 25e-6 + 100000 * 0.5e-6
        self.assertAlmostEqual(a["sessions"]["cost"], exp_sess, places=9)
        self.assertEqual(a["sessions"]["in"], 1000)
        # judges: the exact logged cost, not a token estimate
        self.assertAlmostEqual(a["judges"]["total"]["cost"], 0.0123, places=9)
        self.assertAlmostEqual(a["judges"]["byJudge"]["captioner"]["cost"], 0.0123, places=9)


if __name__ == "__main__":
    unittest.main()
