#!/usr/bin/env python3
"""Give-up cards re-arm on RECOVERY EVENTS (the user 2026-08-18): most give-ups come from call-level
failures (a 529 overload storm, an auth blip) that never engage the usage-limit retry-pause — so the one
wired recovery edge never fired and the "distill failed" chip outlived the outage until a manual Try
again. Under test here:
  - the judge-call health latch, PER MODEL: a call-level failure latches that model degraded; the first
    SERVED reply on the SAME model is the degraded→serving edge, consumable exactly once — a healthy
    model's success is never the failing model's recovery (the Opus-scoped storm, where a Sonnet
    captioner success fired the old model-blind edge mid-storm);
  - rearm_failed_summaries(auto=True), the edge's consumer: one automatic retry per give-up era PER
    LINE (nd["autoRearmed"] keyed by warn kind), gate-aware (a line whose summarizer gate cannot reopen
    is skipped, not burned), while discrete events (startup, retry-pause clear, a distill-model switch)
    open a fresh era;
  - a give-up that KEPT an older real summary (a re-completion never blanks prior text) re-arms by
    clearing its event stamp, since the "" flip cannot apply;
  - "stall-failed" joins the scan/re-arm family, and an ENDED stall's warn retires on rollup instead of
    inflating the fleet failure count forever;
  - a safeguards refusal (the filter ruling on content) never latches the health edge.
SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_rearm", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NID = SID + ":g1"
T0 = 1781100000


def _warn(kind, t=T0):
    return {"kind": kind, "t": t, "msg": "synthetic msg", "detail": "synthetic detail"}


def _seed(status="completed", warns=None, **nd_extra):
    store = jd.load_goals(SID)
    store["rompUuid"] = SID
    nd = {"text": "ship the api", "parentId": None, "mt": T0}
    if warns is not None:
        nd["warns"] = warns
    nd.update(nd_extra)
    store["nodes"][NID] = jd.GuardedNode(nd)
    if status:
        store.setdefault("status", {})[NID] = status
    jd.save_goals(SID, store)


def _node():
    return jd.load_goals(SID)["nodes"][NID]


def _drain_health():
    while jd.consume_judge_recovery():
        pass
    with jd._health_lock:
        jd._CALL_HEALTH["degraded"] = set()
        jd._CALL_HEALTH["stats"] = {}


class JudgeCallHealthEdge(unittest.TestCase):
    def setUp(self):
        jd.STATE.mkdir(parents=True, exist_ok=True)
        _drain_health()

    def test_first_served_reply_on_the_failing_model_is_one_edge(self):
        jd._mark_call_failed("opus")
        self.assertFalse(jd.consume_judge_recovery(), "a failure alone is not a recovery")
        jd._mark_call_served("opus")
        self.assertTrue(jd.consume_judge_recovery(), "the failing model serving again = the edge")
        self.assertFalse(jd.consume_judge_recovery(), "the edge is consumed exactly once")

    def test_a_healthy_models_success_is_not_the_failing_models_edge(self):
        # the 2026-08-18 storm: Opus-scoped 529s while Sonnet served — the old model-blind edge fired
        # on every Sonnet success and spent each card's one automatic retry on a doomed Opus call
        jd._mark_call_failed("opus")
        jd._mark_call_served("sonnet")
        self.assertFalse(jd.consume_judge_recovery(), "another model serving proves nothing about opus")
        jd._mark_call_served("opus")
        self.assertTrue(jd.consume_judge_recovery(), "opus itself serving is the real recovery")

    def test_serving_while_healthy_is_not_an_edge(self):
        jd._mark_call_served("sonnet")
        self.assertFalse(jd.consume_judge_recovery(), "no prior failure → nothing recovered")


class RearmRecoveryEvents(unittest.TestCase):
    def tearDown(self):
        for d in (jd.GOALDIR, jd._overrides_dir()):
            for f in d.glob("*"):
                f.unlink()
        (jd.STATE / "auto-nudge.json").unlink(missing_ok=True)

    def test_auto_rearm_retries_a_give_up_era_exactly_once(self):
        _seed(summary="", distillFails=0, warns=[_warn("summary-failed")])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100, auto=True), 1)
        nd = _node()
        self.assertIsNone(nd.get("summary"), '"" (gave up) → None (owed): the next pass retries')
        self.assertTrue(jd._era_spent(nd, "summary-failed"), "the line's one automatic retry is spent")
        # the retry re-gives-up (sentinel + warn re-stamped; the mark survives the give-up write)
        st = jd.load_goals(SID)
        st["nodes"][NID]["summary"] = ""
        jd.save_goals(SID, st)
        self.assertEqual(jd.rearm_failed_summaries(T0 + 200, auto=True), 0,
                         "a healthy neighbor's every edge must not burn DISTILL_FAIL_CAP calls on a "
                         "card whose own call is broken — one automatic retry per era")

    def test_a_discrete_event_rearms_and_opens_a_fresh_era(self):
        _seed(summary="", warns=[_warn("summary-failed")], autoRearmed=True)   # legacy bool mark
        self.assertEqual(jd.rearm_failed_summaries(T0 + 300), 1,
                         "startup / retry-pause clear / model switch re-arm regardless of the era mark")
        nd = _node()
        self.assertIsNone(nd.get("summary"))
        self.assertNotIn("autoRearmed", nd, "a discrete event opens a fresh era for the health edge")

    def test_rearm_reenters_a_giveup_that_kept_an_older_summary(self):
        # a re-completion's give-up never blanks prior text — so there is no "" to flip; the stamp
        # clears instead and the gate re-enters with the prior summary intact
        _seed(summary="Old takeaway.", distilledMt=T0, warns=[_warn("summary-failed")])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 1)
        nd = _node()
        self.assertEqual(nd.get("summary"), "Old takeaway.", "the prior text is never clobbered")
        self.assertIsNone(nd.get("distilledMt"), "the cleared stamp is what re-enters the distiller")

    def test_an_already_owed_line_is_not_recounted(self):
        _seed(summary=None, warns=[_warn("summary-failed")])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 0,
                         "None is already owed — nothing to flip, nothing to count")

    def test_a_line_whose_gate_is_closed_is_skipped_not_burned(self):
        # summary gate needs a completed/confirming top: re-arming a working card's summary line would
        # flip a field nothing regenerates AND spend its era (the review's dead-surface finding)
        _seed(status="working", summary="", warns=[_warn("summary-failed")])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100, auto=True), 0)
        nd = _node()
        self.assertEqual(nd.get("summary"), "", "no flip on a closed gate")
        self.assertFalse(jd._era_spent(nd, "summary-failed"), "and no era spent on it either")

    def test_two_warn_card_retries_the_live_line_not_the_dead_one(self):
        # the review's two-warn finding: an old stall's warn (no live stall → gate closed) must not
        # consume the retry that the completed card's summary line — the one the user sees — needs
        _seed(status="completed", summary="", stallSummary="",
              warns=[_warn("stall-failed", T0), _warn("summary-failed", T0 + 50)])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100, auto=True), 1)
        nd = _node()
        self.assertEqual(nd.get("stallSummary"), "", "the dead stall line is left alone")
        self.assertIsNone(nd.get("summary"), "the live summary line gets the retry")
        self.assertTrue(jd._era_spent(nd, "summary-failed"))
        self.assertFalse(jd._era_spent(nd, "stall-failed"), "eras are per line, not per node")

    def test_stall_failed_rearms_only_while_the_stall_is_live(self):
        _seed(status="working", stallSummary="", warns=[_warn("stall-failed")])
        scan = jd.judge_failure_scan()
        self.assertEqual(scan["count"], 1, "a given-up stall note counts toward the banner")
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 0, "no live stall → nothing to retry")
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"deferred": {NID: {"why": "reviver-hold", "at": T0}}}))
        self.assertEqual(jd.rearm_failed_summaries(T0 + 200), 1)
        self.assertIsNone(_node().get("stallSummary"), "a live stall's note re-arms like the other lines")


class StallWarnRetires(unittest.TestCase):
    """An ENDED stall's give-up warn retires on rollup (the review finding: once stall-failed joined the
    fleet scan, an un-stalled card's warn had no clear path and inflated the top banner forever)."""

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        (jd.STATE / "auto-nudge.json").unlink(missing_ok=True)

    def test_an_ended_stalls_warn_retires_on_rollup(self):
        _seed(status="working", stallSummary="", warns=[_warn("stall-failed")])
        store = jd.load_goals(SID)
        jd.rollup_status(store, False)                 # no live stall record → the stall is over
        self.assertFalse(any(w.get("kind") == "stall-failed"
                             for w in (store["nodes"][NID].get("warns") or [])),
                         "the warn retires with the surface — the banner count can reach zero again")

    def test_a_live_stalls_warn_survives_rollup(self):
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"deferred": {NID: {"why": "reviver-hold", "at": T0}}}))
        _seed(status="working", stallSummary="", warns=[_warn("stall-failed")])
        store = jd.load_goals(SID)
        jd.rollup_status(store, False)
        self.assertTrue(any(w.get("kind") == "stall-failed"
                            for w in (store["nodes"][NID].get("warns") or [])),
                        "a live stall keeps its warn — the failure is still current")


class GiveUpWarnNamesTheError(unittest.TestCase):
    """The "distill failed" modal names the LAST attempt's literal error and the model it called (the
    user 2026-08-18): an Opus-scoped 529 outage read as generic "errors or timeouts" while every other
    tier served — the modal gave no way to see that only the distill tier's model was down."""

    def setUp(self):
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "usage.json").write_text(json.dumps(
            {"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        jd._judge_ctx.last_call_fail = None
        _drain_health()

    def _run_fake(self, envelope, model="opus"):
        def fake_run(cmd, input=None, capture_output=None, text=None, cwd=None, env=None, timeout=None):
            return SimpleNamespace(stdout=json.dumps(envelope), stderr="", returncode=0)
        saved = jd.subprocess.run
        jd.subprocess.run = fake_run
        try:
            return jd._judge_run(model, "SYS", "u", judge="distiller", tier="distill")
        finally:
            jd.subprocess.run = saved

    def test_an_error_envelope_stashes_the_note_and_the_model(self):
        out = self._run_fake({"is_error": True, "result": "API Error: Repeated 529 Overloaded errors."})
        self.assertEqual(out, "", "an error envelope is a failed call to the caller")
        st = jd._judge_ctx.last_call_fail
        self.assertIn("529 Overloaded", st["note"])
        self.assertEqual(st["model"], "opus")

    def test_an_error_envelope_latches_the_models_health(self):
        self._run_fake({"is_error": True, "result": "API Error: Repeated 529 Overloaded errors."})
        jd._mark_call_served("opus")
        self.assertTrue(jd.consume_judge_recovery(), "the envelope failure latched opus degraded")

    def test_a_safeguards_refusal_never_latches_the_health_edge(self):
        # the filter ruling on one call's CONTENT — deterministic per prompt, not model health; latching
        # it would flap the edge on every flag/success interleave (the closer storm, 2,955 flags)
        self._run_fake({"is_error": True,
                        "result": "API Error: the model's safeguards flagged this message."},
                       model="fable")
        st = jd._judge_ctx.last_call_fail
        self.assertIn("safeguards", st["note"], "the evidence still reaches the warn")
        jd._mark_call_served("fable")
        self.assertFalse(jd.consume_judge_recovery(), "a content refusal is not API degradation")

    def test_a_served_reply_retires_the_stash(self):
        self._run_fake({"is_error": True, "result": "API Error: Repeated 529 Overloaded errors."})
        self._run_fake({"result": "a fine reply", "usage": {}}, model="sonnet")
        self.assertIsNone(jd._judge_ctx.last_call_fail,
                          "a served reply must retire the evidence — a later unrelated give-up "
                          "never wears a stale error")

    def test_warn_detail_names_the_error_and_the_model(self):
        jd._judge_ctx.last_call_fail = {"note": "API Error: Repeated 529 Overloaded errors.",
                                        "model": "opus"}
        nd = {}
        jd._warn_summary_failed(nd, "distiller", T0)
        det = nd["warns"][0]["detail"]
        self.assertIn("529 Overloaded", det, "the literal error reaches the modal")
        self.assertIn("opus", det, "the model is named — a model-scoped outage is visible as such")

    def test_warn_detail_stays_generic_without_evidence(self):
        nd = {}
        jd._warn_summary_failed(nd, "distiller", T0)
        self.assertNotIn("last attempt failed", nd["warns"][0]["detail"])


class ConfirmingWarnSurvives(unittest.TestCase):
    """rollup's summary-surface warn retire must NOT eat a warn during the done-CONFIRMING window (the
    user 2026-08-18, the chipless summaryless card): the distiller enters confirming tops, so its
    give-up/unreadable warns stamp while status still reads "working" — the old `st != "completed"`
    retire dropped them within the same pass, before any chip ever rendered, leaving the "" sentinel
    orphaned with nothing armed to retry it."""

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()

    def _store(self, done_verdict):
        node = {"id": NID, "text": "ship the api", "parentId": None, "nodeComplete": bool(done_verdict),
                "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 30,
                "summary": "", "warns": [_warn("summary-failed")],
                "log": ([{"src": "planner", "kind": "done", "ev_t": T0 + 20, "at": T0 + 21}]
                        if done_verdict else [])}
        return {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V, "lastNode": NID,
                "placements": {}, "status": {NID: "working"}, "nodes": {NID: jd.GuardedNode(node)}}

    def test_a_confirming_tops_summary_warn_survives_the_retire(self):
        store = self._store(done_verdict=True)         # done verdict in, focus unmoved, session open
        jd.rollup_status(store, False)
        self.assertIn(NID, store.get("confirming") or (), "the window this test exists for")
        self.assertTrue(any(w.get("kind") == "summary-failed"
                            for w in (store["nodes"][NID].get("warns") or [])),
                        "the give-up warn lives to render its chip")

    def test_a_plain_working_tops_summary_warn_still_retires(self):
        store = self._store(done_verdict=False)        # no done verdict → genuinely working
        jd.rollup_status(store, False)
        self.assertNotIn(NID, store.get("confirming") or ())
        self.assertFalse(any(w.get("kind") == "summary-failed"
                             for w in (store["nodes"][NID].get("warns") or [])),
                         "a reopened/working card's takeaway isn't shown — its warn retires as before")


class OrphanedSentinelRecheck(unittest.TestCase):
    """A completed top WITH recorded work whose summary is the "" sentinel and whose warn never survived
    (eaten during confirming, before the fix above) is re-checked on DISCRETE recovery events: the stamp
    clears so the distiller re-enters — resolving work writes the real summary; unreadable work
    re-settles and re-warns loudly. Umbrellas (no recorded work) keep their designed silent ""."""

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()

    def test_a_discrete_event_reopens_an_orphaned_sentinel_with_recorded_work(self):
        _seed(status="completed", summary="", distilledMt=T0, trail=[SID + ":s1"])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 1)
        nd = _node()
        self.assertIsNone(nd.get("distilledMt"), "the cleared stamp re-enters the distiller")
        self.assertEqual(nd.get("summary"), "", "the sentinel itself is untouched until the retry rules")

    def test_the_health_edge_never_touches_orphans(self):
        _seed(status="completed", summary="", distilledMt=T0, trail=[SID + ":s1"])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100, auto=True), 0,
                         "warn-less orphans re-check on discrete events only — the auto edge is "
                         "reserved for the warn-gated era machinery")

    def test_an_umbrella_keeps_its_designed_silent_sentinel(self):
        _seed(status="completed", summary="", distilledMt=T0)   # no trail, no placements anywhere
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 0,
                         "no recorded work → the silent '' is correct (the 2026-07-10 design)")

    def test_a_card_with_a_live_summary_warn_is_the_warn_paths_business(self):
        _seed(status="completed", summary="", distilledMt=T0, trail=[SID + ":s1"],
              warns=[_warn("summary-unreadable")])
        self.assertEqual(jd.rearm_failed_summaries(T0 + 100), 0,
                         "a visible chip means the loud path owns it — the sweep is for eaten warns")


class ModelHealthDiagnosis(unittest.TestCase):
    """_giveup_cause names the failing MODEL once its consecutive call failures reach the give-up cap —
    a deterministic count, reset by that model's next served reply — and suggests the settings switch
    when the sick model is the distill tier's (the user 2026-08-18: the banner and the chips must say
    WHICH model is down and what to do about it)."""

    def setUp(self):
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "usage.json").write_text(json.dumps(
            {"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        _drain_health()

    def tearDown(self):
        _drain_health()
        (jd.STATE / "distill-model").unlink(missing_ok=True)

    def test_a_sick_model_is_named_with_its_last_error(self):
        for _ in range(jd.DISTILL_FAIL_CAP):
            jd._mark_call_failed("opus", "API Error: Repeated 529 Overloaded errors.")
        cause, ratelimited = jd._giveup_cause()
        self.assertFalse(ratelimited)
        self.assertIn("opus", cause, "the failing model is named")
        self.assertIn("529 Overloaded", cause, "with its literal last error")

    def test_the_distill_tiers_own_model_earns_the_switch_suggestion(self):
        (jd.STATE / "distill-model").write_text("opus")
        for _ in range(jd.DISTILL_FAIL_CAP):
            jd._mark_call_failed("opus", "API Error: Repeated 529 Overloaded errors.")
        cause, _ = jd._giveup_cause()
        self.assertIn("Switching the distill model", cause,
                      "the give-ups ARE this model's — the fix is one settings click, so say so")

    def test_below_the_cap_stays_generic(self):
        jd._mark_call_failed("opus", "API Error: Repeated 529 Overloaded errors.")
        cause, _ = jd._giveup_cause()
        self.assertIn("errors or timeouts", cause, "one blip is not a diagnosis")

    def test_a_served_reply_resets_the_diagnosis(self):
        for _ in range(jd.DISTILL_FAIL_CAP):
            jd._mark_call_failed("opus", "API Error: Repeated 529 Overloaded errors.")
        jd._mark_call_served("opus")
        cause, _ = jd._giveup_cause()
        self.assertIn("errors or timeouts", cause, "the model recovered — no stale blame")


class AttemptLog(unittest.TestCase):
    """The card's per-line attempt log (judge _fail_log): each failed summarizer try as when + model +
    literal error, capped, cleared by that line's next success — the chip's hover history and the
    modal's "What was tried" (the user 2026-08-18)."""

    def tearDown(self):
        jd._judge_ctx.last_call_fail = None

    def test_appends_from_the_stash_and_caps(self):
        nd = {}
        jd._judge_ctx.last_call_fail = {"note": "API Error: Repeated 529 Overloaded errors.",
                                        "model": "opus"}
        for i in range(10):
            jd._fail_log(nd, "summary", T0 + i)
        self.assertEqual(len(nd["failLog"]), 8, "capped so a store never grows unbounded")
        self.assertEqual(nd["failLog"][-1]["model"], "opus")
        self.assertEqual(nd["failLog"][-1]["t"], T0 + 9)
        self.assertIn("529 Overloaded", nd["failLog"][-1]["note"])

    def test_clear_is_per_line(self):
        nd = {}
        jd._judge_ctx.last_call_fail = {"note": "boom", "model": "opus"}
        jd._fail_log(nd, "summary", T0)
        jd._fail_log(nd, "brief", T0 + 1)
        jd._fail_log_clear(nd, "summary")
        self.assertEqual([e["line"] for e in nd["failLog"]], ["brief"],
                         "the landed line's rows drop; the other line's history stays")
        jd._fail_log_clear(nd, "brief")
        self.assertNotIn("failLog", nd)

    def test_no_stashed_evidence_no_row(self):
        nd = {}
        jd._judge_ctx.last_call_fail = None
        jd._fail_log(nd, "summary", T0)
        self.assertNotIn("failLog", nd)


class KernelRearmWiring(unittest.TestCase):
    """Source pins on the kernel's recovery-event call sites (the RedistillOpWiring precedent —
    the wiring is a few lines inside functions no unit test can enter cheaply)."""

    @classmethod
    def setUpClass(cls):
        cls.km = load_source("romp_kernel_rearm", os.path.join(BIN, "romp-kernel"))

    def test_startup_rearms_before_the_server_loop(self):
        import inspect
        src = inspect.getsource(self.km.main)
        self.assertIn("rearm_failed_summaries", src, "a restart is a discrete recovery event — wired in main()")
        self.assertLess(src.index("rearm_failed_summaries"), src.index("serve_forever"),
                        "the boot sweep re-arms before the kernel starts serving")

    def test_producer_consumes_the_health_edge_after_the_tier_join(self):
        import inspect
        src = inspect.getsource(self.km._producer)
        i_join = src.index("t.join()")
        i_edge = src.index("consume_judge_recovery")
        self.assertGreater(i_edge, i_join,
                           "the edge is consumed AFTER the join — the single-writer window, so the "
                           "re-arm's store writes can't race the judge worker threads")
        seg = src[i_edge:]
        self.assertIn("rearm_failed_summaries", seg[:400], "the consumed edge drives the auto re-arm")
        self.assertIn("auto=True", seg[:400], "the health edge is the era-bounded auto path")

    def test_a_distill_model_switch_rearms_wakes_and_pushes(self):
        # the user 2026-08-18: switching the distill model away from an outage-scoped one must itself
        # retry everything that failed — and show it on the board immediately
        import inspect
        src = inspect.getsource(self.km._apply_judge_settings)
        self.assertIn("jd._distill_model()", src,
                      "the EFFECTIVE model is compared — a triage change while following counts too")
        self.assertIn("rearm_failed_summaries", src, "the switch is a discrete recovery event")
        self.assertIn("_producer_wake.set()", src, "the retry pass starts now, not at the next tick")
        self.assertIn("_push_soon()", src,
                      "the swirl replaces the chip via the woken pusher — never an inline fleet build "
                      "on the request thread (the 2026-08-30 POST wedge)")


if __name__ == "__main__":
    unittest.main()
