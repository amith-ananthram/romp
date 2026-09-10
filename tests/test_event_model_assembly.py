#!/usr/bin/env python3
"""Golden-equivalence replay for the assembly cache (kernel/event_model.py `_assemble`).

The contract under test: parse_session THROUGH the assembly cache (folding appends) produces a
tree byte-identical to a from-scratch full parse, at EVERY prefix of every transcript. Two
independent module instances make that comparison honest — `emi` keeps its assembly cache across
steps (the live folding path) while `emr` has its cache cleared before every parse (always the
full path, which the golden suite already pins). Each scenario is replayed record by record and
the two trees are deep-compared at each step, so any divergence names the exact record that
caused it. The gate tests then trip every fast-path gate on purpose: equivalence must hold BY
DEMOTION (the fold refuses, the full path serves), pinned via the stats counters — and the one
clean-append case must actually FOLD, so the fast path can never silently rot into full-parsing
everything while the equivalence assertions stay green.

All scenarios are SYNTHETIC (placeholder UUIDs, invented text — per CLAUDE.md); the golden test
module's builders are reused so the replay corpus and the golden corpus cannot drift apart.

Run:    python3 tests/test_event_model_assembly.py
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the modules resolve their state root at import time; only
# pytest runs conftest's floor). The golden module is loaded FIRST for its scenario builders —
# it re-points XDG_STATE_HOME to its own tempdir, which our loads then inherit: still hermetic.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
G = load_source("em_golden_scenarios",
                     os.path.join(HERE, "test_event_model_golden.py"))
emi = load_source("em_incremental", os.path.join(SCRIPTS, "romp-event-model"))
emr = load_source("em_reference", os.path.join(SCRIPTS, "romp-event-model"))

SID, NOW, T0 = G.SID, G.NOW, G.T0
iso = G.iso


def _text_of(blocks):
    return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def _tree(x):
    return json.loads(json.dumps(x, sort_keys=True))


class Replay(unittest.TestCase):
    """Record-by-record replay: incremental == full at every step."""
    maxDiff = None

    def _step(self, path, kw, label):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()   # the reference NEVER folds — every call is a full parse
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc), "incremental diverged from full at %s" % label)

    def replay(self, records, states=None, postal=None, min_folds=None, exact_folds=None):
        """Write `records` one at a time, comparing trees at every prefix."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            kw = {"candidate_files": [str(path)], "states": states,
                  "postal_log": list(postal or []), "now": NOW}
            folds0 = emi._ASM_STATS["fold"]
            path.write_text("")
            for i, r in enumerate(records):
                with open(path, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                self._step(path, kw, "record %d/%d" % (i + 1, len(records)))
            if min_folds is not None:
                self.assertGreaterEqual(emi._ASM_STATS["fold"] - folds0, min_folds,
                                        "the fast path never engaged — every step full-parsed")
            if exact_folds is not None:
                self.assertEqual(emi._ASM_STATS["fold"] - folds0, exact_folds,
                                 "fold count drifted — a record kind silently demotes to full")

    def test_all_single_file_golden_scenarios_replay_equivalently(self):
        for name, (records_fn, sent) in G.SINGLE_FILE.items():
            with self.subTest(scenario=name):
                states = G.IDLE_STATES if name == "idle_atom" else None
                self.replay(records_fn(), states=states, postal=sent)

    def test_plain_streaming_replay_actually_folds(self):
        # the red-first pin for the whole feature: a clean append-only stream must take the
        # fast path on EVERY step after the first (equivalence alone would pass trivially if
        # steps demoted to full — an exact count catches a single record kind rotting)
        recs = G.scenario_author_kinds()
        self.replay(recs, postal=G.SENT_LOG, exact_folds=len(recs) - 1)

    def test_resume_lineage_replays_equivalently_across_files(self):
        # file A is static (identity-stable non-leaf), file B streams; the states rows carry
        # the recorded fork so the stitched walk crosses files on both paths
        with tempfile.TemporaryDirectory() as td:
            pa = Path(td) / (G.FSID_A + ".jsonl")
            pb = Path(td) / (G.FSID_B + ".jsonl")
            pa.write_text("\n".join(json.dumps(r) for r in G.scenario_resume_lineage_fileA()) + "\n")
            rows = [{"t": T0 + 500, "resumeFork": {"to": G.FSID_B, "from": G.FSID_A}}]
            kw = {"candidate_files": [str(pa), str(pb)], "states": rows,
                  "postal_log": [], "now": NOW}
            pb.write_text("")
            folds0 = emi._ASM_STATS["fold"]
            for i, r in enumerate(G.scenario_resume_lineage_fileB()):
                with open(pb, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                inc = emi.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                emr._ASM_CACHE.clear()
                ref = emr.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                self.assertEqual(_tree(ref), _tree(inc), "diverged at fileB record %d" % (i + 1))
            self.assertGreater(emi._ASM_STATS["fold"] - folds0, 0,
                               "multi-file sessions silently demote to permanent full parses")


class GarbledTimestamps(unittest.TestCase):
    """A malformed record must never eclipse the rest of the session (T210): a kept record whose
    timestamp does not parse used to reach segment_turns' (t, _seq) sort as t=None and TypeError
    the WHOLE parse — one corrupt line took down the session view for chat and judges alike. The
    fix fails toward SHOWING: ingest borrows the last good stamp seen in file order (the record's
    real neighbor), so the atom stays visible at a truthful adjacent time, counted and noted
    loudly. Skipping it instead would silently drop a possibly-real ask — the one fatal error."""
    maxDiff = None

    def _parse_both(self, path, kw):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc))
        return inc

    def _write(self, path, records):
        with open(path, "a") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def test_garbled_timestamp_never_takes_the_session_down(self):
        # the dispatch's two-record repro: second record's stamp is garbage — parse_session used
        # to TypeError in segment_turns' sort and the whole session view went down with it
        bad = G.aline(T0 + 10, "the reply the user must still see", "a1", "u1")
        bad["timestamp"] = "not-a-timestamp"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("a1", [a.get("uuid") for a in atoms],
                          "the garbled-stamp record was dropped — it must stay visible")
            a1 = next(a for a in atoms if a.get("uuid") == "a1")
            self.assertEqual(a1["t"], T0, "the borrow is the last good stamp in file order")

    def test_garbled_first_record_still_shows(self):
        bad = G.uline(T0, "opening ask", "u1")
        bad["timestamp"] = "garbage"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [bad, G.aline(T0 + 10, "answer", "a1", "u1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("u1", [a.get("uuid") for a in atoms])
            u1 = next(a for a in atoms if a.get("uuid") == "u1")
            self.assertEqual(u1["t"], 0, "no good stamp yet -> epoch zero, deterministically")

    def test_garbled_assistant_append_demotes_and_survives(self):
        # a garbled user/assistant stamp in the DELTA trips the existing ts gate (fold refuses,
        # full parse serves) — and the full parse must now survive it
        bad = G.aline(T0 + 20, "late reply", "a2", "a1")
        bad["timestamp"] = "###"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")])
            self._parse_both(path, kw)
            g0 = emi._ASM_STATS.get("g:ts", 0)
            self._write(path, [bad])
            ses = self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS.get("g:ts", 0), g0 + 1,
                             "a garbled delta stamp folds only the full emit can place — demote")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("a2", [a.get("uuid") for a in atoms])

    def test_garbled_system_record_folds_equivalently(self):
        # a system record (no ts gate) with a garbled stamp FOLDS — the ingest repair must give
        # fold and full parse the same borrowed time, or they diverge
        refusal = {"type": "system", "subtype": "model_refusal_fallback", "uuid": "s1",
                   "parentUuid": "a1", "timestamp": "junk", "content": "swapped models",
                   "originalModel": "m-one", "fallbackModel": "m-two"}
        tail = G.aline(T0 + 30, "from the fallback model", "a2", "s1")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")])
            self._parse_both(path, kw)
            f0 = emi._ASM_STATS["fold"]
            self._write(path, [refusal, tail])
            ses = self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["fold"], f0 + 1,
                             "the garbled system record must fold, not silently demote")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            s1 = next(a for a in atoms if a.get("uuid") == "s1")
            self.assertEqual(s1["t"], T0 + 10, "borrowed from its file neighbor, both paths")

    def test_garbled_tail_never_zeroes_the_watermark(self):
        # review-reproduced divergence (T210): the prepass watermark read the RAW stamp of the
        # chronologically-last record — a garbled tail (which borrows the max stamp and sorts
        # last) zeroed max_ppt, disarmed the g:ts gate, and a timestamp-regressed append FOLDED
        # into carried state a fresh full parse disagrees with. The watermark must read ts_of.
        bad = G.aline(T0 + 20, "tail with a rotten stamp", "aX", "a1")
        bad["timestamp"] = "rotten"
        regressed = G.uline(T0 + 5, "arrives stamped BEFORE the fold watermark", "u2", "aX")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1"), bad])
            self._parse_both(path, kw)
            g0 = emi._ASM_STATS.get("g:ts", 0)
            self._write(path, [regressed])
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS.get("g:ts", 0), g0 + 1,
                             "a regressed append after a garbled tail must DEMOTE — folding it "
                             "diverges (the watermark was zeroed by the raw-stamp read)")

    def test_garbled_spliced_prompt_still_shows(self):
        # a queued_command attachment is the WITNESS for a prompt the user actually typed
        # mid-turn: dropping it on a garbled stamp is the exact silent loss this repo calls
        # fatal — it must ride the repaired stamp like every other record (T210 review)
        att = G.attline(T0 + 15, "the mid-turn ask that must not vanish", "q1", "a1")
        att["timestamp"] = "mangled"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "start", "u1"),
                               G.aline(T0 + 10, "working", "a1", "u1", tools=("Bash",), stop="tool_use"),
                               att,
                               G.trline(T0 + 20, "tu_a1_0", "u2", "q1"),
                               G.aline(T0 + 30, "done", "a2", "u2")])
            ses = self._parse_both(path, kw)
            texts = [(_text_of(a["message"]["content"]) if a.get("message") else "")
                     for t in ses["turns"] for a in t["atoms"]]
            self.assertTrue(any("must not vanish" in x for x in texts),
                            "the spliced prompt was dropped on its garbled stamp")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            spliced = next(a for a in atoms if a.get("absorbed"))
            self.assertEqual(spliced["t"], T0 + 10, "borrowed from its file neighbor")

    def test_garbled_cut_record_still_trims_orphans(self):
        # the pending-cut orphan filter read the cut record's RAW stamp: garbled -> None ->
        # filter skipped -> the deleted prompt's salvaged reply stood alone in the chat (the
        # half-worked-delete shape). The filter must read the repaired stamp (T210 review).
        cut = G.aline(T0 + 10, "the reply being rolled back", "a1", "u1")
        cut["timestamp"] = "shredded"
        rows = [{"t": T0 + 30, "orphanReply": {"uuid": "zz9", "text": "landed after the cut",
                                               "ts": iso(T0 + 30)}}]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), cut,
                               G.uline(T0 + 20, "next", "u2", "a1")])
            kw = {"candidate_files": [str(path)], "states": rows, "postal_log": [], "now": NOW,
                  "leaf_override": "a1"}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertNotIn("zz9", [a.get("uuid") for a in atoms],
                             "an orphan landing AFTER the cut must be trimmed — the garbled cut "
                             "stamp disarmed the filter")

    def test_ts_repair_counts_distinct_records_not_parses(self):
        # a stable corrupt line must not inflate the counter on every full parse — it counts
        # CORRUPTION, not parse volume (T210 review)
        bad = G.aline(T0 + 10, "reply", "a1", "u1")
        bad["timestamp"] = "xxx"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emr._ASM_CACHE.clear()
            emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            c1 = emr._ASM_STATS.get("ts-repair", 0)
            emr._ASM_CACHE.clear()
            emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emr._ASM_STATS.get("ts-repair", 0), c1,
                             "the second full parse re-ingested the same corrupt line and "
                             "counted it again")

    def test_note_cap_rearms_instead_of_going_silent(self):
        import io, contextlib
        bad = G.uline(T0, "ask", "u1")
        bad["timestamp"] = "bad"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [bad, G.aline(T0 + 10, "answer", "a1", "u1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emr._TS_REPAIR_NOTED.clear()
            emr._TS_REPAIR_NOTED.update("stem%d" % i for i in range(64))   # cap reached
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertIn("timestamp", err.getvalue(),
                          "a full cap silenced every NEW file forever — it must re-arm")
            emr._TS_REPAIR_NOTED.clear()

    def test_ts_repair_is_counted_and_noted_loudly(self):
        import io, contextlib
        bad = G.aline(T0 + 10, "reply", "a1", "u1")
        bad["timestamp"] = "zzz"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad, G.uline(T0 + 20, "more", "u2", "a1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            c0 = emr._ASM_STATS.get("ts-repair", 0)
            emr._TS_REPAIR_NOTED.clear()
            emr._TS_REPAIRED_SEEN.clear()   # distinct-record counting — this file's uuids again
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertGreater(emr._ASM_STATS.get("ts-repair", 0), c0, "repairs ride the stats")
            self.assertIn("timestamp", err.getvalue(), "the repair is loud, never silent")
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(err2.getvalue(), "", "the note fires once per file, not per parse")


class GateDemotions(unittest.TestCase):
    """Each fast-path gate, tripped on purpose: equivalence must hold BY DEMOTION."""
    maxDiff = None

    def _parse_both(self, path, kw):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc))
        return inc

    def _stream_then(self, base_records, appended, expect_full=True):
        """Stream a clean base (folding), then append `appended` in ONE step and assert that
        step full-parsed (a gate demotion) — or folded, for the near-miss twin."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            path.write_text("")
            for r in base_records:
                with open(path, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                self._parse_both(path, kw)
            fulls0, folds0 = emi._ASM_STATS["full"], emi._ASM_STATS["fold"]
            with open(path, "a") as fh:
                for r in appended:
                    fh.write(json.dumps(r) + "\n")
            self._parse_both(path, kw)
            if expect_full:
                self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1,
                                 "the gate should have demoted this append to a full parse")
            else:
                self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1,
                                 "this append is the fast path's own case and must fold")

    def _base(self):
        return [G.uline(T0, "first ask", "u1"),
                G.aline(T0 + 10, "first reply", "a1", "u1")]

    def test_clean_append_folds(self):
        self._stream_then(self._base(),
                          [G.uline(T0 + 60, "second ask", "u2", "a1"),
                           G.aline(T0 + 70, "second reply", "a2", "u2")], expect_full=False)

    def test_rewritten_uuid_demotes(self):
        # a verbatim re-write (same uuid) rebinds last-write-wins index state — never folded
        self._stream_then(self._base(), [G.aline(T0 + 10, "first reply", "a1", "u1")])

    def test_resurrected_dangling_target_demotes(self):
        # u2's parent x9 exists nowhere (broken chain, kept); a record LANDING as x9 later must
        # full-parse — a fold would keep the broken/repaired shape while a fresh build rebinds
        base = self._base() + [G.uline(T0 + 30, "on a broken chain", "u2", "x9")]
        self._stream_then(base, [G.aline(T0 + 40, "the missing link lands", "x9", "a1")])

    def test_compact_boundary_demotes(self):
        self._stream_then(self._base(),
                          [G.compact_line(T0 + 100, "cb1", logical_parent="a1")])

    def test_compact_summary_demotes(self):
        self._stream_then(self._base(),
                          [G.compact_summary_line(T0 + 100, "cs1", parent="a1")])

    def test_repeated_promptid_across_appends_demotes(self):
        w1 = G.uline(T0 + 60, "<command-name>/usage</command-name>\n"
                              "<command-message>usage</command-message>\n"
                              "<command-args></command-args>", "w1", "a1")
        w1["promptId"] = "p77"
        so = G.uline(T0 + 61, "<local-command-stdout>tokens: plenty</local-command-stdout>",
                     "so1", "w1")
        so["promptId"] = "p77"
        self._stream_then(self._base() + [w1], [so])

    def test_repeated_promptid_on_plain_records_folds(self):
        # the ROUTINE shape: every record of a turn wears its prompt's id, so tool results
        # repeat it on nearly every real append — those must fold (unshaped, this gate
        # demoted 1863/1869 bursts on a live replay); only wrapper-family shapes and
        # adoptable-boundary episode pids can re-classify old atoms
        opener = G.uline(T0 + 60, "run the checks", "u2", "a1")
        opener["promptId"] = "p42"
        work = G.aline(T0 + 70, "running", "a2", "u2", tools=("Bash",), stop="tool_use")
        tr = G.trline(T0 + 80, "tu_a2_0", "u3", "a2")
        tr["promptId"] = "p42"
        done = G.aline(T0 + 90, "all green", "a3", "u3")
        self._stream_then(self._base() + [opener, work], [tr, done], expect_full=False)

    def test_skill_link_to_new_invocation_demotes(self):
        # the payload record references tu_skill_9 BEFORE any such invocation exists; when the
        # invocation appends, a full parse reclassifies the OLD record — the gate must fire
        payload = G.uline(T0 + 30, "skill instructions payload", "u2", "a1")
        payload["sourceToolUseID"] = "tu_skill_9"
        payload["isMeta"] = True
        inv = G.aline(T0 + 40, "invoking", "a2", "u2", tools=("Skill",))
        inv["message"]["content"][-1]["id"] = "tu_skill_9"
        self._stream_then(self._base() + [payload], [inv])

    def test_timestamp_regression_demotes(self):
        self._stream_then(self._base(),
                          [G.uline(T0 - 50, "typed earlier, landed later", "u2", "a1")])

    def test_unparseable_timestamp_demotes(self):
        # isMeta so the record emits no atom either way: a kept EMITTING record with a garbage
        # timestamp crashes segment_turns on main too (pre-existing, outside this diff) — the
        # gate's job is only that a ts-less record never poisons the chronological carry
        bad = G.uline(T0 + 60, "harness caveat echo", "u2", "a1")
        bad["timestamp"] = "not-a-time"
        bad["isMeta"] = True
        self._stream_then(self._base(), [bad])

    def test_interior_reparent_demotes(self):
        # the new leaf chains to u1 (an interior record), not the old leaf a1 — a rewind shape
        self._stream_then(self._base(), [G.uline(T0 + 60, "edited resend", "u2", "u1")])

    def test_api_error_spur_reparent_demotes(self):
        # the T209 geometry mid-stream: the CLI flushes buffered api_error records parented at
        # the turn's OPENER, and the next prompt chains onto that spur — leaf descent fails
        spur = [{"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 60),
                 "uuid": "e1", "parentUuid": "u1"},
                G.uline(T0 + 70, "next ask", "u2", "e1")]
        self._stream_then(self._base() + [G.aline(T0 + 20, "real reply", "a2", "a1")], spur)

    def test_pending_cut_bypasses_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = self._base() + [G.uline(T0 + 60, "second ask", "u2", "a1"),
                                   G.aline(T0 + 70, "second reply", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [],
                  "now": NOW, "leaf_override": "a1"}
            byp0 = emi._ASM_STATS["bypass"]
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["bypass"], byp0 + 1)

    def test_resume_link_change_demotes(self):
        # same transcript, but a resumeFork row lands between parses -> the links gate refuses
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in self._base()) + "\n")
            kw = {"candidate_files": [str(path)], "states": [], "postal_log": [], "now": NOW}
            self._parse_both(path, kw)
            fulls0 = emi._ASM_STATS["full"]
            kw["states"] = [{"t": T0 + 90, "resumeFork":
                             {"to": "cccccccc-0000-0000-0000-000000000000", "from": SID}}]
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)

    def test_truncated_rewrite_demotes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = self._base() + [G.uline(T0 + 60, "second ask", "u2", "a1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._parse_both(path, kw)
            fulls0 = emi._ASM_STATS["full"]
            path.write_text("\n".join(json.dumps(r) for r in recs[:2]) + "\n")   # shrink = rewrite
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)


class PostalHeal(unittest.TestCase):
    """A marker that missed the index at emit time re-authors once the log catches up — on the
    SERVE path (no transcript change), matching what a full parse with the same rows returns."""
    maxDiff = None

    def test_delivery_record_heals_on_serve(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.postal_line(T0 + 100, "ASK: bump the alpha", "u2", "a1"),
                    G.aline(T0 + 110, "ack peer", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertIsNone(trig["author"]["peer"])          # the miss, cached
            kw["postal_log"] = list(G.SENT_LOG)                # the log catches up; file unchanged
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["peer"], G.PEER)   # healed in place, no re-parse

    def test_absorbed_splice_heals_too(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            spliced = "ASK: bump the alpha\n<!-- romp-msg-id: %s -->" % G.MID
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 20, "working", "a1", "u1", tools=("Read",), stop="tool_use"),
                    G.attline(T0 + 40, spliced, "att1", "a1"),
                    G.aline(T0 + 90, "folded it in", "a2", "att1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            kw["postal_log"] = list(G.SENT_LOG)
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            ab = [a for t in inc["turns"] for a in t["atoms"] if a.get("absorbed")][0]
            self.assertEqual(ab["author"]["peer"], G.PEER)


class ServedTreesAreCallerOwned(unittest.TestCase):
    def test_mutating_a_served_tree_never_reaches_the_next_serve(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in [
                G.uline(T0, "opener", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")]) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            one = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            for t in one["turns"]:
                for a in t["atoms"]:
                    a["type"] = "vandalized"
                    a.pop("message", None)
            two = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            kinds = [a["type"] for t in two["turns"] for a in t["atoms"]]
            self.assertEqual(kinds, ["user", "assistant"])

    def test_eviction_is_one_at_a_time_never_a_clear(self):
        old_max = emi._ASM_CACHE_MAX
        emi._ASM_CACHE_MAX = 2
        try:
            emi._ASM_CACHE.clear()
            with tempfile.TemporaryDirectory() as td:
                paths = []
                for i in range(3):
                    p = Path(td) / ("%08d-1111-2222-3333-444444444444.jsonl" % i)
                    p.write_text(json.dumps(G.uline(T0, "ask %d" % i, "u1")) + "\n")
                    paths.append(p)
                kw = {"states": None, "postal_log": [], "now": NOW}
                for p in paths:
                    emi.parse_session(str(p), rompuuid=p.stem, candidate_files=[str(p)], **kw)
                self.assertEqual(len(emi._ASM_CACHE), 2)
                held = [k[0] for k in emi._ASM_CACHE]
                self.assertNotIn(os.path.realpath(str(paths[0])), held)
                self.assertIn(os.path.realpath(str(paths[2])), held)
        finally:
            emi._ASM_CACHE_MAX = old_max
            emi._ASM_CACHE.clear()



class FallbackPath(unittest.TestCase):
    """The correctness backstop the design leans on: a fold-machinery exception serves a plain
    full parse (equivalent output), warns loudly, pops the poisoned entry, and RECOVERS — the
    next parse rebuilds and the one after folds again."""
    maxDiff = None

    def test_a_fold_exception_serves_a_full_parse_loudly_and_recovers(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text(json.dumps(G.uline(T0, "opener", "u1")) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            with open(path, "a") as fh:
                fh.write(json.dumps(G.aline(T0 + 10, "ok", "a1", "u1")) + "\n")
            old_gates = emi._asm_gates
            emi._asm_gates = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic fold bug"))
            emi._ASM_WARNED[0] = False
            try:
                fb0 = emi._ASM_STATS["fallback"]
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                emr._ASM_CACHE.clear()
                ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                self.assertEqual(_tree(ref), _tree(inc))
                self.assertEqual(emi._ASM_STATS["fallback"], fb0 + 1)
                self.assertTrue(emi._ASM_WARNED[0])
                self.assertIn("assembly fold failed", err.getvalue())
                self.assertNotIn(os.path.realpath(str(path)),
                                 [k[0] for k in emi._ASM_CACHE])   # the poisoned entry is gone
            finally:
                emi._asm_gates = old_gates
            fulls0 = emi._ASM_STATS["full"]
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)      # rebuilds…
            with open(path, "a") as fh:
                fh.write(json.dumps(G.uline(T0 + 60, "next ask", "u2", "a1")) + "\n")
            folds0 = emi._ASM_STATS["fold"]
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1)      # …and folds again
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))


class HealThenFold(unittest.TestCase):
    def test_postal_and_transcript_catch_up_in_the_same_visit(self):
        # the heal runs before the fold in one _assemble visit: the log resolves an old miss
        # while new records append — both effects must land, matching a full parse exactly
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.postal_line(T0 + 100, "ASK: bump the alpha", "u2", "a1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            with open(path, "a") as fh:
                fh.write(json.dumps(G.aline(T0 + 110, "ack peer", "a2", "u2")) + "\n")
            kw["postal_log"] = list(G.SENT_LOG)
            folds0 = emi._ASM_STATS["fold"]
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1)   # healed on the FOLD path
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["peer"], G.PEER)

    def test_wrong_but_resolved_earlier_marker_still_heals(self):
        # the review's major catch: a QUOTED earlier marker resolves first, so the author has
        # peer set but names the wrong message — the heal must keep revisiting until the
        # TRAILING marker resolves, exactly as a fresh full parse would decide it
        mid2 = "1700000000.222_333.TESTHOST"
        body = ("fwd of my earlier note\n<!-- romp-msg-id: %s -->\n"
                "and the real ask\n<!-- romp-msg-id: %s -->" % (G.MID, mid2))
        row2 = {"t": T0 + 195, "ev": "sent", "id": mid2, "from": "feeddesign",
                "from_id": "77777777-6666-5555-4444-333333333333", "to_id": SID,
                "body": "ASK: the real one"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.uline(T0 + 200, body, "u2", "a1", ps=None),
                    G.aline(T0 + 210, "ack", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None,
                  "postal_log": list(G.SENT_LOG), "now": NOW}   # only the QUOTED marker resolves
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["mid"], G.MID)         # provisional: quoted marker won
            kw["postal_log"] = list(G.SENT_LOG) + [row2]           # the trailing row lands
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["mid"], mid2)          # re-decided, not latched


class StatesOnlyChange(unittest.TestCase):
    def test_idle_rows_growing_under_a_static_transcript_serve_equivalently(self):
        # idle/orphan overlays are synthesized OUTSIDE the cache on every call — a states-only
        # change must flow through the serve path without any transcript re-emit
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in [
                G.uline(T0, "opener", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")]) + "\n")
            kw = {"candidate_files": [str(path)], "states": [], "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            serves0 = emi._ASM_STATS["serve"]
            kw["states"] = list(G.IDLE_STATES)
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            self.assertEqual(emi._ASM_STATS["serve"], serves0 + 1)
            self.assertTrue(any(a["type"] == "idle"
                                for t in inc["turns"] for a in t["atoms"]))
if __name__ == "__main__":
    unittest.main(verbosity=2)
