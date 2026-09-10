#!/usr/bin/env python3
"""GET /sessions?threads=1 (the user 2026-08-22): comment-thread sessions ride the unified session
list ONLY when asked — the postal bus asks, so a thread can mail its parent under its own name;
every existing consumer (Obsidian picker, romp sessions, the default bus listing) is unchanged.
Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "11111111-2222-3333-4444-555555555555"
PARENT_NAME = "web"
TSID = "66666666-7777-8888-9999-000000000000"


def _mk_thread(parent, tsid, name="web-comment-1", status="open", reg_name=None, alive=True, root=None):
    """A comment thread as the kernel keeps it: a row in the parent's comments store + an SDK reg
    carrying threadOf (reg_name None = the row's name, the modern shape). `root` defaults to the
    kernel's own state dir (the HTTP doors read it); a test that drives a backend INSTANCE passes
    its private dir — km.jd.STATE is a shared module object every test module's load re-points."""
    root = root or km.jd.STATE
    cdir = root / "comments"
    cdir.mkdir(parents=True, exist_ok=True)
    row = {"tid": tsid, "sid": tsid, "status": status, "createdT": 1, "lastSeenT": 1}
    if name:
        row["name"] = name
    (cdir / (parent + ".json")).write_text(json.dumps({"threads": [row]}))
    sdir = root / "sdk"
    sdir.mkdir(parents=True, exist_ok=True)
    reg = {"sid": tsid, "cwd": "/tmp", "alive": alive, "threadOf": parent, "lastSid": tsid}
    if reg_name or name:
        reg["name"] = reg_name or name
    (sdir / (tsid + ".json")).write_text(json.dumps(reg))


def _rm_threads():
    for f in list((km.jd.STATE / "comments").glob("*.json")) + list((km.jd.STATE / "sdk").glob("*.json")):
        f.unlink()


class RealListingSplit(unittest.TestCase):
    """Both directions of the design against the REAL backend filter (the user 2026-08-22): a threadOf
    reg is absent from live_sessions/_session_rows — what GET /sessions and the pusher's tab payload
    are built from — and present under thread_sessions/?threads=1."""

    def test_a_thread_reg_splits_the_real_way(self):
        # a hermetic backend INSTANCE over a PRIVATE state dir — never km._sdk() (its lazy build runs
        # boot reconcile inside the test process) and never the shared km.jd.STATE (a module object
        # every later test module's load re-points at its own tempdir)
        from pathlib import Path
        sb = load_source("romp_sdk_backend_rows", os.path.join(BIN, "romp_sdk_backend.py"))
        root = Path(tempfile.mkdtemp())
        be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        _mk_thread(PARENT, TSID, name="web-comment-1", root=root)
        saved = km._sdk
        km._sdk = lambda: be
        try:
            self.assertNotIn(TSID, be.live_sessions(), "hidden from the tab/listing source")
            self.assertIn(TSID, be.thread_sessions(), "served only to callers that ask")
            self.assertNotIn(TSID, [r["id"] for r in km._session_rows()])
        finally:
            km._sdk = saved


class ThreadRowsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._saved = (km._session_rows, km._thread_rows)
        km._session_rows = lambda: [{"id": PARENT, "name": "web", "state": "working"}]
        km._thread_rows = lambda: [{"id": TSID, "name": "web-comment-1", "state": "working",
                                    "thread": True, "parent": PARENT}]

    def tearDown(self):
        km._session_rows, km._thread_rows = self._saved

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_new_with_a_threads_name_opens_the_thread_never_a_namesake(self):
        # T223 (2026-09-01): a model-set sweep drove `romp new --model … <name>` over every reg,
        # threads included; /new's already-live check hides threads by design, so for 20 dormant
        # thread names it CREATED 18 namesake top-level sessions (each with its own CLI process,
        # each a tab). A thread's name must answer as the existing thread — prefs applied to it,
        # nothing minted.
        created, prefs = [], []
        saved = (km._sdk_ready, km._create_sdk_session, km._apply_new_session_prefs, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._apply_new_session_prefs = lambda sid, b: (prefs.append((sid, b.get("model"))), {"model": b.get("model")})[1]
        km._live_names = lambda tmux: {}
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            out = self._post("/new", {"name": "web-comment-1", "dir": "/tmp", "model": "claude-fable-5-1"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._apply_new_session_prefs, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, [], "a thread's name must never mint a namesake session")
        self.assertTrue(out.get("ok") and out.get("existing"), out)
        self.assertEqual(out.get("id"), TSID, "the idempotent open lands on the thread")
        self.assertTrue(out.get("thread"), "the caller learns it addressed a thread")
        self.assertEqual(out.get("parent"), PARENT)
        self.assertEqual(prefs, [(TSID, "claude-fable-5-1")], "the sweep's intent — prefs on the thread")

    def test_new_with_a_fresh_name_still_creates(self):
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda tmux: {}
        try:
            out = self._post("/new", {"name": "brand-new", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
        self.assertEqual(created, ["brand-new"])
        self.assertFalse(out.get("thread"))

    def test_a_legacy_threads_registry_name_is_gated_too(self):
        # the review's catch: a pre-naming thread's store row has NO name — its reg says
        # "thread-<hash>" (what the sweep read) and _thread_rows shows the bare hash; both must
        # refuse, or the exact three "thread-<hash>" namesakes of T223 recur
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda tmux: {}
        _mk_thread(PARENT, TSID, name=None, reg_name="thread-" + TSID[:8])
        try:
            for nm in ("thread-" + TSID[:8], TSID[:8]):
                out = self._post("/new", {"name": nm, "dir": "/tmp"})
                self.assertTrue(out.get("thread"), (nm, out))
                self.assertEqual(out.get("id"), TSID)
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, [])

    def test_a_dormant_thread_whose_parent_was_ended_is_still_gated(self):
        # _comment_kill_all flips open threads alive=False with their rows still open — a revived
        # parent finds them again, so their names stay taken
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda tmux: {}
        _mk_thread(PARENT, TSID, name="web-comment-1", alive=False)
        try:
            out = self._post("/new", {"name": "web-comment-1", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertTrue(out.get("thread"))
        self.assertEqual(created, [])

    def test_a_promoted_threads_old_name_is_free(self):
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda tmux: {}
        _mk_thread(PARENT, TSID, name="web-comment-1", status="promoted")
        try:
            self._post("/new", {"name": "web-comment-1", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, ["web-comment-1"], "promoted = a real session now; its old row holds nothing")

    def test_an_unreadable_store_refuses_instead_of_minting(self):
        # the first cut returned {} on any exception — a silently reopened door. Unverifiable refuses.
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names, km._thread_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda tmux: {}
        km._thread_names = lambda: None
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post("/new", {"name": "anything", "dir": "/tmp"})
            self.assertEqual(cm.exception.code, 503)
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names, km._thread_names = saved
        self.assertEqual(created, [])

    def test_fork_and_rename_refuse_a_threads_name(self):
        saved = km._live_names
        km._live_names = lambda tmux: {PARENT_NAME: PARENT}
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            out = self._post("/fork", {"parent": PARENT_NAME, "name": "web-comment-1"})
            self.assertFalse(out.get("ok"))
            self.assertIn("comment thread", out.get("error") or "")
            out = self._post("/rename", {"target": PARENT_NAME, "name": "web-comment-1"})
            self.assertFalse(out.get("ok"))
            self.assertIn("comment thread", out.get("error") or "")
        finally:
            km._live_names = saved
            _rm_threads()

    def test_the_ws_doors_wear_the_same_gate_before_they_act(self):
        import inspect
        src = inspect.getsource(km.Handler._dispatch_ws)
        self.assertLess(src.index("elif _thread_name_refusal(nm, _thread_names())"),
                        src.index('elif msg.get("backend") == "sdk":'),
                        "the create dialog refuses a thread's name BEFORE the sdk create arm")
        ws = inspect.getsource(km._drive) if hasattr(km, "_drive") else ""
        src2 = ws or inspect.getsource(km.Handler._dispatch_ws)
        self.assertIn("_thread_name_refusal(str(msg[\"name\"]).strip(), _thread_names())", src2 + ws,
                      "forkSession refuses a thread's name")
        self.assertIn("elif _thread_name_refusal(new, _thread_names()):", src2 + ws,
                      "renameSession refuses a thread's name")

    def test_sid_of_reaches_a_thread_by_name(self):
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            self.assertEqual(km._sid_of("web-comment-1"), TSID, "an explicit send by name lands on the thread")
            self.assertEqual(km._sid_of("no-such-name"), "no-such-name")
        finally:
            _rm_threads()

    def test_thread_names_maps_every_name_a_thread_answers_to(self):
        _mk_thread(PARENT, TSID, name="web-comment-1", reg_name="web-comment-1")
        try:
            self.assertEqual(km._thread_names(), {"web-comment-1": (TSID, PARENT)})
        finally:
            _rm_threads()

    def test_default_listing_hides_threads(self):
        rows = self._get("/sessions")
        self.assertEqual([r["id"] for r in rows], [PARENT], "every existing consumer sees exactly what it saw")

    def test_threads_param_appends_flagged_rows(self):
        rows = self._get("/sessions?threads=1")
        self.assertEqual([r["id"] for r in rows], [PARENT, TSID])
        t = rows[1]
        self.assertTrue(t.get("thread"), "flagged so the bus can mark it as a minor player")
        self.assertEqual(t.get("parent"), PARENT, "the parent sid rides for reply resolution")


class ThreadWakePinsStand(unittest.TestCase):
    """A dormant comment thread wakes on the model its reg holds — PINS STAND. The T223 rider had the
    backend's `_ensure` consult a kernel-installed hook (`SdkBackend.thread_wake_model`) that re-points a
    dormant thread registered on a SUPERSEDED full id to its family's newest at its next explicit wake.
    That targeted the artefact where a FAMILY click wrote the head's full id into the reg — an
    accidental pin. With the alias as the family default a full id in reg.model is a DELIBERATE one: the
    version submenu writes the pick verbatim, the create dialog sends a pinned family's id, and the
    marker-gated boot pass treats every post-migration head as the user's (the way back to floating is
    the Latest gesture, never a pass). With no accidental heads left to heal, the remap would override
    only deliberate pins — so the kernel wires no wake hook, and a thread pinned to a legacy version
    comes up ON that version; a thread on an alias floats as before. The backend's consult in `_ensure`
    stays as it is (inert with no hook installed)."""

    THREAD = {"threadOf": PARENT, "spawnedAt": 1700000000}   # has run before: dormant, not a fresh fork

    class _Rec:
        made = []

        def __init__(self, backend, reg):
            self.reg = dict(reg)
            self.thread = mock.Mock(is_alive=lambda: True)
            self.on_boot_settled = None
            ThreadWakePinsStand._Rec.made.append(self.reg)

        def start(self):
            pass

    def setUp(self):
        # a catalog in which claude-fable-5 IS superseded — the one shape a wake remap would act on
        self._saved = {fam: [dict(v) for v in vs] for fam, vs in km.MODEL_VERSIONS.items()}
        km.MODEL_VERSIONS["fable"][:] = [{"value": "claude-fable-5-1", "label": "Fable 5.1"},
                                         {"value": "claude-fable-5", "label": "Fable 5"},
                                         {"value": "fable", "label": "Fable (newest)"}]

    def tearDown(self):
        for fam, vs in self._saved.items():
            km.MODEL_VERSIONS[fam][:] = vs

    @staticmethod
    def _kernel_wired_hook():
        """The wake hook exactly as the kernel installs it on the backend it builds, read off
        `_sdk_locked`'s source (building the real backend in-process runs a boot reconcile — this
        file's first test says why not) and resolved against the kernel module: None when the kernel
        wires none. So the wake below runs under whatever hook the kernel would give a live backend."""
        import inspect
        import re
        m = re.search(r"thread_wake_model\s*=\s*(\w+)", inspect.getsource(km._sdk_locked))
        return getattr(km, m.group(1)) if m else None

    def _wake(self, model):
        """Wake a dormant thread registered on `model` through a hermetic backend INSTANCE wired the
        kernel's way; returns (the model the spawned session got, the model the reg holds after)."""
        from pathlib import Path
        sb = load_source("romp_sdk_backend_rows", os.path.join(BIN, "romp_sdk_backend.py"))
        root = Path(tempfile.mkdtemp())
        be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        be.thread_wake_model = self._kernel_wired_hook()
        sb.write_reg(root, TSID, {"sid": TSID, "name": "web-comment-1", "cwd": "/tmp", "alive": True,
                                  "lastSid": TSID, "model": model, **self.THREAD})
        self._Rec.made = []
        with mock.patch.object(sb, "SdkSession", self._Rec):
            be._ensure(TSID)
        return self._Rec.made[0]["model"], sb.read_reg(root, TSID).get("model")

    def test_a_dormant_thread_pinned_to_a_legacy_version_wakes_on_it(self):
        self.assertEqual(self._wake("claude-fable-5"), ("claude-fable-5", "claude-fable-5"),
                         "a full id in the reg is the user's pin — it stands at the wake, on disk too")

    def test_a_dormant_thread_on_an_alias_wakes_floating(self):
        self.assertEqual(self._wake("fable"), ("fable", "fable"),
                         "an alias auto-tracks in the CLI; nothing to do at the wake")

    def test_the_kernel_wires_no_wake_remap(self):
        import inspect
        src = inspect.getsource(km._sdk_locked)
        self.assertNotIn("thread_wake_model = _family_newest_model", src,
                         "the remap would re-point deliberate pins")
        self.assertIsNone(self._kernel_wired_hook(), "no hook at all: the backend's consult stays inert")


class ThreadRowsBuilder(unittest.TestCase):
    def test_thread_rows_join_the_name_from_the_parents_comments_store(self):
        class FakeBE:
            def thread_sessions(self):
                return {TSID: {"state": "waiting", "threadOf": PARENT}}
        saved = (km._sdk, km._load_comments, km._cwd_of)
        km._sdk = lambda: FakeBE()
        km._load_comments = lambda sid: ({"threads": [{"tid": TSID, "name": "web-comment-1"}]}
                                         if sid == PARENT else {})
        km._cwd_of = lambda sid: ""
        try:
            rows = km._thread_rows()
        finally:
            km._sdk, km._load_comments, km._cwd_of = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "web-comment-1",
                         "the comments store is where a thread's editable name lives")
        self.assertEqual(rows[0]["parent"], PARENT)
        self.assertTrue(rows[0]["thread"])


if __name__ == "__main__":
    unittest.main()
