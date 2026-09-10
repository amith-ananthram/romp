#!/usr/bin/env python3
"""T304: the figure script (scripts/restart_metrics_report.py) over a synthetic `romp restart-metrics --json`
document. The data shaping is pure and always tested; the drawing runs end to end when cleanplots and
matplotlib import (they are not romp dependencies: `uvx --with cleanplots --with matplotlib --with pandas
pytest tests/test_restart_metrics_report.py`), and is SKIPPED with a named reason otherwise, never
replaced by another look. Synthetic only."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sys.path.insert(0, HERE)
from romp_load import load_source   # noqa: E402

rep = load_source("romp_restart_metrics_report", os.path.join(ROOT, "scripts", "restart_metrics_report.py"))
rm = load_source("romp_restart_metrics_for_report", os.path.join(ROOT, "bin", "romp-restart-metrics"))
from test_restart_metrics import _fixture, SID   # noqa: E402  (the synthetic week)

HAVE_PLOT = importlib.util.find_spec("cleanplots") is not None and importlib.util.find_spec("matplotlib") is not None \
    and importlib.util.find_spec("pandas") is not None


def _doc(label_shift=0):
    state = Path(tempfile.mkdtemp())
    _fixture(state)
    doc = rm.collect(state, kind="week", anchor="2026-09-10", tz="UTC", live=False)
    doc["label"] = "TESTHOST"
    doc["live"] = {"source": "cgroup", "t": 1, "errors": [], "duplicateClis": [],
                   "sessions": [{"sid": SID, "name": "web", "memBytes": 300 * 1048576, "cpuS": 12.5, "sid8": "11111111"},
                                {"sid": None, "name": "22222222", "memBytes": 100 * 1048576, "cpuS": None, "sid8": "22222222"}],
                   "kernel": {"port": 1, "pid": 42, "uptimeS": 100, "perf": {"cpuS": 55.0, "rssKb": 2048 * 1024, "idleShare": 0.4}},
                   "memTotalBytes": 400 * 1048576, "sessionsCounted": 2}
    return doc


class Frames(unittest.TestCase):
    def test_frames_from_two_documents(self):
        fr = rep.frames([("baseline", _doc()), ("after", _doc())], anonymize=False)   # the named view; the default is pinned below
        self.assertEqual(fr["labels"], ["baseline", "after"])
        self.assertEqual([r["label"] for r in fr["windows"]], ["baseline", "after"])
        w = fr["windows"][0]
        self.assertEqual((w["window"], w["restarts"], w["cutTurns"], w["cutTurnsPerRestart"]), ("week of 2026-09-10", 3, 3, 1.0))
        self.assertEqual((w["outageP50"], w["outageP90"], w["quietP50"], w["backstopFires"]), (2.5, 4.0, 297.0, 1))
        self.assertEqual(w["events"]["orphansReaped"], 1)
        self.assertEqual((w["resumedTurns"], w["redoTurns"], w["redoUsd"], w["spendUsd"]), (3, 1, 0.5, 15.5))
        self.assertEqual(w["latencySamples"], [37.0, 10.0])
        self.assertEqual(w["stateLogSamples"], [30.0, 37.0])
        self.assertEqual(len(fr["live"]), 2)
        lv = fr["live"][0]
        self.assertEqual([s["name"] for s in lv["sessions"]], ["web", "22222222"], "largest first")
        self.assertEqual((lv["kernelRssMb"], lv["kernelCpuS"], lv["kernelIdleShare"], lv["kernelPid"]), (2048.0, 55.0, 0.4, 42))
        ks = fr["kernel"][0]
        self.assertEqual(ks["label"], "baseline")
        self.assertEqual([(p["kind"], p["rssMb"]) for p in ks["points"]], [("exit", 4096.0), ("boot", 300.0), ("exit", 2048.0)])
        self.assertEqual(ks["points"][0]["days"], 0.0)

    def test_session_names_are_hidden_by_default_outside_the_state_root(self):
        """The manager's fold (2026-09-10): real session names (other projects' among them) are private, so
        every figure written outside the user's own state root names sessions by memory rank, and only the
        caller's explicit ask shows them."""
        fr = rep.frames([("baseline", _doc())])                       # anonymize=True is the frames default
        self.assertEqual([s["name"] for s in fr["live"][0]["sessions"]], ["session 1", "session 2"])
        self.assertTrue(fr["anonymized"])
        named = rep.frames([("baseline", _doc())], anonymize=False)
        self.assertEqual([s["name"] for s in named["live"][0]["sessions"]], ["web", "22222222"])
        self.assertTrue(rep.anonymize_default(tempfile.mkdtemp()), "a scratch dir is outside the state root")
        self.assertFalse(rep.anonymize_default(rep.STATE_ROOT / "romp-research" / "restart-metrics"), "the user's own state root shows names")
        self.assertTrue(rep.anonymize_default(Path("/nonexistent/place")))

    def test_load_docs_labels(self):
        d = Path(tempfile.mkdtemp())
        (d / "base.json").write_text(json.dumps(_doc()))
        docs = rep.load_docs(["baseline=%s" % (d / "base.json"), str(d / "base.json")])
        self.assertEqual([l for l, _ in docs], ["baseline", "base"])
        (d / "bad.json").write_text("{}")
        with self.assertRaises(SystemExit):
            rep.load_docs([str(d / "bad.json")])


@unittest.skipUnless(HAVE_PLOT, "cleanplots, matplotlib and pandas are not importable here; run under uvx --with cleanplots --with matplotlib --with pandas")
class Render(unittest.TestCase):
    def test_every_figure_is_drawn_end_to_end(self):
        out = Path(tempfile.mkdtemp()) / "figs"
        made = rep.render([("baseline", _doc()), ("after", _doc())], out)
        expected = {"cut_turns", "restart_timing", "quiet_window", "boot_events", "redo_cost", "turn_latency", "session_resources",
                    "kernel_memory"}
        self.assertEqual(set(made), expected)
        for name, path in made.items():
            self.assertIsNotNone(path, name + " drew nothing")
            self.assertGreater(os.path.getsize(path), 1000, name + " is empty")
            self.assertTrue(path.endswith(".png"))
        figs = (out / "figures.json").read_text()
        self.assertNotIn("web", figs, "a scratch out dir is outside the state root: names hidden by default")
        self.assertIn("session 1", figs)
        self.assertIn("restart metrics: TESTHOST", (out / "summary.txt").read_text())
        made2 = rep.render([("baseline", _doc())], out / "named", anonymize=False)
        self.assertIn("web", (out / "named" / "figures.json").read_text())
        self.assertIsNotNone(made2["session_resources"])

    def test_main_prints_the_paths(self):
        d = Path(tempfile.mkdtemp())
        (d / "b.json").write_text(json.dumps(_doc()))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rep.main(["--doc", "baseline=%s" % (d / "b.json"), "--out", str(d / "out")])
        self.assertEqual(rc, 0)
        self.assertIn("cut_turns", buf.getvalue())
        self.assertIn("session names: hidden", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            rep.main(["--doc", "baseline=%s" % (d / "b.json"), "--out", str(d / "out2"), "--named"])
        self.assertIn("session names: shown", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
