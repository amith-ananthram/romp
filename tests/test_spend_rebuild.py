#!/usr/bin/env python3
"""`romp spend-rebuild` (2026-09-06): recount the spend ledger's TOKEN columns from the transcripts.

The recorder diffed the CLI's per-turn `usage` as if it were a running total (see sdk_backend._turn_usage),
so every ledger written before the fix under-counts tokens. The transcripts hold the API's own per-call
usage blocks; summed by local hour/day per session they ARE the columns the fixed recorder would have
written. Dollars and turn counts are the recorder's and must survive untouched. Synthetic everything:
placeholder sids, an invented project slug, a temp state dir, a temp Claude config dir."""
import json
import os
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
cli = load_source("romp_spend_rebuild_cli", os.path.join(BIN, "romp-spend-rebuild"))

WEB = "11111111-2222-3333-4444-555555555555"
API = "11111111-2222-3333-4444-666666666666"
NOTE = "11111111-2222-3333-4444-777777777777"    # a comment thread on WEB

U1 = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 300}
U2 = {"input_tokens": 5, "output_tokens": 50, "cache_read_input_tokens": 5300, "cache_creation_input_tokens": 20}
U3 = {"input_tokens": 2, "output_tokens": 7, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}


def _tok(u):
    return sum(u.values())


class SpendRebuild(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.state = root / "romp"
        (self.state / "sdk").mkdir(parents=True)
        self.claude = root / "claude"
        self.proj = self.claude / "projects" / "-tmp-notes-api"
        self.proj.mkdir(parents=True)
        # the calls sit three hours back, inside ONE local hour bucket, safely within "today or yesterday"
        self.t0 = time.time() - 3 * 3600
        self.t0 -= self.t0 % 3600 - 60          # :01 past the hour, so +0..+90s stays in the bucket
        self.hour = time.strftime("%Y-%m-%dT%H", time.localtime(self.t0))
        self.day = time.strftime("%Y-%m-%d", time.localtime(self.t0))

    def tearDown(self):
        self.td.cleanup()

    def _ts(self, offset):
        return datetime.fromtimestamp(self.t0 + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _transcript(self, sid, calls):
        """calls: [(offset_s, message id, usage, sidechain)] — plus a user record, which carries no usage."""
        lines = [json.dumps({"type": "user", "timestamp": self._ts(0), "message": {"role": "user", "content": "hello"}})]
        for off, mid, u, side in calls:
            lines.append(json.dumps({"type": "assistant", "timestamp": self._ts(off), "isSidechain": side,
                                     "message": {"id": mid, "role": "assistant", "model": "claude-x", "usage": u,
                                                 "content": [{"type": "text", "text": "ok"}]}}))
        (self.proj / (sid + ".jsonl")).write_text("\n".join(lines) + "\n")

    def _subagent(self, sid, agent, calls, nested=""):
        """A subagent lane's transcript under the session's sidecar folder — same record shape; `nested` puts it one level
        down (a workflow agent's workflows/wf_<id>/)."""
        d = self.proj / sid / "subagents" / nested if nested else self.proj / sid / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        for off, mid, u, side in calls:
            lines.append(json.dumps({"type": "assistant", "timestamp": self._ts(off), "isSidechain": side,
                                     "message": {"id": mid, "role": "assistant", "model": "claude-x", "usage": u,
                                                 "content": [{"type": "text", "text": "ok"}]}}))
        (d / (agent + ".jsonl")).write_text("\n".join(lines) + "\n")

    def _reg(self, sid, name, auth="key", thread_of=""):
        d = {"sid": sid, "name": name, "cwd": "/tmp/notes-api", "auth": auth}
        if thread_of:
            d["threadOf"] = thread_of
        (self.state / "sdk" / (sid + ".json")).write_text(json.dumps(d))

    def _ledger(self, days, hours):
        (self.state / "spend.json").write_text(json.dumps({"days": days, "hours": hours}))

    def _read(self):
        return json.loads((self.state / "spend.json").read_text())

    def _run(self, *extra):
        return cli.main(["--state-dir", str(self.state), "--claude-dir", str(self.claude), *extra])

    def _wrong_bucket(self):
        # what the mis-diffing recorder wrote for a two-call turn: the second call's figure only
        return {"usd": 1.5, "turns": 1, "tokIn": 5, "tokOut": 50, "tokCacheR": 300, "tokCacheW": 20,
                "key": {"usd": 1.5, "turns": 1, "tok": 375},
                "bySid": {WEB: {"usd": 1.5, "turns": 1, "tok": 375, "key": {"usd": 1.5, "turns": 1, "tok": 375}}}}

    def test_recounts_the_token_columns_and_leaves_dollars_and_turns_alone(self):
        # m1 twice = one message written as two streaming records (counted once); m3 is a subagent's call
        self._transcript(WEB, [(0, "m1", U1, False), (5, "m1", U1, False), (60, "m2", U2, False), (90, "m3", U3, True)])
        self._reg(WEB, "web")
        self._ledger({self.day: self._wrong_bucket()}, {self.hour: self._wrong_bucket()})
        self.assertEqual(self._run("--apply"), 0)
        d = self._read()
        want = (17, 157, 11300, 320)
        for kind, key in (("days", self.day), ("hours", self.hour)):
            b = d[kind][key]
            self.assertEqual((b["tokIn"], b["tokOut"], b["tokCacheR"], b["tokCacheW"]), want, kind)
            self.assertEqual((b["usd"], b["turns"]), (1.5, 1), "the recorder's dollars and turns are not this tool's to touch")
            self.assertEqual(b["key"]["tok"], sum(want))
            self.assertEqual((b["key"]["tokCacheR"], b["key"]["usd"], b["key"]["turns"]), (11300, 1.5, 1), "the keyed sub-count gains the split")
            self.assertEqual((b["bySid"][WEB]["tok"], b["bySid"][WEB]["key"]["tok"], b["bySid"][WEB]["usd"]), (sum(want), sum(want), 1.5))
        baks = list(self.state.glob("spend.json.bak-*"))
        self.assertEqual(len(baks), 1, "the previous ledger is kept beside the new one")
        self.assertEqual(json.loads(baks[0].read_text())["days"][self.day]["tokCacheR"], 300)

    def test_subagent_lanes_count_toward_their_session(self):
        # the Agent tool's lanes write their own transcripts under <sid>/subagents/; their calls are in the
        # recorder's modelUsage total (same CLI process), so the recount must read them too — they were
        # 45% of one measured day, and a recount without them still read as roughly half
        self._transcript(WEB, [(0, "m1", U1, False)])
        self._subagent(WEB, "agent-a1", [(20, "s1", U2, True), (40, "s2", U3, True)])
        self._reg(WEB, "web")
        self._ledger({self.day: self._wrong_bucket()}, {self.hour: self._wrong_bucket()})
        self.assertEqual(self._run("--apply"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["tokCacheR"], 5000 + 5300 + 1000)
        self.assertEqual(h["bySid"][WEB]["tok"], _tok(U1) + _tok(U2) + _tok(U3), "lanes bill the session that ran them")

    def test_a_workflow_agents_nested_lane_counts_too(self):
        # Claude Code 2.1.261 writes a workflow agent's transcript under subagents/workflows/wf_<id>/; the flat glob
        # missed every one, so a recount under-billed exactly the sessions that ran workflows (T355)
        self._transcript(WEB, [(0, "m1", U1, False)])
        self._subagent(WEB, "agent-a1", [(20, "s1", U2, True)])
        self._subagent(WEB, "agent-a2", [(40, "s2", U3, True)], nested="workflows/wf_0123456789abcdef")
        self._reg(WEB, "web")
        self._ledger({self.day: self._wrong_bucket()}, {self.hour: self._wrong_bucket()})
        self.assertEqual(self._run("--apply"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["bySid"][WEB]["tok"], _tok(U1) + _tok(U2) + _tok(U3), "the nested lane bills its session too")

    def test_a_dry_run_writes_nothing(self):
        self._transcript(WEB, [(0, "m1", U1, False), (60, "m2", U2, False)])
        self._reg(WEB, "web")
        self._ledger({self.day: self._wrong_bucket()}, {self.hour: self._wrong_bucket()})
        before = (self.state / "spend.json").read_text()
        self.assertEqual(self._run(), 0)
        self.assertEqual((self.state / "spend.json").read_text(), before)
        self.assertEqual(list(self.state.glob("spend.json.bak-*")), [])

    def test_login_sessions_stay_out_of_the_key_sub_count_and_a_thread_bills_its_owner(self):
        self._transcript(WEB, [(0, "m1", U1, False)])
        self._transcript(API, [(10, "m2", U2, False)])       # a login session: counts in the total, not the key
        self._transcript(NOTE, [(20, "m3", U3, False)])      # a comment thread on WEB: its tokens are WEB's
        self._reg(WEB, "web")
        self._reg(API, "api", auth="login")
        self._reg(NOTE, "note", thread_of=WEB)
        b = {"usd": 2.0, "turns": 2, "tokIn": 0, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0,
             "key": {"usd": 1.0, "turns": 1, "tok": 0},
             "bySid": {WEB: {"usd": 1.0, "turns": 1, "tok": 0, "key": {"usd": 1.0, "turns": 1, "tok": 0}},
                       API: {"usd": 1.0, "turns": 1, "tok": 0}}}
        self._ledger({self.day: dict(b)}, {self.hour: dict(b)})
        self.assertEqual(self._run("--apply"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["tokCacheR"], 5000 + 5300 + 1000, "every session's calls make the bucket total")
        self.assertEqual(h["key"]["tok"], _tok(U1) + _tok(U3), "the keyed sub-count: web plus its thread, never the login session")
        self.assertEqual(h["bySid"][WEB]["tok"], _tok(U1) + _tok(U3), "the thread's calls land on its owner")
        self.assertNotIn(NOTE, h["bySid"])
        self.assertEqual(h["bySid"][API]["tok"], _tok(U2))
        self.assertNotIn("key", h["bySid"][API], "a login session gains no keyed split")

    def test_a_missing_bucket_is_added_only_inside_the_span_the_ledger_covers(self):
        # a turn's calls can straddle an hour edge while its result lands in one bucket: the hour after
        # the ledger's bucket gets its calls (zero dollars, zero turns); a call ten days before the
        # ledger begins does not stretch the ledger's history
        self._transcript(WEB, [(0, "m1", U1, False), (3600, "m2", U2, False), (-10 * 86400, "m0", U3, False)])
        self._reg(WEB, "web")
        self._ledger({self.day: self._wrong_bucket()}, {self.hour: self._wrong_bucket()})
        self.assertEqual(self._run("--apply"), 0)
        d = self._read()
        nxt = time.strftime("%Y-%m-%dT%H", time.localtime(self.t0 + 3600))
        self.assertEqual(set(d["hours"]), {self.hour, nxt})
        self.assertEqual((d["hours"][nxt]["usd"], d["hours"][nxt]["turns"], d["hours"][nxt]["tokCacheR"]), (0.0, 0, 5300))
        self.assertEqual(d["hours"][nxt]["key"]["tok"], _tok(U2), "the added bucket's keyed sub-count follows the session's auth")
        self.assertEqual(d["hours"][self.hour]["tokCacheR"], 5000)
        old_day = time.strftime("%Y-%m-%d", time.localtime(self.t0 - 10 * 86400))
        self.assertNotIn(old_day, d["days"])
        # the day bucket holds both calls of today (the straddling one is still today, or is the next day's)
        self.assertGreaterEqual(sum(int(v.get("tokCacheR") or 0) for v in d["days"].values()), 5000)

    def test_a_lower_recount_is_kept_as_recorded_unless_allowed(self):
        # Claude Code removes transcripts after its cleanup period: a bucket whose sessions' files are
        # gone recounts LOW. Lower is missing evidence, never a correction — kept, and said so — unless
        # --allow-lower asks for the lower figure anyway.
        self._transcript(WEB, [(0, "m1", U1, False)])         # 5410 tokens on disk…
        self._reg(WEB, "web")
        big = {"usd": 9.0, "turns": 4, "tokIn": 100, "tokOut": 100, "tokCacheR": 90000, "tokCacheW": 100,
               "key": {"usd": 9.0, "turns": 4, "tok": 90300}}   # …against a bucket that recorded far more
        self._ledger({self.day: dict(big)}, {self.hour: dict(big)})
        self.assertEqual(self._run("--apply"), 0)
        self.assertEqual(self._read()["hours"][self.hour], big, "kept byte-for-byte")
        self.assertEqual(list(self.state.glob("spend.json.bak-*")), [], "nothing changed, so nothing was written")
        self.assertEqual(self._run("--apply", "--allow-lower"), 0)
        self.assertEqual(self._read()["hours"][self.hour]["tokCacheR"], 5000)
        self.assertEqual(self._read()["hours"][self.hour]["usd"], 9.0)

    def test_a_session_whose_transcript_is_gone_keeps_its_recorded_tokens(self):
        # the never-lower rule holds PER SESSION (review find on #956, 2026-09-07): API's transcript is gone
        # while WEB's recount lifts the bucket total past the bar — the bucket used to pass and API's bySid
        # row (and its share of the key sub-count) was silently zeroed. Missing evidence for ANY recorded
        # session keeps the whole bucket, and says so.
        self._transcript(WEB, [(0, "m1", U1, False)])          # WEB recounts to 5410, above its recorded 100
        self._reg(WEB, "web"); self._reg(API, "api")
        b = {"usd": 2.0, "turns": 2, "tokIn": 10, "tokOut": 10, "tokCacheR": 100, "tokCacheW": 10,
             "key": {"usd": 2.0, "turns": 2, "tok": 130},
             "bySid": {WEB: {"usd": 1.0, "turns": 1, "tok": 100, "key": {"usd": 1.0, "turns": 1, "tok": 100}},
                       API: {"usd": 1.0, "turns": 1, "tok": 30, "key": {"usd": 1.0, "turns": 1, "tok": 30}}}}
        self._ledger({self.day: dict(b)}, {self.hour: dict(b)})
        self.assertEqual(self._run("--apply"), 0)
        self.assertEqual(self._read()["hours"][self.hour], b, "API has no evidence → the bucket is kept as recorded")
        self.assertEqual(self._run("--apply", "--allow-lower"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["bySid"][API]["tok"], 0, "--allow-lower takes the lower figure on purpose")

    def test_an_earlier_clear_episodes_transcript_is_counted(self):
        # a /clear mints a new fsid; the previous conversation stays under the old one, which the kernel
        # records in episodes/<sid>.jsonl — the rebuild must read it (review find on #956, 2026-09-07)
        old_fsid = "11111111-2222-3333-4444-aaaaaaaaaaaa"
        self._transcript(old_fsid, [(0, "m1", U1, False)])   # the pre-/clear episode
        self._transcript(WEB, [(30, "m2", U2, False)])       # the current one
        self._reg(WEB, "web")
        (self.state / "episodes").mkdir()
        (self.state / "episodes" / (WEB + ".jsonl")).write_text(json.dumps({"t": 1, "fsid": old_fsid}) + "\n")
        b = {"usd": 2.0, "turns": 2, "tokIn": 0, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0}
        self._ledger({self.day: dict(b)}, {self.hour: dict(b)})
        self.assertEqual(self._run("--apply"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["tokCacheR"], 5000 + 5300, "both episodes' calls count toward the session")
        self.assertEqual(h["bySid"][WEB]["tok"], _tok(U1) + _tok(U2))

    def test_keyed_ness_comes_from_the_ledgers_own_row_before_the_registrys_current_auth(self):
        # the registry holds only the CURRENT auth; an auth flip overwrote it and the rebuild then moved a
        # session's whole history across the key split. The recorder wrote bySid[sid].key exactly when the
        # sid billed the key in that bucket — that per-bucket truth wins (review find on #956, 2026-09-07).
        self._transcript(WEB, [(0, "m1", U1, False)])
        self._reg(WEB, "web", auth="login")                   # flipped to login SINCE this bucket was recorded
        b = {"usd": 1.0, "turns": 1, "tokIn": 0, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0,
             "key": {"usd": 1.0, "turns": 1, "tok": 0},
             "bySid": {WEB: {"usd": 1.0, "turns": 1, "tok": 0, "key": {"usd": 1.0, "turns": 1, "tok": 0}}}}
        self._ledger({self.day: dict(b)}, {self.hour: dict(b)})
        self.assertEqual(self._run("--apply"), 0)
        h = self._read()["hours"][self.hour]
        self.assertEqual(h["key"]["tok"], _tok(U1), "the bucket says WEB billed the key then; today's auth does not rewrite that")
        self.assertEqual(h["bySid"][WEB]["key"]["tok"], _tok(U1))

    def test_the_source_pins(self):
        src = open(os.path.join(os.path.dirname(HERE), "cli", "spend_rebuild.py")).read()
        self.assertIn("if mid in seen", src.replace("or mid in seen", "if mid in seen"), "streaming splits count once")
        self.assertIn('if r.get("type") != "assistant":', src, "only assistant records carry usage — no text is read")
        disp = open(os.path.join(BIN, "romp")).read()
        self.assertIn('if [[ "${1:-}" == "spend-rebuild" ]]; then', disp)
        self.assertIn('exec romp-spend-rebuild "$@"', disp)
        self.assertTrue(os.path.islink(os.path.join(BIN, "romp-spend-rebuild")))


if __name__ == "__main__":
    unittest.main()
