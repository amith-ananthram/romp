#!/usr/bin/env python3
"""T323 stage 4a, served: a REAL hermetic kernel over a large synthetic session whose transcript compacted several times;
a chat client (the stage 3 websocket client) looks at it, so the kernel parses it whole; the kernel is stopped with
SIGTERM and its drain writes the assembly document. A SECOND kernel over the same state root, same client: the parse
restores from the document (asmCheckpoint.restored, no fallback), the first session frame arrives (its time is
printed), the kernel log holds no LazyBodyRead, and the leaf's bytes read before the frame are the document plus the
tail plus what the frame hydrated, less than the whole file read twice. What stays whole is on the record: a full
frame hydrates every turn it renders, which for a first open is the whole session (the tail-first frame is 4b's).
Synthetic only: invented text, placeholder uuids, TESTHOST."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab                      # noqa: E402  the lab kernel's environment
from test_fold_checkpoints_served import ChatClient, _free_port, iso   # noqa: E402  the websocket client

WEB = "aaaaaaaa-4444-4222-8333-444444444444"
WORDS = ("fixture", "suite", "backoff", "jitter", "cap", "retry", "review", "branch", "merge", "green", "README", "wire")


def transcript(t0, turns=4000, compact_every=150):
    import random
    rnd = random.Random(4)
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "b%d" % k, "s%d" % k
            recs.append({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
                         "timestamp": iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
            recs.append({"type": "user", "uuid": s, "parentUuid": b, "timestamp": iso(t + 1), "isCompactSummary": True,
                         "message": {"role": "user", "content": "summary so far: " + " ".join(rnd.choice(WORDS) for _ in range(120))}})
            parent = s; t += 2
        u, a = "u%d" % k, "a%d" % k
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
                     "message": {"role": "user", "content": " ".join(rnd.choice(WORDS) for _ in range(30)) + " %d" % k}})
        blocks = [{"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(200))}]
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
                     "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}})
        parent = a; t += 60
    return recs


def _children(pid):
    """The kernel's child processes, with their command lines, from /proc (a pusher blocked on a subprocess shows here)."""
    out = []
    try:
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % d) as f:
                    st = f.read()
                ppid = int(st[st.rindex(")") + 2:].split()[1])
                if ppid != pid:
                    continue
                with open("/proc/%s/cmdline" % d, "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                out.append("%s: %s" % (d, cmd[:160]))
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        return None
    return out


class RestartOverACheckpointedSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="romp-t323s4a-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), cls.dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cls.claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "goals", "timeline"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(cls.claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 86400
        Path(cls.state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(cls.state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5", "lastStopAt": t0 + 90}))
        Path(cls.state, "states", WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
            {"t": t0 + 10, "state": "working"}, {"t": t0 + 70, "state": "waiting"}, {"t": t0 + 71, "state": "idle"})))
        cls.leaf = os.path.join(proj, WEB + ".jsonl")
        Path(cls.leaf).write_text("".join(json.dumps(r) + "\n" for r in transcript(t0)))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.token = "testtok-t323s4a"

    @classmethod
    def tearDownClass(cls):
        keep = os.environ.get("ROMP_T323S4_KEEP_LOGS")
        if keep:
            os.makedirs(keep, exist_ok=True)
            for f in os.listdir(cls.lab):
                if f.startswith("kernel-") and f.endswith(".log"):
                    shutil.copy(os.path.join(cls.lab, f), os.path.join(keep, f))
        shutil.rmtree(cls.lab, ignore_errors=True)

    def _boot(self):
        port = _free_port()
        env = _lab.kernel_env(self.lab, self.claude, self.dist, port, self.token, ROMP_HOST_NAME="TESTHOST",
                              ROMP_READER_TRACE="1",   # one stderr line per byte-pulling read, quoted when a bound fails
                              ROMP_PERF_STACKS="1")    # /perf carries every thread's last frames, sampled while a frame is awaited
        logp = os.path.join(self.lab, "kernel-%d.log" % port)
        k = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(logp, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            k.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        for _ in range(40):
            try:
                if self._get(port, "/version").get("uptime_s", 0) >= 4:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        return k, port, logp

    def _get(self, port, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-Romp-Token": self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def _open_tab(self, port, timeline=None, pid=None):
        """A chat client looks at web; returns the seconds from the ready frame to web's session frame. `timeline`, a list,
        receives one /perf sample per second while the frame is awaited (the assembly counters, for a failure message
        that has to say what the kernel did while a client waited on a runner nobody can log into)."""
        client = ChatClient(port, self.token, WEB)
        stop = threading.Event()
        def sample():
            t0 = time.time()
            while not stop.wait(1.0):                                     # loop-ok: bounded by the frame's own 60 s wait
                try:
                    perf = self._get(port, "/perf")
                    ai = perf.get("asmIndex") or {}
                    stacks = {n: fr for n, fr in (perf.get("stacks") or {}).items()
                              if not any("stop.wait" in l or "waiter.acquire" in l or "selector" in l for l in fr[-1:])}   # the idle ones aside
                    timeline.append({"t": round(time.time() - t0, 1), "built": ai.get("materialized"), "builtBy": ai.get("materializedBy"),
                                     "hydratedBy": (perf.get("asmCheckpoint") or {}).get("hydratedBy"),
                                     "process": perf.get("process"), "pusher": perf.get("pusher"), "stagesMs": perf.get("stages_ms"),
                                     "builds": perf.get("builds"), "judge": perf.get("judge"), "stacks": stacks,
                                     "children": _children(pid) if pid else None})
                except Exception as e:
                    timeline.append({"t": round(time.time() - t0, 1), "error": repr(e)[:120]})
        th = threading.Thread(target=sample, daemon=True) if timeline is not None else None
        try:
            t0 = time.time()
            if th is not None:
                th.start()
            client.send({"type": "ready"})
            for fr in client.frames(60):
                if fr.get("type") == "session" and (fr.get("id") == WEB or fr.get("sid") == WEB):
                    return time.time() - t0, fr
            self.fail("no session frame for web within 60 s")
        finally:
            stop.set()
            if th is not None:
                th.join(timeout=5)
            client.close()

    def _leaf_trace(self, logp):
        """The kernel's trace lines about the leaf (reads that pulled bytes, chain answers), for a failure message."""
        leaf = os.path.basename(self.leaf)
        with open(logp, errors="replace") as f:
            return "\n" + "\n".join(l.rstrip() for l in f if leaf in l and (l.startswith("reader:") or l.startswith("chain:")))

    def _log_tail(self, logp, n=40):
        """The kernel log's last lines, reader-trace lines aside (those are the leaf trace's), for a failure message that has
        to say what the kernel was doing on a runner nobody can log into."""
        with open(logp, errors="replace") as f:
            lines = [l.rstrip() for l in f if not l.startswith("reader:")]
        return "\n" + "\n".join(lines[-n:])

    def _stop(self, k):
        k.send_signal(signal.SIGTERM)
        k.wait(timeout=30)

    def test_the_second_kernel_restores_the_assembly_from_the_first_kernels_document(self):
        k1, p1, log1 = self._boot()
        try:
            dt1, _ = self._open_tab(p1)
            time.sleep(2.0)
            perf1 = self._get(p1, "/perf")["asmCheckpoint"]
            self.assertEqual(perf1["fallbacks"], {}, "a first boot has nothing to fall back from")
        finally:
            self._stop(k1)
        docs = [f for f in os.listdir(os.path.join(self.state, "checkpoints")) if f.endswith(".asm.json.gz")]
        self.assertEqual(len(docs), 1, "the exit wrote web's assembly document; wrote: %s" % docs)
        import gzip
        doc = json.loads(gzip.decompress(open(os.path.join(self.state, "checkpoints", docs[0]), "rb").read()))
        self.assertEqual(doc["path"], os.path.realpath(self.leaf))
        folds = {}                                          # the fold names the first kernel's exit left per file
        for f in os.listdir(os.path.join(self.state, "checkpoints")):
            if f.endswith(".json"):
                with open(os.path.join(self.state, "checkpoints", f)) as fh:
                    d = json.load(fh)
                folds[d.get("path", f)] = sorted((d.get("folds") or {}).keys())   # by full path: the leaf and its states log
        leaf_folds = folds.get(os.path.realpath(self.leaf), folds.get(self.leaf, []))   #  share a basename (CI's listdir order)
        self.assertLessEqual({"agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"}, set(leaf_folds),
                         "the exit leaves every leaf fold's cursor, the judges' included, whether or not a pass reached it while the "
                         "kernel lived: a fold with no cursor reads the leaf whole at its first run after the restart; left: %s" % folds)
        size = os.path.getsize(self.leaf)
        k2, p2, log2 = self._boot()
        try:
            perf_boot = self._get(p2, "/perf")
            timeline = []
            dt2, frame = self._open_tab(p2, timeline, k2.pid)
            time.sleep(1.0)
            perf = self._get(p2, "/perf")
            asm = perf["asmCheckpoint"]
            by = perf["checkpoints"]["readByPath"]
            self.assertEqual(asm["fallbacks"], {}, "the document verified: %s" % asm)
            self.assertGreaterEqual(asm["restored"], 1, "the parse came from the document: %s" % asm)
            leaf_read = by.get(os.path.realpath(self.leaf), by.get(self.leaf, 0))
            self.assertLessEqual(leaf_read, size + 8 * 64, "the leaf was never read whole: %d of %d bytes (the tail, the guards, and the "
                                                            "frame's hydration of the atoms it renders); the folds the first kernel left: %s; the leaf's reads and chain answers: %s"
                                                            % (leaf_read, size, folds, self._leaf_trace(log2)))
            self.assertLess(leaf_read - asm["hydratedBytes"], size / 4, "without the frame's hydration the leaf cost its tail and guards only: "
                                                                        "%d read, %d hydrated, %d whole" % (leaf_read, asm["hydratedBytes"], size))
            self.assertGreater(len(frame.get("events") or []), 0, "the frame carries events")
            bound = max(15.0, 2.5 * dt1)   # headroom over the runner's observed 10 s, scaling with this run's own whole-parse frame: a
            #                                per-atom scan reads 20 s and up here (below); a slow serial step read 10.08 s (2026-09-12)
            self.assertLess(dt2, bound, "the first frame of the restored kernel came in %.1fs against a bound of %.1fs (the first kernel's "
                                        "whole-parse frame %.1fs): hydration seeks to each record's offset; a scan "
                                       "from byte zero per atom measured 5.5 s on a 2000-turn fixture and grows with its square, so it "
                                       "would take over 20 s on this %d-record one; asmIndex=%s hydratedBy=%s; while the frame was awaited:%s; "
                                       "at boot: %s; the kernel's last lines:%s"
                                       % (dt2, bound, dt1, sum(1 for _ in open(self.leaf)), perf.get("asmIndex"), asm.get("hydratedBy"),
                                          "".join("\n  " + json.dumps(x, sort_keys=True, default=str) for x in timeline),
                                          json.dumps({k: perf_boot.get(k) for k in ("asmIndex", "asmCheckpoint", "process", "pusher", "stages_ms", "judge")},
                                                     sort_keys=True, default=str),
                                          self._log_tail(log2)))
            n_lazy = sum(1 for row in doc["atoms"] if row.get("lz") is not None)   # the atoms with a body to read (not a boundary)
            self.assertGreater(asm["hydratedAtoms"], 0, "the frame hydrated the pre-cut atoms it rendered")
            self.assertEqual(asm["hydratedAtoms"], n_lazy, "each pre-cut atom with a body read once, at its offset, whoever asked first: %s"
                             % asm["hydratedBy"])
            time.sleep(2.0)                                            # whatever the judges did meanwhile (a pass under the full suite's
            perf = self._get(p2, "/perf")                              #  load may not finish here; the deterministic proof that the
            asm = perf["asmCheckpoint"]                                #  declared plan hydrates nothing is tests/test_asm_checkpoint.py)
            self.assertNotIn("declared_plan", asm["hydratedBy"], "a session with no task store and no plan hydrates nothing for the "
                                                                    "planner's declared-plan fold: %s" % asm["hydratedBy"])
            self.assertEqual(asm["hydratedAtoms"], n_lazy, "no caller re-read a body: the memo served what the frame had read; "
                                                           "by caller %s; judges %s" % (asm["hydratedBy"], perf.get("judge")))
            by = perf["checkpoints"]["readByPath"]
            leaf_read = by.get(os.path.realpath(self.leaf), by.get(self.leaf, 0))
            self.assertLessEqual(leaf_read, size + 8 * 64, "the judges' pass added no whole read: %d of %d bytes; hydration by caller %s; %s"
                                 % (leaf_read, size, asm["hydratedBy"], self._leaf_trace(log2)))
            log = open(log2).read()
            self.assertNotIn("LazyBodyRead", log, "no consumer read a body before hydrating")
            self.assertNotIn("assembly checkpoint fallback", log)
            sys.stderr.write("t323s4a served: first frame %.2fs on the first kernel, %.2fs on the restored one; leaf %d bytes, read %d; "
                             "hydrated %d atoms / %d bytes; docs %s\n" % (dt1, dt2, size, leaf_read, asm["hydratedAtoms"], asm["hydratedBytes"],
                                                                        {k: v for k, v in asm.items() if k in ("restored", "written", "skipped")}))
        finally:
            self._stop(k2)


if __name__ == "__main__":
    unittest.main()
