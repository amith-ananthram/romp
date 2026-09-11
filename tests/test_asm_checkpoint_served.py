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


def transcript(t0, turns=400, compact_every=60):
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
        env = _lab.kernel_env(self.lab, self.claude, self.dist, port, self.token, ROMP_HOST_NAME="TESTHOST")
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

    def _open_tab(self, port):
        """A chat client looks at web; returns the seconds from the ready frame to web's session frame."""
        client = ChatClient(port, self.token, WEB)
        try:
            t0 = time.time()
            client.send({"type": "ready"})
            for fr in client.frames(60):
                if fr.get("type") == "session" and (fr.get("id") == WEB or fr.get("sid") == WEB):
                    return time.time() - t0, fr
            self.fail("no session frame for web within 60 s")
        finally:
            client.close()

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
        docs = [f for f in os.listdir(os.path.join(self.state, "checkpoints")) if f.endswith(".asm.json")]
        self.assertEqual(len(docs), 1, "the exit wrote web's assembly document; wrote: %s" % docs)
        doc = json.loads(open(os.path.join(self.state, "checkpoints", docs[0])).read())
        self.assertEqual(doc["path"], os.path.realpath(self.leaf))
        size = os.path.getsize(self.leaf)
        k2, p2, log2 = self._boot()
        try:
            perf_boot = self._get(p2, "/perf")
            dt2, frame = self._open_tab(p2)
            time.sleep(1.0)
            perf = self._get(p2, "/perf")
            asm = perf["asmCheckpoint"]
            by = perf["checkpoints"]["readByPath"]
            self.assertEqual(asm["fallbacks"], {}, "the document verified: %s" % asm)
            self.assertGreaterEqual(asm["restored"], 1, "the parse came from the document: %s" % asm)
            leaf_read = by.get(os.path.realpath(self.leaf), by.get(self.leaf, 0))
            self.assertLess(leaf_read, 2 * size, "the leaf was not read whole twice (%d of %d bytes: the tail, the guards, and the frame's hydration)" % (leaf_read, size))
            self.assertGreater(len(frame.get("events") or []), 0, "the frame carries events")
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
