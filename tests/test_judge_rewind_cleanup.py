#!/usr/bin/env python3
"""Rewound-turn goals: the chain-membership predicate + the two mint seams (2026-08-17).

A conversation rewind abandons a transcript branch, but the goal cleanup was a one-shot t>=cut_t
sweep at gesture time while the judges kept parsing — and minting from — the abandoned tail:
during a bare rollback's armed window the judge parse had no leaf_override (unbounded re-mint),
and a producer pass framed before the rewind published its mints after the sweep (the late-mint
shape, proven live). The fix, pinned here:
  - em.chain_membership: THE exported membership predicate, built from the display parse's exact
    inputs (resume links + lineage closure + pending cut). "rewind" is the only sweepable verdict;
    "clear" is /clear jurisdiction, "broken" chains are kept, "eclipsed" chains are kept (a machine
    api_error spur's abandonment, T209 — never a user gesture), unknown uuids prove nothing.
  - jd.parsed_session honors the backend's pending cut (leaf_override), so an armed bare rollback
    stops yielding abandoned units at the source.
  - jd.apply_plan_guarded: the write-moment stand-down at every planner mint site — fresh,
    frame-independent inputs; RETIRES (placements[key]=None), never skips, so auto-nudge's
    placement gate can't wedge.
  - Task-store mirrors of abandoned-turn to-dos are LEFT AS-IS by decision (the task store is
    authoritative and a rewind does not roll it back) — pinned, not fixed.
SYNTHETIC fixtures only (placeholder uuids, TESTHOST-style invented text)."""
import json
import os
import tempfile
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
jd = load_source("romp_judge_rwclean", os.path.join(BIN, "romp-judge"))
em = jd.em

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000
CUT = T0 + 20            # u2's time: the edited/deleted record — everything at/after is abandoned


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def attline(t, uuid, parent, prompt="queued synthetic note"):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "attachment": {"type": "queued_command", "prompt": prompt}}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self._saved_state = jd.STATE
        (self.td / "state").mkdir()      # judge-errors.jsonl appends into it without mkdir -p
        jd._rebind_state(self.td / "state")
        jd.set_pending_cut_provider(None)
        jd.end_pass_frame(True)          # belt: never inherit a frame a crashed test left open
        self.path = self.td / (SID + ".jsonl")

    def tearDown(self):
        jd.set_pending_cut_provider(None)
        jd.end_pass_frame(True)
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._rebind_state(self._saved_state)

    def write(self, recs):
        self.path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def append(self, recs):
        with open(self.path, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def base_recs(self):
        """u1 -> a1 -> u2 -> a2: the tail (u2, a2) is what a rewind at u2 abandons."""
        return [uline(T0, "first synthetic ask", "u1"),
                aline(T0 + 10, "First synthetic reply, fully settled here.", "a1", "u1"),
                uline(CUT, "second synthetic ask", "u2", "a1"),
                aline(T0 + 30, "Second synthetic reply, also settled.", "a2", "u2")]

    def fork_recs(self):
        """The consumed rewind: u3 branches from a1, abandoning u2/a2."""
        return [uline(T0 + 60, "second ask, rewritten", "u3", "a1"),
                aline(T0 + 70, "Reply on the new branch, settled.", "a3", "u3")]

    def eclipse_recs(self):
        """T209's machine geometry: the CLI's buffered api_error spur roots at u2 (the turn's
        opener) and the next prompt chains onto it, abandoning a2 with no user gesture."""
        return [{"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 25),
                 "uuid": "e1", "parentUuid": "u2",
                 "error": {"message": "429 rate_limit_error (synthetic)"}},
                uline(T0 + 60, "third synthetic ask", "u3", "e1"),
                aline(T0 + 70, "Third synthetic reply.", "a3", "u3")]


def flush_orphan_recs():
    """The api_error-flush orphaning shape (2026-09-01, synthetic): the CLI recovered from a
    storm and persisted the reply (a2x), then flushed its buffered api_error records from the
    PRE-reply leaf (u2), hijacking the chain — the reply branch is bypassed on disk with no user
    gesture anywhere near the fork."""
    return [uline(T0, "first synthetic ask", "u1"),
            aline(T0 + 10, "First synthetic reply, fully settled here.", "a1", "u1"),
            uline(T0 + 20, "the stormed ask", "u2", "a1"),
            aline(T0 + 40, "Reply the flush bypassed on disk.", "a2x", "u2"),
            {"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 21), "uuid": "e1",
             "parentUuid": "u2", "level": "error", "retryAttempt": 1, "maxRetries": 10,
             "retryInMs": 1000, "source": "request_retry"},
            {"type": "system", "subtype": "stop_hook_summary", "timestamp": iso(T0 + 41),
             "uuid": "sh1", "parentUuid": "e1", "level": "suggestion"},
            uline(T0 + 60, "the ask after the storm", "u3", "sh1"),
            aline(T0 + 70, "Reply on the flushed spine.", "a3", "u3")]


class ChainMembershipPredicate(Base):
    """em.chain_membership — the ONE exported membership fact (identity-key hazards, per verdict)."""

    def test_a_rewound_fork_classifies_rewind(self):
        self.write(self.base_recs() + self.fork_recs())
        mem = em.chain_membership(self.path)
        self.assertEqual(mem["rewind"], {"u2", "a2"}, "the abandoned tail is the rewind set")
        self.assertTrue({"u1", "a1", "u3", "a3"} <= mem["kept"])

    def test_an_attachment_uuid_on_the_dead_branch_is_rewind(self):
        # hazard (a): an absorbed prompt's atom t is its ENQUEUE time (can predate cut_t), so the
        # t-keyed sweep misses it — but its uuid is a real spine node and the predicate catches it
        self.write(self.base_recs() + [attline(T0 + 5, "att1", "a2")] + self.fork_recs())
        mem = em.chain_membership(self.path)
        self.assertIn("att1", mem["rewind"], "a dead-branch attachment record is provably abandoned")

    def test_a_clear_branch_is_clear_jurisdiction_never_rewind(self):
        # hazard (d): /clear leaves a fresh null-parent head; the old chain reaches its own clean
        # null root — the episode machinery's turf, and sweeping it as a rewind would archive a
        # whole healthy prior conversation's cards
        self.write(self.base_recs() + [uline(T0 + 60, "fresh start after clear", "u3", None),
                                       aline(T0 + 70, "New conversation reply.", "a3", "u3")])
        mem = em.chain_membership(self.path)
        self.assertEqual(mem["rewind"], set())
        self.assertEqual(mem["clear"], {"u1", "a1", "u2", "a2"})

    def test_a_broken_chain_is_kept(self):
        # hazard (e): a parentUuid pointing at a uuid in NO transcript is unprovable — kept.
        # (Interleaved mid-file: the file's LAST record is the leaf, and the broken line must not
        # usurp that or the whole real chain would read pre-clear.)
        recs = self.base_recs()
        self.write(recs[:2] + [uline(T0 + 15, "stray line", "u9",
                                     "99999999-aaaa-bbbb-cccc-dddddddddddd")] + recs[2:])
        mem = em.chain_membership(self.path)
        self.assertIn("u9", mem["broken"])
        self.assertIn("u9", mem["kept"])
        self.assertNotIn("u9", mem["rewind"])

    def test_resume_fork_stitched_history_is_kept(self):
        # hazard (f): a recorded resume fork's fresh head is stitched to the from-file's last
        # record, so pre-fork history stays active — never "clear", never "rewind"
        frm = "22222222-3333-4444-5555-666666666666"
        fp_from = self.td / (frm + ".jsonl")
        fp_from.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "ask in the first file", "u1"),
            aline(T0 + 10, "Reply in the first file.", "a1", "u1")]) + "\n")
        self.write([uline(T0 + 60, "continues after the machine cut", "u3", None),
                    aline(T0 + 70, "Reply on the stitched chain.", "a3", "u3")])
        rows = [{"resumeFork": {"from": frm, "to": SID}, "t": T0 + 55}]
        mem = em.chain_membership(self.path, candidate_files=[str(self.path), str(fp_from)],
                                  states=rows)
        self.assertTrue({"u1", "a1", "u3", "a3"} <= mem["kept"], "stitched history stays kept")
        self.assertEqual(mem["rewind"], set())
        self.assertEqual(mem["clear"], set())

    def test_an_unknown_uuid_is_in_no_set(self):
        self.write(self.base_recs())
        mem = em.chain_membership(self.path)
        for k in ("kept", "rewind", "clear", "broken", "eclipsed"):
            self.assertNotIn("orphan:12345", mem[k], "a synthetic salvage id proves nothing")

    def test_an_eclipsed_branch_is_kept_never_rewind(self):
        # T209: the abandoned reply is the ONLY visible copy — it must classify eclipsed (kept),
        # and never enter the one sweepable set.
        self.write(self.base_recs() + self.eclipse_recs())
        mem = em.chain_membership(self.path)
        self.assertEqual(mem["eclipsed"], {"a2"}, "the machine-abandoned reply is eclipsed")
        self.assertIn("a2", mem["kept"], "eclipsed content is kept")
        self.assertEqual(mem["rewind"], set(), "an eclipse is never sweepable")

    def test_a_tail_spur_is_already_eclipsed_mid_flush(self):
        # adversarial-review finding on the first cut: with the spur as the transcript's TAIL
        # (a parse racing the CLI's multi-line flush, or a session dead mid-storm) the probe
        # exhausted into "rewind" — one-way goal archives in the race window, and the T209 eat
        # made permanent on the mid-storm death. An api_error on the spine out of the fork is
        # a machine artifact whatever follows.
        self.write(self.base_recs() + [
            {"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 25),
             "uuid": "e1", "parentUuid": "u2",
             "error": {"message": "429 rate_limit_error (synthetic)"}}])
        mem = em.chain_membership(self.path)
        self.assertEqual(mem["eclipsed"], {"a2"}, "the tail spur already eclipses, never sweeps")
        self.assertEqual(mem["rewind"], set())
        self.assert_parity_shape(mem)

    def test_a_cyclic_machine_spine_terminates_and_keeps(self):
        # adversarial-review finding on the first cut: a multi-node parent CYCLE of system
        # records (corruption this module's classify already anticipates with its own cycle
        # branch) closed the spine-child map and the probe looped forever — one corrupt
        # transcript hung every chat build and judge pass. The guard exits the cycle and the
        # machine-spine terminal keeps the branch (keep-on-unprovable, the module's bias).
        # the LEAF sits inside the cycle (cyC is the file's last uuid-bearing record), which
        # is what closes the spine-child map — a leaf outside it leaves an open chain and only
        # the exhaustion terminal fires
        recs = [{"type": "system", "subtype": "api_error", "timestamp": iso(T0 + i),
                 "uuid": u, "parentUuid": pu,
                 "error": {"message": "429 rate_limit_error (synthetic)"}}
                for i, (u, pu) in enumerate([("cyA", "cyC"), ("cyB", "cyA")])]
        recs.append(uline(T0 + 10, "ask rejoining the cycle", "ux", "cyA"))
        recs.append({"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 11),
                     "uuid": "cyC", "parentUuid": "cyB",
                     "error": {"message": "429 rate_limit_error (synthetic)"}})
        self.write(recs)
        mem = em.chain_membership(self.path)          # pre-guard: this call never returned
        self.assertIn("ux", mem["kept"], "a real ask off a corrupt machine spine is kept")

    def assert_parity_shape(self, mem):
        """kept == active ∪ broken ∪ eclipsed, from the same verdict sets we were handed."""
        self.assertTrue(mem["eclipsed"] <= mem["kept"])

    def test_a_pending_cut_moves_the_tail_into_rewind(self):
        # the armed bare-rollback window: nothing on disk yet, but the cut is the ground truth
        self.write(self.base_recs())
        full = em.chain_membership(self.path)
        self.assertEqual(full["rewind"], set())
        cut = em.chain_membership(self.path, leaf_override="a1")
        self.assertEqual(cut["rewind"], {"u2", "a2"})

    def test_a_flush_bypassed_reply_is_eclipsed_never_rewind(self):
        # the api_error-flush orphaning (2026-09-01): the bypass at the fork is the CLI's own
        # buffered-error chain — probed THROUGH the stop_hook_summary to the landed next
        # prompt — so the persisted reply rejoins "kept" via "eclipsed" and no sweep
        # predicate can ever read it as abandoned
        self.write(flush_orphan_recs())
        mem = em.chain_membership(self.path)
        self.assertEqual(mem["eclipsed"], {"a2x"})
        self.assertIn("a2x", mem["kept"], "eclipsed is a subset of kept")
        self.assertEqual(mem["rewind"], set())

    def test_the_sweep_predicates_spare_an_eclipsed_branch(self):
        # the fix's downstream face: _rewound_away is the write-moment mint stand-down AND the
        # drop-sweep discriminator; _per_file_rewound feeds the dead-branch reconciliation
        # (reconcile_rewound_goals unions it with mem["rewind"]) — before the eclipse, both
        # read the machine-orphaned branch as a rewind and goals anchored there were archived
        # by a sweep no user gesture ever justified
        self.write(flush_orphan_recs())
        self.assertFalse(jd._rewound_away(SID, str(self.path), "a2x"),
                         "an eclipsed uuid never stands a mint down")
        pf, fails = jd._per_file_rewound(SID, [str(self.path)])
        self.assertEqual(fails, 0)
        self.assertNotIn("a2x", pf, "the per-file discriminator agrees: nothing to sweep")
        # contrast, same predicates: a genuine user-gesture fork still answers durably rewound
        self.write(self.base_recs() + self.fork_recs())
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")


class PredicateParityGolden(Base):
    """Item 10: the exported helper vs the display parse's own adapter — byte-identical kept sets
    on every branching shape. Guards the four-divergent-helpers problem: any future second
    implementation of 'is this uuid on the chain' must fail here."""

    def _parse_side_kept(self, leaf, cands=None, states=None, leaf_override=None):
        """kept as parse_session computes it, via the SAME public pieces it now shares."""
        links = em.resume_fork_links(em._load_states(states))
        cands = em._lineage_closure(leaf, cands or [str(leaf)], links)
        ad = em.FileAdapter(cands, leaf, leaf_override=leaf_override, resume_links=links)
        return ad.kept_uuids(ad.active_path())

    def assert_parity(self, leaf, cands=None, states=None, leaf_override=None):
        mem = em.chain_membership(leaf, candidate_files=cands, states=states,
                                  leaf_override=leaf_override)
        self.assertEqual(mem["kept"], self._parse_side_kept(leaf, cands, states, leaf_override))

    def test_parity_on_a_plain_rewind_fork(self):
        self.write(self.base_recs() + self.fork_recs())
        self.assert_parity(self.path)

    def test_parity_on_an_eclipsed_machine_spur(self):
        # the parity guard bites on the eclipsed geometry too: kept_uuids and
        # chain_membership["kept"] must include the eclipsed branch IDENTICALLY (T209)
        self.write(self.base_recs() + self.eclipse_recs())
        self.assert_parity(self.path)
        mem = em.chain_membership(self.path)
        self.assertIn("a2", mem["kept"])

    def test_parity_on_a_flush_orphaned_branch(self):
        # the eclipse (probe + chain selection) lives inside chain_verdicts, so BOTH faces
        # (kept_uuids and chain_membership) inherit it from the one implementation — pinned anyway
        self.write(flush_orphan_recs())
        self.assert_parity(self.path)

    def test_parity_under_a_pending_cut(self):
        self.write(self.base_recs())
        self.assert_parity(self.path, leaf_override="a1")

    def test_parity_across_a_recorded_resume_fork_with_lineage(self):
        frm = "22222222-3333-4444-5555-666666666666"
        fp_from = self.td / (frm + ".jsonl")
        fp_from.write_text("\n".join(json.dumps(r) for r in self.base_recs()) + "\n")
        self.write([uline(T0 + 60, "continues after the machine cut", "u5", None),
                    aline(T0 + 70, "Stitched reply.", "a5", "u5")])
        rows = [{"resumeFork": {"from": frm, "to": SID}, "t": T0 + 55}]
        # the lineage closure must pull the from-file in even when the caller passes only the leaf
        self.assert_parity(self.path, cands=[str(self.path)], states=rows)
        mem = em.chain_membership(self.path, candidate_files=[str(self.path)], states=rows)
        self.assertIn("u1", mem["kept"], "the closure joined the resumed-from file")

    def test_parity_across_a_compaction_stitch(self):
        recs = self.base_recs() + [
            {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 40),
             "uuid": "cb1", "parentUuid": None, "logicalParentUuid": "gone-never-written",
             "compactMetadata": {"preservedSegment": {"tailUuid": "a2"}}},
            uline(T0 + 50, "post-compaction ask", "u3", "cb1"),
            aline(T0 + 60, "Post-compaction reply.", "a3", "u3")]
        self.write(recs)
        self.assert_parity(self.path)
        mem = em.chain_membership(self.path)
        self.assertIn("u1", mem["kept"], "the repaired stitch keeps pre-compaction history")


class JudgeParseHonorsPendingCut(Base):
    """Item 2 (first half): during an armed bare rollback the judge's parse is the CUT world, so
    plan_units never yields the abandoned turns at all — the orphan source closes where it opens.
    Fails on the pre-fix judge (parsed_session had no leaf_override; nothing lands on disk during
    the window, so every pass re-collected the deleted turn as live)."""

    def test_the_armed_window_hides_the_abandoned_tail_from_the_judges(self):
        self.write(self.base_recs())
        jd.set_pending_cut_provider(lambda sid: "a1" if sid == SID else "")
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        texts = json.dumps([a.get("message") for t in sess["turns"] for a in t["atoms"]])
        self.assertNotIn("second synthetic ask", texts, "the judge sees the cut world")
        store = {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
                 "placementsV": jd.PLACEMENTS_V}
        trigs = [u[6] for u in jd.plan_units(sess, store)]
        self.assertNotIn("u2", trigs, "no unit is ever collected from the abandoned tail")

    def test_the_cut_rides_the_parse_cache_key(self):
        # arming changes the parse with NO file change — a stale cache hit here would serve the
        # un-cut world for the whole window (the kernel _parse learned this on 2026-07-16)
        self.write(self.base_recs())
        full = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        jd.set_pending_cut_provider(lambda sid: "a1")
        cut = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(cut, full)
        texts = json.dumps([a.get("message") for t in cut["turns"] for a in t["atoms"]])
        self.assertNotIn("second synthetic ask", texts)
        jd.set_pending_cut_provider(None)
        back = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        texts = json.dumps([a.get("message") for t in back["turns"] for a in t["atoms"]])
        self.assertIn("second synthetic ask", texts, "clearing the cut busts the cache too")

    def test_a_broken_provider_is_loud_and_degrades_to_the_uncut_world(self):
        self.write(self.base_recs())
        def boom(sid):
            raise RuntimeError("synthetic provider failure")
        jd.set_pending_cut_provider(boom)
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        texts = json.dumps([a.get("message") for t in sess["turns"] for a in t["atoms"]])
        self.assertIn("second synthetic ask", texts, "degrades to pre-fix behavior, never blank")
        errs = jd.ERRORS.read_text()
        self.assertIn("pending-cut", errs, "…but never silently")


class WriteMomentStandDown(Base):
    """Items 1 + 2 (pinned-frame variant) + 7: apply_plan_guarded at the mint moment."""

    def _store(self):
        return {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
                "placementsV": jd.PLACEMENTS_V}

    def test_late_mint_after_the_sweep_stands_down(self):
        # Item 1, the g44 shape: the sweep ran at cut_t, THEN a pass framed pre-rewind applies a
        # unit whose prompt is on the (now consumed) abandoned branch. Pre-fix: the node mints,
        # survives forever, and the auto-nudge quotes it into the new conversation.
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        jd.save_goals(SID, s)
        jd.drop_goals_after(SID, CUT)                  # the one-shot sweep already ran (empty here)
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-dead", CUT,
                                        [{"do": "mint", "why": "x", "text": "orphan ask"}], [],
                                        prompt_uuid="u2")
        self.assertFalse(applied, "the guard stood down")
        self.assertEqual(s["nodes"], {}, "no orphan node exists")
        self.assertIn("seg-dead", s["placements"])
        self.assertIsNone(s["placements"]["seg-dead"], "RETIRED, not skipped")

    def test_pinned_frame_is_no_defense_the_guard_reads_fresh_inputs(self):
        # the pass frame pins the pre-rewind parse for the WHOLE pass; the guard must not consult it
        self.write(self.base_recs())
        self.assertTrue(jd.begin_pass_frame())
        jd.parsed_session(SID, [str(self.path)], T0 + 100)   # frame pins the un-cut world
        self.append(self.fork_recs())                        # the rewind lands mid-pass
        s = self._store()
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-dead", CUT,
                                        [{"do": "mint", "why": "x", "text": "orphan ask"}], [],
                                        prompt_uuid="u2")
        jd.end_pass_frame(True)
        self.assertFalse(applied, "fresh adapter walk sees the branch-take the frame hides")
        self.assertEqual(s["nodes"], {})

    def test_bare_rollback_window_stands_down_via_the_pending_cut(self):
        # during the armed window even a FRESH parse shows the dead tail as active — the guard
        # must fold in backend.pending_cut (item 2's second half). But PENDING-cut evidence is
        # not durable (the rewind can still fail/dissolve), so the stand-down DEFERS — the key
        # stays absent, never a permanent None retirement made on a rewind that never happened.
        self.write(self.base_recs())
        jd.set_pending_cut_provider(lambda sid: "a1")
        s = self._store()
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-dead", CUT,
                                        [{"do": "mint", "why": "x", "text": "orphan ask"}], [],
                                        prompt_uuid="u2")
        self.assertFalse(applied)
        self.assertEqual(s["nodes"], {})
        self.assertNotIn("seg-dead", s["placements"], "deferred, NOT retired — pending evidence")
        self.assertIn("rewind-stand-down-pending", jd.ERRORS.read_text(), "the deferral is loud")
        # the rollback dissolves (transcript unchanged, cut gone) → the same unit now mints
        jd.set_pending_cut_provider(None)
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-dead", CUT,
                                        [{"do": "mint", "why": "x", "text": "restored ask"}], [],
                                        prompt_uuid="u2")
        self.assertTrue(applied, "the dissolved rollback's ask still gets its card")
        self.assertEqual(len(s["nodes"]), 1)

    def test_a_consumed_rewind_on_disk_still_retires_durably(self):
        # the counterpart: once the branch-take is ON DISK the evidence can never un-happen, so
        # the guard retires exactly as before — even while a (new) cut is armed on the same session
        self.write(self.base_recs() + self.fork_recs())
        jd.set_pending_cut_provider(lambda sid: "a1")
        s = self._store()
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-dead", CUT,
                                        [{"do": "mint", "why": "x", "text": "orphan ask"}], [],
                                        prompt_uuid="u2")
        self.assertFalse(applied)
        self.assertIn("seg-dead", s["placements"], "durable on-disk evidence → retired")
        self.assertIsNone(s["placements"]["seg-dead"])

    def test_a_none_prompt_uuid_mints_unguarded(self):
        # abandonment can't be proven for an anchorless unit — the hard mint floor outranks suspicion
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-x", T0 + 60,
                                        [{"do": "mint", "why": "x", "text": "anchorless ask"}], [],
                                        prompt_uuid=None)
        self.assertTrue(applied)
        self.assertEqual(len(s["nodes"]), 1)

    def test_a_kept_prompt_mints_normally(self):
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        applied = jd.apply_plan_guarded(SID, str(self.path), s, "seg-new", T0 + 60,
                                        [{"do": "mint", "why": "x", "text": "new-branch ask"}], [],
                                        prompt_uuid="u3")
        self.assertTrue(applied)
        self.assertEqual(len(s["nodes"]), 1)

    def test_a_pending_cut_defers_the_unit_and_a_dissolved_rollback_mints_it(self):
        # Through _plan_session itself: a stand-down retirement made on PENDING-cut evidence was
        # permanent, but the cut is not — the CLI can refuse, the old branch can grow. The restore
        # leg brings back hidden CARDS, but an ask retired before its card existed had nothing to
        # restore: silently dropped forever (the repo's one fatal error). Pending evidence must
        # DEFER; the resolved world re-decides — dissolve → the ask mints, take → the tail stops
        # yielding units at all.
        self.write(self.base_recs())
        saved = (jd.plan_llm, jd.opener_llm)
        jd.plan_llm = jd.opener_llm = (
            lambda *a, **k: '{"ops":[{"why":"x","do":"mint","text":"Synthetic card"}]}')
        try:
            self.assertTrue(jd.begin_pass_frame())
            pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)   # framed BEFORE the gesture
            dead_keys = [jd._unit_key(u[0], u[1]) for u in jd.plan_units(pinned, self._store())
                         if u[6] == "u2"]
            self.assertTrue(dead_keys, "premise: the pinned world yields the doomed unit")
            jd.set_pending_cut_provider(lambda sid: "a1")   # the bare rollback arms MID-PASS
            jd._plan_session(SID, str(self.path), T0 + 100)
            jd.end_pass_frame(True)
            s = jd.load_goals(SID)
            for k in dead_keys:
                self.assertNotIn(k, s["placements"], "pending evidence defers — no permanent retirement")
            self.assertFalse(any(nd.get("promptUuid") == "u2" for nd in s["nodes"].values()),
                             "…and nothing minted from the maybe-dead branch")
            self.assertIn("rewind-stand-down-pending", jd.ERRORS.read_text(), "the deferral is loud")
            # the rollback DISSOLVES: transcript unchanged, cut gone — the next pass mints the ask
            jd.set_pending_cut_provider(None)
            jd._plan_session(SID, str(self.path), T0 + 200)
        finally:
            jd.plan_llm, jd.opener_llm = saved
        s = jd.load_goals(SID)
        self.assertTrue(any(nd.get("promptUuid") == "u2" for nd in s["nodes"].values()),
                        "the restored world's ask got its card after all")

    def test_retire_not_skip_keeps_the_nudge_gate_open(self):
        # Item 7: the kernel's _unplanned gate asks _placed_key of EVERY unit — a stood-down unit
        # left ABSENT reads pending forever and silences nudges for the whole session (the
        # documented 2026-07-27 wedge). The retired key must read placed.
        self.write(self.base_recs())
        self.assertTrue(jd.begin_pass_frame())
        pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)   # yields the doomed unit
        self.append(self.fork_recs())
        s = self._store()
        units = list(jd.plan_units(pinned, s))
        dead = [u for u in units if u[6] == "u2"]
        self.assertTrue(dead, "premise: the pinned world still yields the abandoned unit")
        for u in dead:
            key = jd._unit_key(u[0], u[1])
            self.assertFalse(jd.apply_plan_guarded(SID, str(self.path), s, u[0], u[2],
                                                   [{"do": "mint", "why": "x", "text": "t"}], [],
                                                   place_key=key, prompt_uuid=u[6]))
        jd.end_pass_frame(True)
        live = {sg["id"] for t in pinned["turns"] for sg in jd._segs(t, s)}
        unplanned = [u for u in dead
                     if not jd._placed_key(s["placements"], jd._unit_key(u[0], u[1]), live)]
        self.assertEqual(unplanned, [], "every stood-down unit reads placed — the gate is open")


class ChainMemo(Base):
    """The write-moment chain memo: _rewound_away answers an unchanged session without a second
    FileAdapter, and every input the adapter reads busts the memo — a
    transcript append, a states resumeFork row (by the states file's own stat, and by the lineage
    closure it grows), a from-file leaving that closure, the pending cut — and the key is taken
    before the build reads, so a write that lands mid-build is never sealed under it. A build that
    raises and a key that cannot be stat'd never memoize; reconcile_rewound_goals shares the on-disk
    slot; entries evict oldest-used at the cap and _rebind_state clears them."""

    def setUp(self):
        super().setUp()
        jd._CHAIN_MEMO.clear()
        self.built = []
        orig = em.FileAdapter.__init__

        def counting(ad, *a, **k):
            self.built.append(1)
            return orig(ad, *a, **k)
        self._orig_init = orig
        em.FileAdapter.__init__ = counting

    def tearDown(self):
        em.FileAdapter.__init__ = self._orig_init
        super().tearDown()

    def test_unchanged_files_build_one_adapter_and_agree(self):
        self.write(self.base_recs() + self.fork_recs())
        first = jd._rewound_away(SID, str(self.path), "u2")
        second = jd._rewound_away(SID, str(self.path), "u2")
        self.assertEqual((first, second), ("durable", "durable"))
        self.assertEqual(len(self.built), 1, "the second call served the memo")
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u3"), "a kept uuid, same memo")
        self.assertFalse(jd._rewound_away(SID, str(self.path), "nobody"), "an unknown uuid, same memo")
        self.assertEqual(len(self.built), 1)

    def test_a_transcript_append_invalidates_and_answers_fresh(self):
        self.write(self.base_recs())
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"))
        self.append(self.fork_recs())                            # the branch-take lands on disk
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 2)
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 2, "the new world is memoized in turn")

    def test_a_states_resume_fork_row_invalidates_and_answers_fresh(self):
        # the leaf is a fresh head; only the states row's lineage closure joins the from-file in
        # which u2 sits on a rewound branch, so the row alone must change the answer
        frm = "22222222-3333-4444-5555-666666666666"
        (self.td / (frm + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in self.base_recs() + self.fork_recs()) + "\n")
        self.write([uline(T0 + 100, "continues after the machine cut", "u5", None),
                    aline(T0 + 110, "Stitched reply.", "a5", "u5")])
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "no lineage: u2 is unknown")
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (SID + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": frm, "to": SID}, "t": T0 + 90}) + "\n")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable",
                         "the closure joined the from-file and u2 rejoins the stitched spine")
        self.assertEqual(len(self.built), 2)
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 2, "the stitched world is memoized too")

    def test_a_states_row_whose_from_file_is_already_the_anchor_invalidates_by_its_stat_alone(self):
        # the common first-hop shape: the SDK's resume fork records from=<the stable sid>, and
        # _judge_candidates already carries <sid>.jsonl as the anchor, so the row adds NOTHING to
        # the lineage closure — only the states file's (mtime, size) in the key moves, and that
        # has to be enough on its own
        self.write(self.base_recs() + self.fork_recs())                 # SID.jsonl: the anchor
        other = "33333333-4444-5555-6666-777777777777"
        leaf = self.td / (other + ".jsonl")
        leaf.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 100, "continues after the machine cut", "u5", None),
            aline(T0 + 110, "Stitched reply.", "a5", "u5")]) + "\n")
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        states = jd.STATESDIR / (SID + ".jsonl")
        states.write_text(json.dumps({"t": T0 + 80, "note": "an unrelated synthetic states row"}) + "\n")
        self.assertFalse(jd._rewound_away(SID, str(leaf), "u2"), "no link yet: u2 sits in the anchor's own graph")
        self.assertEqual(len(self.built), 1)
        with open(states, "a") as f:
            f.write(json.dumps({"resumeFork": {"from": SID, "to": other}, "t": T0 + 90}) + "\n")
        self.assertEqual(jd._rewound_away(SID, str(leaf), "u2"), "durable",
                         "the stitch re-points the fresh head at the anchor's tip, behind which u2 is rewound")
        self.assertEqual(len(self.built), 2, "the states stat alone busted the memo")

    def test_a_from_file_that_vanishes_invalidates_through_the_closure(self):
        # the lineage closure's own channel: the from-file is not a candidate and nothing writes
        # the states file, so neither the candidates' stats nor the states stat move — only the
        # closure (with the from-file stats it carries) can bust the key
        frm = "22222222-3333-4444-5555-666666666666"
        (self.td / (frm + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in self.base_recs() + self.fork_recs()) + "\n")
        self.write([uline(T0 + 100, "continues after the machine cut", "u5", None),
                    aline(T0 + 110, "Stitched reply.", "a5", "u5")])
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (SID + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": frm, "to": SID}, "t": T0 + 90}) + "\n")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 1)
        (self.td / (frm + ".jsonl")).unlink()
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "no from-file: u2 is unknown again")
        self.assertEqual(len(self.built), 2, "the closure lost a file and the key moved with it")
        self.assertFalse(jd.ERRORS.exists() and "chain-check" in jd.ERRORS.read_text(),
                         "a clean rebuild, not a failed one answering False")

    def test_the_key_is_taken_before_the_build_reads(self):
        # a rewind that lands DURING a build (after the key's stats, while the adapter reads) is
        # never sealed under that key: the next call re-stats, sees the append and rebuilds,
        # instead of serving a pre-append verdict as the post-append world's
        self.write(self.base_recs())
        orig = em.chain_membership

        def append_mid_build(*a, **k):
            out = orig(*a, **k)
            em.chain_membership = orig                          # once: the racing writer
            self.append(self.fork_recs())
            return out
        em.chain_membership = append_mid_build
        try:
            self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "built from the pre-append records")
        finally:
            em.chain_membership = orig
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable",
                         "the key predates the append, so the next call's stat misses and rebuilds")
        self.assertEqual(len(self.built), 2)

    def test_arming_and_clearing_the_cut_each_answer_fresh(self):
        self.write(self.base_recs())
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"))          # the on-disk build
        jd.set_pending_cut_provider(lambda sid: "a1")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "pending")
        self.assertEqual(len(self.built), 2, "the armed world is a second build; the durability "
                                             "check reads the on-disk slot already in the memo")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "pending")
        self.assertEqual(len(self.built), 2, "the armed world is memoized under its cut")
        jd.set_pending_cut_provider(None)
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "the dissolved rollback's ask is live again")
        self.assertEqual(len(self.built), 2, "clearing serves the on-disk slot: it never depended on the cut")
        jd.set_pending_cut_provider(lambda sid: "u1")                          # a different cut
        self.assertEqual(jd._rewound_away(SID, str(self.path), "a1"), "pending")
        self.assertEqual(len(self.built), 3, "another cut is another world")

    def test_the_on_disk_slot_is_built_only_when_a_cut_armed_check_needs_it(self):
        self.write(self.base_recs())
        jd.set_pending_cut_provider(lambda sid: "a1")
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u1"), "kept under the cut too")
        self.assertEqual(len(self.built), 1, "no durability question, no on-disk build")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "pending")
        self.assertEqual(len(self.built), 2, "the first rewind answer under the cut builds the on-disk graph")

    def test_a_build_that_raises_is_loud_and_never_memoized(self):
        self.write(self.base_recs() + self.fork_recs())
        orig, calls = em.chain_membership, []

        def boom(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("synthetic adapter failure")
            return orig(*a, **k)
        em.chain_membership = boom
        try:
            self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "pre-fix behavior: mint anyway")
            self.assertIn("chain-check", jd.ERRORS.read_text(), "the failure is loud")
            self.assertNotIn(SID, jd._CHAIN_MEMO, "a raised build leaves no entry")
            self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
            self.assertIn(SID, jd._CHAIN_MEMO)
        finally:
            em.chain_membership = orig

    def test_a_key_that_cannot_be_stat_ed_bypasses_the_memo(self):
        self.write(self.base_recs() + self.fork_recs())
        orig = jd._fileset_key

        def no_stat(files):
            raise OSError("synthetic stat failure")
        jd._fileset_key = no_stat
        try:
            before = jd.chain_memo_stats()
            self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable", "built fresh")
            self.assertNotIn(SID, jd._CHAIN_MEMO, "an unkeyable build is not memoized")
            self.assertEqual(jd.chain_memo_stats()["bypass"], before["bypass"] + 1)
        finally:
            jd._fileset_key = orig
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertIn(SID, jd._CHAIN_MEMO, "with the stat back, the next call memoizes")

    def test_reconcile_shares_the_on_disk_slot_both_ways(self):
        self.write(self.base_recs() + self.fork_recs())
        orig, calls = em.chain_membership, []

        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)
        em.chain_membership = counting
        try:
            jd._RECON_MEMO.clear()
            self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
            jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200)
            self.assertEqual(len(calls), 1, "the reconciliation read the memo's on-disk slot")
            jd._CHAIN_MEMO.clear()
            jd._RECON_MEMO.clear()
            jd.reconcile_rewound_goals(SID, str(self.path), T0 + 300)
            self.assertEqual(len(calls), 2)
            self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
            self.assertEqual(len(calls), 2, "the stand-down read what the reconciliation built")
        finally:
            em.chain_membership = orig

    def test_the_served_sets_are_immutable_and_the_dict_is_a_copy(self):
        self.write(self.base_recs() + self.fork_recs())
        mem = jd._chain_membership(SID, str(self.path), "")
        self.assertIsInstance(mem["rewind"], frozenset)
        mem["rewind"] = set()                                    # a caller scribbling on its copy
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 1)

    def test_the_memo_evicts_the_oldest_used_entry_at_the_cap(self):
        saved = jd._CHAIN_MEMO_MAX
        jd._CHAIN_MEMO_MAX = 2
        try:
            sids = ["%08d-1111-2222-3333-444444444444" % i for i in range(3)]
            paths = []
            for sid in sids:
                pth = self.td / (sid + ".jsonl")
                pth.write_text("\n".join(json.dumps(r) for r in self.base_recs() + self.fork_recs()) + "\n")
                paths.append(str(pth))
            jd._rewound_away(sids[0], paths[0], "u2")
            jd._rewound_away(sids[1], paths[1], "u2")
            jd._rewound_away(sids[0], paths[0], "u2")            # a hit: sids[0] is the hot entry
            jd._rewound_away(sids[2], paths[2], "u2")            # at the cap: the oldest-USED goes
            self.assertEqual(set(jd._CHAIN_MEMO), {sids[0], sids[2]})
        finally:
            jd._CHAIN_MEMO_MAX = saved

    def test_the_counters_report_hits_misses_and_populates(self):
        self.write(self.base_recs() + self.fork_recs())
        before = jd.chain_memo_stats()
        jd._rewound_away(SID, str(self.path), "u2")
        jd._rewound_away(SID, str(self.path), "u2")
        after = jd.chain_memo_stats()
        self.assertEqual([after[k] - before[k] for k in ("miss", "populate", "hit", "bypass")], [1, 1, 1, 0])

    def test_rebind_state_clears_the_memo(self):
        self.write(self.base_recs() + self.fork_recs())
        jd._rewound_away(SID, str(self.path), "u2")
        self.assertIn(SID, jd._CHAIN_MEMO)
        jd._rebind_state(self.td / "state")
        self.assertEqual(jd._CHAIN_MEMO, {})

    def test_a_from_file_rewritten_in_place_invalidates_by_its_own_stat(self):
        # the lineage from-files are frozen after their fork in practice, but the key stats them anyway:
        # a from-file that grows in place moves no candidate stat, no states stat and no closure
        # membership, so only its own (mtime, size) in the key can bust the memo
        frm = "22222222-3333-4444-5555-666666666666"
        fpath = self.td / (frm + ".jsonl")
        fpath.write_text("\n".join(json.dumps(r) for r in self.base_recs()) + "\n")
        self.write([uline(T0 + 100, "continues after the machine cut", "u5", None),
                    aline(T0 + 110, "Stitched reply.", "a5", "u5")])
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (SID + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": frm, "to": SID}, "t": T0 + 90}) + "\n")
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "the stitch hangs u5 under a2: u2 is kept")
        self.assertEqual(len(self.built), 1)
        with open(fpath, "a") as f:
            for r in self.fork_recs():                            # the from-file's tip is now a3: u2 rewound
                f.write(json.dumps(r) + "\n")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 2, "the from-file's stat alone busted the memo")

    def test_two_threads_that_miss_together_both_build_outside_the_lock(self):
        # the build runs outside _CHAIN_LOCK, which serializes the dict operations only: two callers
        # that miss together both build (the loser's populate is one wasted adapter walk, never the
        # judge pools stalled behind one builder holding the lock)
        import threading
        self.write(self.base_recs() + self.fork_recs())
        before = jd.chain_memo_stats()
        orig, broken, out = em.chain_membership, [], []
        barrier = threading.Barrier(2, timeout=10)              # the event: both builders inside the build at
        #                                                         once; the timeout only bounds the failure

        def meeting(*a, **k):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                broken.append(1)
            return orig(*a, **k)
        em.chain_membership = meeting
        try:
            ts = [threading.Thread(target=lambda: out.append(jd._rewound_away(SID, str(self.path), "u2")))
                  for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        finally:
            em.chain_membership = orig
        self.assertEqual(broken, [], "both builders were inside the build at once: neither held the lock there")
        self.assertEqual(out, ["durable", "durable"])
        self.assertEqual(len(self.built), 2)
        self.assertEqual(jd.chain_memo_stats()["populate"] - before["populate"], 2)
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 2, "the third call is a hit")

    def test_a_same_identity_rewrite_is_served_stale_until_the_memo_is_cleared(self):
        # WHY every test site that clears _PARSE_CACHE clears _CHAIN_MEMO beside it: both key on the
        # transcript's (mtime, size), so a fixture rewritten in place to the same byte count inside one
        # clock tick keeps its key, and a memo an earlier test populated serves the OLD verdict for the
        # new bytes without reading the file. Deterministic here: the mtime is pinned back with
        # os.utime, never left to the clock. em's records cache keys on the same identity and is
        # cleared beside the two, so the rebuild reads the new bytes and the chain memo's own
        # contribution is what the test isolates.
        recs = self.base_recs() + self.fork_recs()
        self.write(recs)
        want = self.path.stat().st_size
        st = os.stat(self.path)
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable")
        self.assertEqual(len(self.built), 1)
        # the same byte count with u2 LIVE: the fork rows go, a padded row under a2 makes up the length
        pad = want - len(("\n".join(json.dumps(r) for r in self.base_recs() + [uline(T0 + 60, "", "u9", "a2")]) + "\n").encode())
        self.assertGreaterEqual(pad, 0, "the filler row fits inside the original byte count")
        live = self.base_recs() + [uline(T0 + 60, "x" * pad, "u9", "a2")]
        self.write(live)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns))
        now = os.stat(self.path)
        self.assertEqual((now.st_mtime_ns, now.st_size), (st.st_mtime_ns, st.st_size), "precondition: same identity")
        self.assertEqual(jd._rewound_away(SID, str(self.path), "u2"), "durable",
                         "the hazard: the memo serves the pre-rewrite verdict for bytes under which u2 is live")
        self.assertEqual(len(self.built), 1, "...without a build, so nothing read the new bytes")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()          # the swept sites' form
        em._JSONL_CACHE.clear()                                  # the records cache under the adapter, same key
        self.assertFalse(jd._rewound_away(SID, str(self.path), "u2"), "cleared: rebuilt from the new bytes, u2 kept")
        self.assertEqual(len(self.built), 2)


class PlanSessionIntegration(Base):
    """The guards through _plan_session itself: a pass framed pre-rewind retires the dead units
    before any model call, and skips the plan-sync whose anchor died mid-pass; the next pass
    (fresh world) syncs normally. Also pins Path E (item 9): the task-store mirror re-mints with
    ON-CHAIN identity after a sweep — the task store is authoritative and a rewind does not roll
    it back (left as-is by decision)."""

    def setUp(self):
        super().setUp()
        self._saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        self.cfg = self.td / "claude-cfg"
        (self.cfg / "tasks" / SID).mkdir(parents=True)
        (self.cfg / "tasks" / SID / "1.json").write_text(json.dumps(
            {"id": "1", "subject": "synthetic open step", "status": "in_progress"}))
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)

    def tearDown(self):
        if self._saved_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._saved_cfg
        super().tearDown()

    def test_a_pass_framed_pre_rewind_retires_dead_units_and_defers_the_sync(self):
        self.write(self.base_recs())
        self.assertTrue(jd.begin_pass_frame())
        pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)   # frame pins the pre-rewind world
        dead_keys = [jd._unit_key(u[0], u[1]) for u in jd.plan_units(pinned, {
            "rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
            "placementsV": jd.PLACEMENTS_V}) if u[6] == "u2"]
        self.assertTrue(dead_keys, "premise: the pinned world yields the doomed unit")
        self.append(self.fork_recs())                                 # the rewind lands mid-pass
        jd._plan_session(SID, str(self.path), T0 + 100)
        jd.end_pass_frame(True)
        s = jd.load_goals(SID)
        for k in dead_keys:
            self.assertIn(k, s["placements"], "the dead unit was RETIRED (key present)")
            self.assertIsNone(s["placements"][k], "…as processed-no-goal")
        self.assertFalse(any(nd.get("promptUuid") == "u2" for nd in s["nodes"].values()),
                         "nothing minted from the abandoned branch")
        self.assertFalse(any(nd.get("agentTask") for nd in s["nodes"].values()),
                         "the plan-sync whose anchor died mid-pass deferred to the next pass")
        errs = jd.ERRORS.read_text()
        self.assertIn("rewind-stand-down", errs, "the stand-down is loud")
        # next pass, fresh world: the mirror mints with ON-CHAIN identity (Path E pinned as-is)
        jd._plan_session(SID, str(self.path), T0 + 200)
        s = jd.load_goals(SID)
        mirrors = [nd for nd in s["nodes"].values() if nd.get("agentTask")]
        self.assertEqual(len(mirrors), 1, "the open to-do re-mints — the task store is authoritative")
        self.assertEqual(mirrors[0].get("promptUuid"), "u3", "…anchored on the NEW branch")

    def test_path_e_pinned_a_swept_mirror_of_a_still_open_todo_remints(self):
        # Item 9 (assert-current): sweep the mirror, task store still holds the item open → the
        # next sync re-mints it. This is the decided behavior, not a bug being fixed.
        self.write(self.base_recs() + self.fork_recs())
        store = jd.load_goals(SID)
        session = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertTrue(jd._sync_declared_plan(store, session, "seg-sync", CUT, prompt_uuid="u2"))
        jd.save_goals(SID, store)
        self.assertEqual(jd.drop_goals_after(SID, CUT), 1, "the sweep archives the mirror")
        store = jd.load_goals(SID)
        self.assertTrue(jd._sync_declared_plan(store, session, "seg-sync2", T0 + 60,
                                               prompt_uuid="u3"))
        mirrors = [nd for nd in store["nodes"].values() if nd.get("agentTask")]
        self.assertEqual(len(mirrors), 1, "the mirror re-minted — the task store is authoritative")
        self.assertEqual(mirrors[0].get("promptUuid"), "u3", "…with post-rewind on-chain identity")


class ReconcileRewoundGoals(Base):
    """The state-keyed reconciliation (rides the triage cadence, event-gated on the abandoned-branch
    set changing): the ONLY cover for rewinds romp never sees — CLI-native Esc-Esc, the SDK forkAt
    resume, an unresolvable cut, a crash between arm and take. 28 live orphans existed when this
    shipped; one had been actively judged for a day after its conversation stopped existing."""

    def setUp(self):
        super().setUp()
        jd._RECON_MEMO.clear()

    def _store(self):
        return {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
                "placementsV": jd.PLACEMENTS_V}

    def _mint(self, s, seg, t, text, pu, parent_ops=None):
        jd.apply_plan(s, seg, t, parent_ops or [{"do": "mint", "why": "x", "text": text}],
                      jd.open_menu(s), prompt_uuid=pu)

    def test_the_g44_zombie_is_archived_so_no_nudge_precondition_survives(self):
        # Item 8, the end-to-end shape: a working top minted from a rewound-away turn + an idle
        # session on the new branch. Pre-fix every auto-nudge gate passed and _followup_body quoted
        # the orphan ("Still open on this:") into a conversation with no memory of the ask — twice,
        # live. The fire's precondition is a live still-'working' top; the reconciliation removes it.
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Zombie ask from the deleted turn", "u2")
        self._mint(s, "seg-live", T0 + 60, "Real ask on the new branch", "u3")
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        zombie, kept = "%s:g1" % SID, "%s:g2" % SID
        self.assertEqual(jd.load_goals(SID)["status"].get(zombie, "working"), "working",
                         "premise: the zombie is a nudgeable working top today")
        n = jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200)
        self.assertEqual(n, 1)
        live = jd.load_goals(SID)
        self.assertNotIn(zombie, live["nodes"], "the zombie left the live store — nothing to nudge")
        self.assertIn(kept, live["nodes"], "the new branch's card is untouched")
        self.assertIn(zombie, jd.load_goal_archive(SID)["nodes"], "archived, recoverable — not deleted")
        self.assertIn(zombie, live.get("rewindSwept", {}), "tombstoned against rebase resurrection")
        self.assertIn("rewound-archived", jd.ERRORS.read_text(), "the move is loud")

    def test_reconciliation_is_event_keyed_not_a_timer(self):
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Zombie", "u2")
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 1)
        rev = jd.load_goals(SID).get("rev")
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 201), 0,
                         "unchanged fileset → not an event, no adapter walk archives anything")
        self.append([uline(T0 + 90, "more work on the new branch", "u4", "a3"),
                     aline(T0 + 95, "More new-branch work, settled.", "a4", "u4")])
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 202), 0,
                         "fileset moved but the abandoned set did not → no new information")
        self.assertEqual(jd.load_goals(SID).get("rev"), rev, "…and the store was never re-published")
        self.append([uline(T0 + 120, "rewriting that", "u5", "a3"),
                     aline(T0 + 125, "Reply after the second rewind.", "a5", "u5")])
        s = jd.load_goals(SID)
        self._mint(s, "seg-dead2", T0 + 95, "Second zombie", "u4")
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 300), 1,
                         "a NEW abandoned branch (u4's) is the event that re-arms the check")

    def test_a_kept_anchored_child_under_a_dead_top_survives_the_drag_and_reparents(self):
        # The reconciliation fires days after the rewind, when a zombie top has accumulated REAL
        # live-branch descendants (the grouper files new work under existing tops — ~90 live goals,
        # 8 open, sat under one dead top on live data). The drag must stop at a child whose own
        # anchor is provably kept, and the survivor must stay reachable (re-parented, in open_menu).
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Zombie top from the deleted turn", "u2")
        top = "%s:g1" % SID
        jd.apply_plan(s, "seg-live", T0 + 60,
                      [{"do": "sub", "why": "x", "under": 1, "text": "Live work filed under it"}],
                      jd.open_menu(s), prompt_uuid="u3")
        child = "%s:g2" % SID
        self.assertEqual(s["nodes"][child]["parentId"], top, "premise: the live child sits under the zombie")
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 1,
                         "only the dead top moves — the kept-anchored child is spared")
        live = jd.load_goals(SID)
        self.assertNotIn(top, live["nodes"], "the zombie top archived")
        self.assertIn(child, live["nodes"], "the live-branch child stays")
        self.assertIsNone(live["nodes"][child].get("parentId"), "…re-parented at the nearest survivor (a top)")
        self.assertIn(child, [nd["id"] for nd in jd.open_menu(live)], "…and still reachable by the planner")

    def test_a_store_write_reopens_the_gate_for_an_already_known_dead_branch(self):
        # the escape the review named: reconcile memoizes the sig, then a mint slips past the
        # write-moment guard's fail-open onto that ALREADY-swept branch (a transient chain-check
        # error mid-pass mints anyway, loudly, by design). The transcript never changes again — so
        # a transcript-only gate could never re-catch it while the kernel stayed up: the g44 zombie
        # back, behind one transient fault. The mint's own store write IS the new information; the
        # gate watches both sides of the join.
        self.write(self.base_recs() + self.fork_recs())
        jd.save_goals(SID, self._store())
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 0,
                         "premise: the abandoned set is memoized, nothing to move yet")
        s = jd.load_goals(SID)
        self._mint(s, "seg-escape", CUT, "Escaped orphan", "u2")   # the fail-open mint: store-only
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 300), 1,
                         "the next pass archives it — no restart, no second rewind needed")
        self.assertNotIn("%s:g1" % SID, jd.load_goals(SID)["nodes"])
        self.assertIn("%s:g1" % SID, jd.load_goal_archive(SID)["nodes"])

    def test_a_merge_transplanted_dead_anchor_on_a_kept_survivor_is_not_swept(self):
        # hazard (b): _merge_nodes grafts the dupe's promptUuid onto a survivor lacking one — mixed
        # provenance proves nothing about the NODE, so it stays
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-x", T0, "Kept-origin survivor", "u2")
        nid = "%s:g1" % SID
        s["nodes"][nid]["mergedFrom"] = [{"id": "%s:g9" % SID, "text": "dead twin", "at": CUT}]
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 0)
        self.assertIn(nid, jd.load_goals(SID)["nodes"])

    def test_an_umbrella_parent_over_orphan_children_is_swept_with_them(self):
        # hazard (c): the umbrella has no promptUuid — the inverted subtree-drag direction
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-a", CUT, "Orphan child one", "u2")
        self._mint(s, "seg-b", CUT + 5, "Orphan child two", "u2")
        s["nodes"]["%s:umb" % SID] = jd.GuardedNode({
            "id": "%s:umb" % SID, "text": "Umbrella over dead work", "parentId": None,
            "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
            "t": CUT + 10, "mt": CUT + 10, "umbrella": True, "log": []})
        for gid in ("%s:g1" % SID, "%s:g2" % SID):
            s["nodes"][gid]["parentId"] = "%s:umb" % SID
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 3)
        live = jd.load_goals(SID)["nodes"]
        self.assertNotIn("%s:umb" % SID, live, "the empty shell went with its children")

    def test_an_enqueue_time_anchor_below_cut_t_is_still_swept(self):
        # hazard (a): an absorbed prompt's atom t is its ENQUEUE time — a dead-branch node can carry
        # t < cut_t and escape any t-keyed sweep forever; the identity key catches it
        self.write(self.base_recs() + [attline(T0 + 5, "att1", "a2")] + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-abs", T0 + 5, "Born from an absorbed prompt", "att1")
        jd.save_goals(SID, s)
        self.assertLess(s["nodes"]["%s:g1" % SID]["t"], CUT, "premise: t-keyed sweeps miss this node")
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 1)
        self.assertNotIn("%s:g1" % SID, jd.load_goals(SID)["nodes"])

    def test_clear_branch_and_broken_chain_nodes_are_untouched(self):
        # hazards (d) + (e): pre-/clear history is the episode machinery's jurisdiction; a broken
        # chain is unprovable — neither is ever swept as a rewind
        recs = self.base_recs()
        self.write(recs[:2] + [uline(T0 + 15, "dangling line", "u9",
                                     "99999999-aaaa-bbbb-cccc-dddddddddddd")] + recs[2:] +
                   [uline(T0 + 60, "fresh start after clear", "u3", None),
                    aline(T0 + 70, "New conversation reply.", "a3", "u3")])
        s = self._store()
        self._mint(s, "seg-pre", T0, "Pre-clear card", "u2")     # u2 is now pre-/clear, not rewound
        self._mint(s, "seg-brk", T0 + 15, "Broken-chain card", "u9")
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 0)
        self.assertEqual(len(jd.load_goals(SID)["nodes"]), 2, "both cards stay")

    def test_an_open_agent_task_pins_its_card_against_the_sweep(self):
        # Path E stays as-is: the live task store is authoritative — the agent may genuinely still
        # hold the to-do, and archiving would just re-mint a fresh mirror while losing the diary
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Mirror of a still-open to-do", "u2")
        nid = "%s:g1" % SID
        s["nodes"][nid]["agentTask"] = {"key": "1", "status": "open", "raw": "in_progress"}
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 0)
        self.assertIn(nid, jd.load_goals(SID)["nodes"])

    def test_a_dead_branch_inside_a_pre_clear_episode_file_is_still_reconciled(self):
        # The whole-graph walk can only ever call a pre-/clear file's interior branches "clear"
        # (their chains reach the old episode's null root; the current spine never sees them), so
        # 10 of the 28 audited live orphans — including ALL 5 live+archive dual-residents — were
        # invisible to it. The per-file discriminator walks each dead file by itself: a branch
        # that rejoins the file's OWN spine is a rewind wherever the graph later went. Benign
        # pre-clear spine nodes stay (they are "clear" in the whole graph AND on their own file's
        # spine), and so does the current branch's card.
        anchor = self.td / (SID + ".jsonl")            # the dead episode: a rewind INSIDE it, then /clear
        anchor.write_text("\n".join(json.dumps(r) for r in
                                    self.base_recs() + self.fork_recs()) + "\n")
        epfsid = "44444444-5555-6666-7777-888888888888"   # a second dead episode, enumerated only
        epfile = self.td / (epfsid + ".jsonl")            # by the episode log (u6b/a6b rewound away
        eprecs = [uline(T0 + 200, "old-episode ask", "u6"),  # inside it, u7 taking the branch)
                  aline(T0 + 210, "Old-episode reply, settled.", "a6", "u6"),
                  uline(T0 + 215, "follow-up on the old episode", "u6b", "a6"),
                  aline(T0 + 218, "Follow-up reply.", "a6b", "u6b"),
                  uline(T0 + 220, "follow-up, rewritten", "u7", "a6"),
                  aline(T0 + 230, "Branch reply.", "a7", "u7")]
        epfile.write_text("\n".join(json.dumps(r) for r in eprecs) + "\n")
        jd.append_episode(SID, "u6", epfsid, T0 + 200)
        leaf = self.td / ("99999999-aaaa-bbbb-cccc-dddddddddddd.jsonl")   # the CURRENT episode
        leaf.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 300, "fresh start after clear", "u9"),
            aline(T0 + 310, "New conversation reply.", "a9", "u9")]) + "\n")
        s = self._store()
        self._mint(s, "seg-a", CUT, "Orphan from the anchor's dead branch", "u2")
        self._mint(s, "seg-b", T0 + 215, "Orphan from the old episode's dead branch", "u6b")
        self._mint(s, "seg-c", T0, "Benign pre-clear card", "u1")
        self._mint(s, "seg-d", T0 + 300, "Current branch's card", "u9")
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(leaf), T0 + 400), 2,
                         "both interior dead branches are caught — nothing else")
        live = jd.load_goals(SID)["nodes"]
        self.assertNotIn("%s:g1" % SID, live, "the anchor's dead-branch orphan archived")
        self.assertNotIn("%s:g2" % SID, live, "the episode-log-enumerated file's orphan archived")
        self.assertIn("%s:g3" % SID, live, "a benign pre-clear spine card is untouched")
        self.assertIn("%s:g4" % SID, live, "the current branch's card is untouched")

    def test_a_user_restored_card_survives_boot_and_later_rewind_re_reconciles(self):
        # The restore pops the tombstone, but the branch stays "rewind" in the graph forever and
        # _RECON_MEMO is process memory: every kernel restart (memo reset) and any later unrelated
        # rewind (sig change) re-ran the full sweep and re-archived the card the user deliberately
        # brought back — a card move on ZERO new information. The restore's durable stamp stands
        # the node down from the identity-keyed sweep for good.
        self.write(self.base_recs() + self.fork_recs())
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Zombie the user wants kept", "u2")
        jd.save_goals(SID, s)
        nid = "%s:g1" % SID
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 1)
        arch = jd.load_goal_archive(SID)               # the user restores it (the undo-clear moves:
        payload = dict(arch["nodes"].pop(nid))         # out of the archive, journaled, replayed)
        jd.save_goal_archive(SID, arch)
        jd.append_restore(SID, {nid: payload}, {}, T0 + 300)
        live = jd.load_goals(SID)
        self.assertIn(nid, live["nodes"], "premise: the restore landed")
        jd.save_goals(SID, live)
        jd._RECON_MEMO.clear()                         # a kernel restart resets the event gate
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 400), 0,
                         "the boot re-check moves nothing — the restore is durable")
        self.assertIn(nid, jd.load_goals(SID)["nodes"])
        self.append([uline(T0 + 90, "more work on the new branch", "u4", "a3"),
                     aline(T0 + 95, "More new-branch work, settled.", "a4", "u4"),
                     uline(T0 + 120, "rewriting that", "u5", "a3"),
                     aline(T0 + 125, "Reply after the second rewind.", "a5", "u5")])
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 500), 0,
                         "an unrelated later rewind's sig change does not re-take it either")
        self.assertIn(nid, jd.load_goals(SID)["nodes"])

    def _broken_episode_world(self, corrupt_head=False, unreadable=False):
        """The pre-/clear episode fixture with the EPIDIR-enumerated dead episode file BROKEN: a
        valid-JSON-but-non-dict head line (FileAdapter raises on it naturally), or chmod 000 (the
        incremental reader swallows the OSError into zero records with no row of its own). One
        orphan minted from the anchor's dead branch (provable without the broken file) and one
        from the broken file's own dead branch. Returns (leaf, epfile, eprecs)."""
        anchor = self.td / (SID + ".jsonl")
        anchor.write_text("\n".join(json.dumps(r) for r in
                                    self.base_recs() + self.fork_recs()) + "\n")
        epfsid = "44444444-5555-6666-7777-888888888888"
        epfile = self.td / (epfsid + ".jsonl")
        eprecs = [uline(T0 + 200, "old-episode ask", "u6"),
                  aline(T0 + 210, "Old-episode reply, settled.", "a6", "u6"),
                  uline(T0 + 215, "follow-up on the old episode", "u6b", "a6"),
                  aline(T0 + 218, "Follow-up reply.", "a6b", "u6b"),
                  uline(T0 + 220, "follow-up, rewritten", "u7", "a6"),
                  aline(T0 + 230, "Branch reply.", "a7", "u7")]
        body = "\n".join(json.dumps(r) for r in eprecs) + "\n"
        epfile.write_text(("[]\n" if corrupt_head else "") + body)
        if unreadable:
            os.chmod(epfile, 0)
            self.addCleanup(os.chmod, epfile, 0o600)
        jd.append_episode(SID, "u6", epfsid, T0 + 200)
        leaf = self.td / ("99999999-aaaa-bbbb-cccc-dddddddddddd.jsonl")
        leaf.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 300, "fresh start after clear", "u9"),
            aline(T0 + 310, "New conversation reply.", "a9", "u9")]) + "\n")
        s = self._store()
        self._mint(s, "seg-a", CUT, "Orphan from the anchor's dead branch", "u2")
        self._mint(s, "seg-b", T0 + 215, "Orphan from the old episode's dead branch", "u6b")
        jd.save_goals(SID, s)
        return leaf, epfile, eprecs

    def test_a_failed_per_file_scan_blocks_the_marker_never_the_proven_archives(self):
        # The one-time -v2 migration writes its done-marker only on a ZERO-FAILURE pass — but a
        # per-file scan failure was logged-and-swallowed inside _per_file_rewound, so the pass
        # returned a PARTIAL abandoned set with fails=0: the marker landed over the miss and
        # steady-state discovery (48h-windowed) never revisits a dormant session, permanently
        # skipping exactly the pre-/clear orphan class the widening targets. A per-file failure
        # now raises AFTER the proven archives land (partial archiving is idempotent and safe)
        # and BEFORE the memo (a partial sig must never become the event gate's baseline), so
        # run_rewound_reconcile counts the session failed and the marker waits.
        leaf, epfile, eprecs = self._broken_episode_world(corrupt_head=True)
        with self.assertRaises(RuntimeError):
            jd.reconcile_rewound_goals(SID, str(leaf), T0 + 400)
        live = jd.load_goals(SID)["nodes"]
        self.assertNotIn("%s:g1" % SID, live, "the PROVEN orphan still archived — partial is safe")
        self.assertIn("%s:g1" % SID, jd.load_goal_archive(SID)["nodes"])
        self.assertIn("%s:g2" % SID, live, "the broken file's orphan untouched — never guessed at")
        self.assertNotIn(SID, jd._RECON_MEMO, "a partial sig never becomes the gate's baseline")
        self.assertIn("rewound-reconcile-file", jd.ERRORS.read_text(),
                      "…and the row has its own category, distinguishable from a session failure")
        # the block heals where the failure does: with the file fixed, the retry pass (the next
        # boot's migration re-run) takes the rest and only then memoizes
        epfile.write_text("\n".join(json.dumps(r) for r in eprecs) + "\n")
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(leaf), T0 + 500), 1)
        self.assertNotIn("%s:g2" % SID, jd.load_goals(SID)["nodes"])
        self.assertIn(SID, jd._RECON_MEMO, "a clean pass memoizes")

    def test_a_silently_unreadable_episode_file_still_counts_as_a_failure(self):
        # The sneakier trigger: the incremental reader swallows an OSError into an EMPTY record
        # list with no log row, so the per-file except never fired yet the sig was still partial —
        # even quieter than the claim said. A non-empty transcript that yields zero records now
        # counts as the read failure it is.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("chmod 000 does not block reads for root")
        leaf, epfile, eprecs = self._broken_episode_world(unreadable=True)
        with self.assertRaises(RuntimeError):
            jd.reconcile_rewound_goals(SID, str(leaf), T0 + 400)
        self.assertIn("%s:g2" % SID, jd.load_goals(SID)["nodes"],
                      "the unreadable file's orphan is untouched, not silently skipped for good")
        self.assertNotIn(SID, jd._RECON_MEMO)
        self.assertIn("yielded no records", jd.ERRORS.read_text(), "the zero-record read is loud")

    def test_a_pending_unconsumed_cut_is_not_reconciled(self):
        # the two-phase hold owns the armed window (hide now, archive at take, RESTORE on failure) —
        # reconciling it would archive cards for a rewind that can still fail
        self.write(self.base_recs())                   # no fork on disk: the rollback is only armed
        jd.set_pending_cut_provider(lambda sid: "a1")
        s = self._store()
        self._mint(s, "seg-dead", CUT, "Card the pending delete would drop", "u2")
        jd.save_goals(SID, s)
        self.assertEqual(jd.reconcile_rewound_goals(SID, str(self.path), T0 + 200), 0)
        self.assertIn("%s:g1" % SID, jd.load_goals(SID)["nodes"])


if __name__ == "__main__":
    unittest.main()
