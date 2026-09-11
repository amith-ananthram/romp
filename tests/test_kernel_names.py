#!/usr/bin/env python3
"""Session names are reserved ATOMICALLY across create, fork, rename, promote and revive (2026-09-08).

Every door that gives a session a name used to read the live snapshot and then act, with nothing
between the two. Two same-name POST /new requests both passed `nm in live` and both spawned; the WS
createSession op found nothing to focus and created a second session; the WS forkSession and
renameSession ops had no live check at all, so a fork or a rename landed on a running session's name;
a revive brought a dead session back beside a newer session that had since taken its name; a comment
thread could be named after a live session. Now _claim_name reserves the name in ONE locked step
against the pending set, every door claims before it acts — inside the creator wrappers
(_create_sdk_session, _create_codex_session, _fork_session, _comment_promote, _revive_session,
_rename_claimed, and _comment_create for threads), so no door can skip it — and
_release_name frees the name once the registration is durable or the attempt has failed.

The ORDER inside every door is claim FIRST, snapshot SECOND (review find, 2026-09-08): a door that took
its live snapshot and then claimed left a gap in which a rival could claim, register and release
entirely unseen, so both minted. With the claim taken first, the snapshot is taken while the claim is
held and necessarily lists every earlier registration; and the liveness read bypasses the pusher
cycle's scoped snapshot, so a comment create retried on the pusher thread reads the world NOW, not
at cycle start. ClaimBeforeSnapshot and ClaimReadsPastTheCycleScope pin both.

The concurrency here is EVENT-keyed: the first creator parks on a threading.Event between its claim and
its durable registration while the second door tries the same name; nothing sleeps. Synthetic fixtures
only — placeholder sids and the demo names (web / api).
"""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path
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
km = load_source("romp_kernel_names", os.path.join(BIN, "romp-kernel"))
_REAL_LIVE_NAMES = km._live_names   # the real registry reader, for the classes that exercise it
_REAL_LIVE_MAP = km._live_map        # the real liveness read, for the class that exercises the cycle scope
_REAL_THREAD_NAMES = km._thread_names     # the real store walk, for the class that parks a door inside it

SID = "11111111-2222-3333-4444-555555555555"      # a running session named web
SID2 = "66666666-7777-8888-9999-000000000000"     # what the first create mints
SID3 = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"     # what a second (duplicate) create would mint
PARENT = "88888888-9999-aaaa-bbbb-cccccccccccc"   # a commented-on session named api
TSID = "99999999-aaaa-bbbb-cccc-dddddddddddd"     # its comment thread
TSID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"    # a second thread


def _write_threads(parent, rows):
    """Comment threads as the kernel keeps them: rows in the parent's comments store plus an SDK reg
    per thread carrying threadOf — what _thread_names and _comment_thread read."""
    cdir = km.jd.STATE / "comments"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / (parent + ".json")).write_text(json.dumps({"threads": [
        {"tid": t, "sid": t, "name": n, "status": "open", "createdT": 1, "lastSeenT": 1} for t, n in rows]}))
    sdir = km.jd.STATE / "sdk"
    sdir.mkdir(parents=True, exist_ok=True)
    for t, n in rows:
        (sdir / (t + ".json")).write_text(json.dumps(
            {"sid": t, "name": n, "cwd": "/tmp", "alive": True, "threadOf": parent, "lastSid": t}))


def _rm_threads():
    for sub in ("comments", "sdk"):
        d = km.jd.STATE / sub
        if d.is_dir():
            for f in d.glob("*.json"):
                f.unlink()
    km._thread_reg_memo.clear()


LINE = "web\t/work/web\t#112233\t#ffffff\n"    # the names/<sid> entry of a session named web


@contextlib.contextmanager
def _publish_fails(path, err=28, text="No space left on device"):
    """A fault BENEATH the real writer: os.replace onto `path` (the atomic publish) raises; every other
    replace proceeds. The writer runs for real — its temp, its cleanup — so what the file and its
    directory look like afterwards is the writer's doing, not a mock's guarantee."""
    real = os.replace
    want = os.path.realpath(str(path))

    def replace(src, dst, *a, **k):
        if os.path.realpath(str(dst)) == want:
            raise OSError(err, text)
        return real(src, dst, *a, **k)
    with mock.patch.object(os, "replace", replace):
        yield


class _ParkingSdk:
    """The SDK backend as the create path touches it. With park=True the FIRST spawn waits on `gate`
    between the creator's claim and its durable registration (`entered` says it got there); every
    later spawn registers at once — so on the tree under test a second same-name create is refused
    while the first is parked, and on a tree without the claim BOTH reach spawn. Registration is
    durable when the name lands in `live`, the snapshot every door reads."""

    def __init__(self, live, park=True, raise_first=False):
        self.live, self.park, self.raise_first = live, park, raise_first
        self.spawns, self.claims_at_spawn = [], []
        self.entered, self.gate = threading.Event(), threading.Event()

    def available(self):
        return True

    def live_sessions(self):
        return {}

    def spawn(self, nm, cwd, bg="", fg="", sid=None, auth="", env=None):
        self.spawns.append(nm)
        self.claims_at_spawn.append({k: dict(v) for k, v in getattr(km, "_NAME_CLAIMS", {}).items()})
        if len(self.spawns) == 1:
            if self.raise_first:
                raise RuntimeError("the CLI would not start")
            if self.park:
                self.entered.set()
                self.gate.wait(timeout=10)
        sid = (SID2, SID3)[min(len(self.spawns), 2) - 1]
        self.live[nm] = sid                      # durable: the next live snapshot lists it
        return sid

    def connect(self, sid):
        return True


class _ThreadBE:
    """The SDK backend as the comment-thread door touches it: fork / connect / send / kill recorded,
    the claims dict sampled at the fork. fork_fails=True raises there, as a CLI that would not start."""

    def __init__(self, outer, fork_fails=False):
        self.calls, self.outer, self.fork_fails = [], outer, fork_fails

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="",
             model="", effort="", fast=""):
        self.calls.append(("fork", name, self.outer.claims()))
        if self.fork_fails:
            raise RuntimeError("the CLI would not start")
        return sid

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid))
        return True

    def kill(self, sid):
        self.calls.append(("kill", sid))
        return True


class _Base(unittest.TestCase):
    def patch(self, name, value):
        saved = getattr(km, name)
        setattr(km, name, value)
        self.addCleanup(setattr, km, name, saved)

    def patch_backend_for(self, fn):
        saved = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(fn)
        self.addCleanup(setattr, km.Sessions, "backend_for", saved)

    def setUp(self):
        self.live = {}                          # the live-name snapshot every door reads (name -> sid)
        self.patch("_live_map", lambda: {})
        self.patch("_live_names", lambda tm: dict(self.live))
        self.patch("_mark_views_dirty", lambda: None)
        self.patch("_push_session_now", lambda sid: None)
        self.patch("_push_soon", lambda: None)
        self.patch("_commands_for_cwd", lambda cwd: ([], False))
        self.patch("_pick_identity_color", lambda now=None: ("#123456", "#ffffff"))
        self.reveals = []
        self.patch("_reveal_chat_for", lambda client, msg: self.reveals.append(((client or {}).get("wid"), msg)))
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self._drop_claims)

    @staticmethod
    def _drop_claims():
        claims = getattr(km, "_NAME_CLAIMS", None)   # a failed test must not poison the next one
        if claims is not None:
            claims.clear()

    def claims(self):
        # getattr-guarded so that on a tree WITHOUT the reservation the assertion that names the defect
        # ("both creates succeeded", "forked onto a live name") is what fails, not this helper
        return {k: dict(v) for k, v in getattr(km, "_NAME_CLAIMS", {}).items()}

    def hold(self, nm, kind="create", sid=""):
        """Another door's registration in flight: a claim this test holds until it ends."""
        self.assertEqual(km._claim_name(nm, {}, kind, sid), "")
        self.addCleanup(km._release_name, nm)

    def thread_door(self):
        """The comment-thread door's harness: a commented-on session `api` (PARENT) whose tip fork never
        reads the transcript, and a fork-recording backend (self.tbe) behind Sessions.backend_for."""
        self.tbe = _ThreadBE(self)
        self.patch_backend_for(lambda sid: self.tbe)
        self.patch("_sdk_ready", lambda: True)
        path = os.path.join(self.dir, PARENT + ".jsonl")
        open(path, "w").close()
        self.patch("_sessions", lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "api", "path": path, "mtime": now}])
        self.addCleanup(_rm_threads)


class _Routes(_Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def post(self, path, body):
        # km.TOKEN, not os.environ: pytest imports every collected module before any test runs, and
        # another module may assign ROMP_SERVE_TOKEN at import after this module's kernel captured the
        # value it checks, so the env at request time depends on collection order (the fix
        # test_color_route.py carries; review find, 2026-09-08). The kernel's own token is the one the
        # handler compares against.
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": km.TOKEN,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


class ConcurrentCreates(_Routes):
    """POST /new twice for one name while the first create is between its claim and its durable
    registration: exactly one session, the other refused as being created (409)."""

    def setUp(self):
        super().setUp()
        self.be = _ParkingSdk(self.live)
        self.patch("_sdk", lambda: self.be)

    def test_two_concurrent_new_requests_mint_exactly_one_session(self):
        # on origin/main: AssertionError: 2 != 1 : both creates succeeded
        out = {}

        def first():
            out["first"] = self.post("/new", {"name": "web", "dir": self.dir, "backend": "sdk"})
        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(self.be.entered.wait(timeout=10), "the first create reached its spawn")
        st, second = self.post("/new", {"name": "web", "dir": self.dir, "backend": "sdk"})
        self.be.gate.set()
        t.join(timeout=10)
        self.assertEqual(len(self.be.spawns), 1, "both creates succeeded")
        self.assertEqual(st, 409, second)
        self.assertFalse(second.get("ok"))
        self.assertIn("being created", second.get("error") or "", "the refusal names the in-flight create")
        _, body = out["first"]
        self.assertEqual((body.get("ok"), body.get("id")), (True, SID2), body)
        self.assertEqual(self.be.claims_at_spawn[0].get("web", {}).get("kind"), "create",
                         "the name is held while the registration is in flight")
        self.assertEqual(self.claims(), {}, "…and released once the create returned")

    def test_an_unknown_backend_is_refused_by_name_before_any_claim_or_spawn(self):
        # two backends since the tmux backend's removal (2026-09-11): a third word is a 400 that names the
        # two, never mapped to a backend the asker did not name; nothing is claimed and nothing spawns
        st, r = self.post("/new", {"name": "term1", "dir": self.dir, "backend": "tmux"})
        self.assertEqual(st, 400, r)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), 'unknown backend "tmux": "sdk" (Claude Code, the default) or "codex"')
        self.assertEqual(self.be.spawns, [], "nothing spawned behind a refusal")
        self.assertEqual(self.claims(), {}, "nothing claimed either")

    def test_a_codex_create_of_a_reserved_name_is_a_409_too(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("cx1")
        spawns = []

        class _Codex:
            def spawn(self, nm, cwd, bg="", fg="", sid=None, auth=""):
                spawns.append(nm)
                return SID2
        self.patch("_codex_ready", lambda: True)
        self.patch("_codex", lambda: _Codex())
        st, r = self.post("/new", {"name": "cx1", "dir": self.dir, "backend": "codex"})
        self.assertEqual(st, 409, r)
        self.assertIn("being created", r.get("error") or "")
        self.assertEqual(spawns, [])


class WsCreate(_Base):
    """The picker's createSession op: a LIVE same-name session keeps the focus-the-existing UX; a
    RESERVED in-flight name is refused with the create text — there is nothing to focus yet."""

    def setUp(self):
        super().setUp()
        self.be = _ParkingSdk(self.live)
        self.patch("_sdk", lambda: self.be)
        self.handler = object.__new__(km.Handler)

    def client(self, wid):
        frames = []
        return {"app": "chat", "alive": True, "wid": wid,
                "send": lambda s: frames.append(json.loads(s))}, frames

    def dispatch(self, msg, client):
        km.Handler._dispatch_ws(self.handler, msg, client)

    def test_a_reserved_name_is_refused_not_focused(self):
        # on origin/main: AssertionError: 2 != 1 : both creates succeeded
        a, _fa = self.client("win-A")
        b, fb = self.client("win-B")
        t = threading.Thread(target=self.dispatch,
                             args=({"type": "createSession", "name": "web", "dir": self.dir, "backend": "sdk"}, a))
        t.start()
        self.assertTrue(self.be.entered.wait(timeout=10), "the first create reached its spawn")
        self.dispatch({"type": "createSession", "name": "web", "dir": self.dir, "backend": "sdk"}, b)
        self.be.gate.set()
        t.join(timeout=10)
        self.assertEqual(len(self.be.spawns), 1, "both creates succeeded")
        warns = [f for f in fb if f.get("type") == "warn"]
        self.assertEqual(len(warns), 1, fb)
        self.assertIn("being created", warns[0]["text"])
        self.assertEqual(self.reveals, [("win-A", {"type": "focus", "id": SID2})],
                         "the creator's window follows its session; the refused asker is focused on NOTHING")
        self.assertEqual(self.claims(), {})

    def test_a_live_name_still_focuses_the_existing_tab(self):
        # green on origin/main too: today's focus-the-existing UX is kept for a LIVE name (the guard
        # against a claim that refuses what it should focus)
        self.live["web"] = SID
        b, fb = self.client("win-B")
        self.dispatch({"type": "createSession", "name": "web", "dir": self.dir, "backend": "sdk"}, b)
        self.assertEqual(self.be.spawns, [], "nothing minted for a running name")
        self.assertEqual(self.reveals, [("win-B", {"type": "focus", "id": SID})], "today's UX: focus the running one")
        self.assertEqual([f for f in fb if f.get("type") == "warn"], [])

    def test_a_create_naming_no_backend_is_the_sdk_arm_and_a_reserved_name_warns(self):
        # no backend word = Claude Code, the default (the tmux backend's removal, 2026-09-11)
        self.hold("term1")
        self.be.park = False
        b, fb = self.client("win-B")
        self.dispatch({"type": "createSession", "name": "term1", "dir": self.dir}, b)
        self.assertEqual([f["type"] for f in fb], ["warn"], fb)
        self.assertIn("being created", fb[0]["text"])
        self.assertEqual(self.be.spawns, [], "nothing spawned behind a refusal")

    def test_an_unknown_backend_word_warns_and_creates_nothing(self):
        b, fb = self.client("win-B")
        self.dispatch({"type": "createSession", "name": "term1", "dir": self.dir, "backend": "tmux"}, b)
        self.assertEqual([f["type"] for f in fb], ["warn"], fb)
        self.assertEqual(fb[0]["text"], 'unknown backend "tmux": Claude Code ("sdk") or Codex ("codex")')
        self.assertEqual(self.be.spawns, [], "an unknown word is never mapped to a backend the asker did not name")
        self.assertEqual(self.claims(), {})

    def test_a_codex_create_of_a_reserved_name_warns_and_spawns_nothing(self):
        # the Codex arm's refusal, unpinned until now: a mutant that drops its `if not _sid` warn passed
        # every test (review find, 2026-09-08). On that mutant: Lists differ: [] != ['warn']
        self.hold("cx1")
        spawns = []

        class _Codex:
            def spawn(self, nm, cwd, bg="", fg="", sid=None, auth=""):
                spawns.append(nm)
                return SID2
        self.patch("_codex_ready", lambda: True)
        self.patch("_codex", lambda: _Codex())
        b, fb = self.client("win-B")
        self.dispatch({"type": "createSession", "name": "cx1", "dir": self.dir, "backend": "codex"}, b)
        self.assertEqual([f["type"] for f in fb], ["warn"], fb)
        self.assertIn("being created", fb[0]["text"])
        self.assertEqual(spawns, [], "nothing spawned behind a refusal")
        self.assertEqual(self.reveals, [], "nothing to focus")


class FailurePathsFreeTheName(_Base):
    def test_a_create_that_raises_leaves_the_name_free_for_the_retry(self):
        # on origin/main: AssertionError: None != 'create' : the name was held when the create blew up
        be = _ParkingSdk(self.live, park=False, raise_first=True)
        self.patch("_sdk", lambda: be)
        with self.assertRaises(RuntimeError):
            km._create_sdk_session("web", self.dir)
        self.assertEqual(be.claims_at_spawn[0].get("web", {}).get("kind"), "create",
                         "the name was held when the create blew up")
        self.assertEqual(self.claims(), {}, "a failed create must leave the name free")
        sid, extra = km._create_sdk_session("web", self.dir)
        self.assertEqual(sid, SID3, extra)
        self.assertEqual(self.claims(), {})

    def test_a_refused_create_answers_the_failed_create_shape_flagged_as_a_name_conflict(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("web")
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        sid, extra = km._create_sdk_session("web", self.dir)
        self.assertEqual(sid, "")
        self.assertTrue(extra.get("nameTaken"))
        self.assertIn("being created", extra.get("error") or "")
        self.assertEqual(be.spawns, [])


class RenameDoors(_Routes):
    def setUp(self):
        super().setUp()
        self.live = {"web": SID}
        self.renames, self.claims_at_rename = [], []
        outer = self

        class _BE:
            accept = True                       # False: the backend declines (a sid it does not know)

            def rename(self, sid, name):
                outer.claims_at_rename.append(outer.claims())
                outer.renames.append((sid, name))
                return self.accept
        self.be = _BE()
        self.patch_backend_for(lambda sid: self.be)
        self.patch("_kernel_knows", lambda sid: True)

    def test_rename_route_onto_a_name_being_created_is_refused(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("web2")
        st, r = self.post("/rename", {"target": "web", "name": "web2"})
        self.assertFalse(r.get("ok"), r)
        self.assertIn("being created", r.get("error") or "")
        self.assertEqual(self.renames, [])

    def test_rename_route_onto_a_live_name_is_refused(self):
        # green on origin/main too (the route's own check refused this); kept as the guard for the
        # reordered route, whose live-name refusal is now the claim's
        self.live["api"] = SID2
        st, r = self.post("/rename", {"target": "web", "name": "api"})
        self.assertFalse(r.get("ok"), r)
        self.assertIn("already running", r.get("error") or "")
        self.assertEqual(self.renames, [])

    def test_rename_route_onto_its_own_name_acks_without_a_write(self):
        # on origin/main: AssertionError: False is not true : {'ok': False, 'error': 'a session named "web" is already running…'}
        self.patch("_name_of", lambda sid: "web" if sid == SID else None)
        st, r = self.post("/rename", {"target": "web", "name": "web"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual((r.get("id"), r.get("name")), (SID, "web"))
        self.assertEqual(self.renames, [], "nothing to write")

    def test_a_free_name_is_held_through_the_backend_write_and_released_after(self):
        # on origin/main: AssertionError: None != 'rename'
        st, r = self.post("/rename", {"target": "web", "name": "web2"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.renames, [(SID, "web2")])
        self.assertEqual(self.claims_at_rename[0].get("web2", {}).get("kind"), "rename")
        self.assertEqual(self.claims(), {})

    def test_ws_rename_is_refused_on_a_reserved_or_live_name_and_acks_its_own(self):
        # on origin/main: AttributeError: '_claim_name'
        frames = []
        client = {"send": lambda s: frames.append(json.loads(s))}
        self.hold("web2")
        self.assertTrue(km._drive({"type": "renameSession", "id": SID, "name": "web2"}, client))
        self.assertEqual(frames[-1]["type"], "warn", frames)
        self.assertIn("being created", frames[-1]["text"])
        self.live["api"] = SID2
        km._drive({"type": "renameSession", "id": SID, "name": "api"}, client)
        self.assertIn("already running", frames[-1]["text"])
        self.assertEqual(self.renames, [], "the WS door wrote nothing onto a taken name")
        self.patch("_name_of", lambda sid: "web" if sid == SID else None)
        km._drive({"type": "renameSession", "id": SID, "name": "web"}, client)
        self.assertEqual(frames[-1], {"type": "renamed", "id": SID, "name": "web"}, "own name: an ack")
        self.assertEqual(self.renames, [])
        km._drive({"type": "renameSession", "id": SID, "name": "web3"}, client)
        self.assertEqual(frames[-1], {"type": "renamed", "id": SID, "name": "web3"})
        self.assertEqual(self.renames, [(SID, "web3")])
        self.assertEqual(self.claims(), {"web2": {"kind": "create", "sid": ""}},
                         "the rename's claim is released; the planted in-flight create still stands")

    def test_ws_rename_declined_by_the_backend_is_said_and_releases_the_claim(self):
        # the op used to answer NOTHING on a decline; the PR's warn was unpinned until now (review find,
        # 2026-09-08). With the warn replaced by `pass`: Lists differ: [] != ['warn']
        frames = []
        client = {"send": lambda s: frames.append(json.loads(s))}
        self.be.accept = False
        self.assertTrue(km._drive({"type": "renameSession", "id": SID, "name": "web2"}, client))
        self.assertEqual([f["type"] for f in frames], ["warn"], frames)
        self.assertIn("the rename did not take", frames[0]["text"])
        self.assertEqual(self.renames, [(SID, "web2")], "the backend was asked, and declined")
        self.assertEqual(self.claims(), {}, "the declined rename's claim is released")

    def _raising_backend(self):
        # a names-file write that failed under the backend's rename (ENOSPC here): the backends
        # compensate and RE-RAISE so the asker hears it — and the doors must turn that into words
        def raising(sid, name):
            self.renames.append((sid, name))
            raise OSError(28, "No space left on device")
        self.be.rename = raising

    def test_a_backend_that_raises_is_said_to_the_ws_asker_and_releases_the_claim(self):
        # on origin/main: OSError: [Errno 28] No space left on device — escaped km._drive; live, the
        # receive loop's catch-all logged it and the client heard nothing
        frames = []
        client = {"send": lambda s: frames.append(json.loads(s))}
        self._raising_backend()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._drive({"type": "renameSession", "id": SID, "name": "web2"}, client))
        self.assertEqual([f["type"] for f in frames], ["warn"], frames)
        self.assertIn("the rename did not take", frames[0]["text"])
        self.assertIn("No space left on device", frames[0]["text"], "the asker hears WHY")
        self.assertEqual(self.renames, [(SID, "web2")], "the backend was asked, and raised")
        self.assertEqual(self.claims(), {}, "the failed rename's claim is released")

    def test_a_backend_that_raises_is_said_to_the_route_asker(self):
        # on origin/main: do_POST's catch-all answered a 500 whose body is a traceback, not JSON
        self._raising_backend()
        with contextlib.redirect_stderr(io.StringIO()):
            st, r = self.post("/rename", {"target": "web", "name": "web2"})
        self.assertEqual(st, 200, r)
        self.assertFalse(r.get("ok"), r)
        self.assertIn("the rename did not take", r.get("error") or "")
        self.assertIn("No space left on device", r.get("error") or "", "the asker hears WHY")
        self.assertEqual(self.claims(), {}, "the failed rename's claim is released")


class ForkDoor(_Base):
    def setUp(self):
        super().setUp()
        self.live = {"web": SID}
        self.forks = []
        self.patch("_fork_session_inner",
                   lambda psid, at, nm, now=None, client=None: self.forks.append((nm, self.claims())) or None)
        self.patch("_kernel_knows", lambda sid: True)
        self.patch_backend_for(lambda sid: object())
        self.frames = []
        self.client = {"send": lambda s: self.frames.append(json.loads(s))}

    def test_the_ws_fork_op_refuses_a_live_name(self):
        # on origin/main: AssertionError: [('web', {})] != [] : the WS fork door forked onto a live name
        km._drive({"type": "forkSession", "id": SID, "name": "web"}, self.client)
        self.assertEqual(self.forks, [], "the WS fork door forked onto a live name")
        self.assertIn("already running", self.frames[-1]["text"])

    def test_the_ws_fork_op_refuses_a_name_being_created(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("web-fork")
        km._drive({"type": "forkSession", "id": SID, "name": "web-fork"}, self.client)
        self.assertEqual(self.forks, [])
        self.assertIn("being created", self.frames[-1]["text"])

    def test_a_free_fork_name_is_held_through_the_fork_and_released_after(self):
        # on origin/main: AssertionError: None != {'kind': 'fork', 'sid': SID}
        km._drive({"type": "forkSession", "id": SID, "name": "web-fork"}, self.client)
        self.assertEqual([n for n, _ in self.forks], ["web-fork"])
        self.assertEqual(self.forks[0][1].get("web-fork"), {"kind": "fork", "sid": SID})
        self.assertEqual(self.claims(), {})
        self.assertEqual(self.frames, [], "no refusal on success — the fork's own push carries the tab")


class PromoteDoor(_Base):
    def setUp(self):
        super().setUp()
        self.live = {"web": SID}
        self.promotes = []
        self.patch("_comment_promote_inner",
                   lambda parent, tid, nm, now=None, client=None: self.promotes.append((nm, self.claims())) or None)
        self.patch("_sdk_ready", lambda: True)

        class _BE:
            def promote_thread(self, sid, name, bg="", fg=""):
                return True
        self.patch_backend_for(lambda sid: _BE())
        _write_threads(PARENT, [(TSID, "api-comment-1"), (TSID2, "api-comment-2")])
        self.addCleanup(_rm_threads)

    def test_promote_onto_a_live_sessions_name_is_refused(self):
        # on origin/main: AttributeError: '_comment_promote_inner' (setUp patches the inner)
        err = km._comment_promote(PARENT, TSID, "web")
        self.assertIn("already running", err or "")
        self.assertEqual(self.promotes, [])

    def test_promote_onto_a_name_being_created_is_refused(self):
        # on origin/main: AttributeError: '_comment_promote_inner' (setUp patches the inner)
        self.hold("sidework")
        err = km._comment_promote(PARENT, TSID, "sidework")
        self.assertIn("being created", err or "")
        self.assertEqual(self.promotes, [])

    def test_promote_onto_another_threads_name_is_refused_but_its_own_is_fine(self):
        # on origin/main: AttributeError: '_comment_promote_inner' (setUp patches the inner)
        err = km._comment_promote(PARENT, TSID, "api-comment-2")
        self.assertIn("is a comment thread of", err or "")
        self.assertIsNone(km._comment_promote(PARENT, TSID, "api-comment-1"), "a thread promotes under its own name")
        self.assertEqual([n for n, _ in self.promotes], ["api-comment-1"])
        self.assertEqual(self.promotes[0][1].get("api-comment-1"), {"kind": "promote", "sid": TSID})
        self.assertEqual(self.claims(), {})


class ReviveDoor(_Base):
    class _Sdk:
        def __init__(self, outer):
            self.calls, self.outer = [], outer

        def owns(self, sid):
            return True

        def resume(self, name, sid, cwd=None):
            self.calls.append(("resume", name, sid, self.outer.claims()))
            return True

        def connect(self, sid):
            self.calls.append(("connect", sid))
            return True

    def setUp(self):
        super().setUp()
        self.sdk = self._Sdk(self)
        self.patch("_sdk", lambda: self.sdk)
        self.patch("_codex", lambda: None)          # the inner asks the Codex backend too — none here
        self.patch("_name_of", lambda sid: "web" if sid == SID else None)
        self.patch("_cwd_of", lambda sid: self.dir)
        self.sent = []
        self.patch("_send_to_view", lambda app, msg, wid: self.sent.append((app, msg, wid)))
        self.client = {"app": "chat", "wid": "win-A"}

    def test_revive_refuses_when_a_newer_live_session_wears_the_name(self):
        # on origin/main: AssertionError: [('resume', 'web', SID), ('connect', SID)] != [] : the dead session came up beside a live namesake
        self.live = {"web": SID2}
        km._revive_session(SID, self.client)
        self.assertEqual([c[:3] for c in self.sdk.calls], [], "the dead session came up beside a live namesake")
        # the asker's chat AND feed hear it (the feed's parked card latched Revive on the click and re-arms
        # on the refusal for its own sid; review find 2026-09-08), the shape every other revive refusal takes
        self.assertEqual([(a, m["type"], m["id"], w) for a, m, w in self.sent],
                         [("chat", "reviveFailed", SID, "win-A"), ("feed", "reviveFailed", SID, "win-A")])
        self.assertIn("already running", self.sent[0][1]["text"])
        self.assertEqual(self.reveals, [], "a refused revive focuses nothing")

    def test_revive_refuses_a_name_being_registered_right_now(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("web")
        km._revive_session(SID, self.client)
        self.assertEqual(self.sdk.calls, [])
        self.assertEqual([a for a, _, _ in self.sent], ["chat", "feed"], "the asker's chat and feed both hear it")
        self.assertIn("being created", self.sent[0][1]["text"])

    def test_a_free_name_is_held_through_the_resume_and_released_after(self):
        # on origin/main: AssertionError: None != {'kind': 'revive', 'sid': SID}
        km._revive_session(SID, self.client)
        self.assertEqual([c[:3] for c in self.sdk.calls], [("resume", "web", SID), ("connect", SID)])
        self.assertEqual(self.sdk.calls[0][3].get("web"), {"kind": "revive", "sid": SID}, "held while resume wrote the reg")
        self.assertEqual(self.claims(), {})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.reveals, [("win-A", {"type": "focus", "id": SID})])

    def test_revive_refused_on_a_threads_name_is_worded_for_what_a_revive_can_do(self):
        # a revive asks for the dead session's OWN name and cannot pick another; the T223 refusal told it
        # to (review find, 2026-09-08). On the PR head: 'pick another name' unexpectedly found in the text
        _write_threads(PARENT, [(TSID, "web")])
        self.addCleanup(_rm_threads)
        km._revive_session(SID, self.client)
        self.assertEqual(self.sdk.calls, [], "the dead session came up beside a thread wearing its name")
        text = self.sent[0][1]["text"]
        self.assertIn("is a comment thread of", text)
        self.assertNotIn("pick another name", text)
        self.assertIn("rename this session first", text, "what a revive CAN do: rename the dead tab, then revive")
        self.assertEqual(self.claims(), {})

    def test_a_session_that_is_already_up_is_not_a_collision_with_itself(self):
        # green on origin/main too (no claim to collide with); the guard for the own-row exclusion
        self.live = {"web": SID}
        km._revive_session(SID, self.client)
        self.assertEqual(self.sent, [], "its own live row is not a rival")
        self.assertEqual([c[0] for c in self.sdk.calls], ["resume", "connect"])


class ThreadNames(_Base):
    """A comment thread's name shares the session namespace, both ways."""

    def setUp(self):
        super().setUp()
        self.live = {"web": SID}
        self.thread_door()
        self.be = self.tbe

    def create(self, name=""):
        return km._comment_create(PARENT, "", "a passage", "a note", name=name)

    def test_a_thread_may_not_take_a_live_sessions_name(self):
        # on origin/main: AssertionError: unexpectedly None : the thread was created under a live session's name
        err, tid = self.create(name="web")
        self.assertIsNotNone(err, "the thread was created under a live session's name")
        self.assertIn("already running", err)
        self.assertEqual(self.be.calls, [])
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [], "no row for a refused thread")

    def test_a_thread_may_not_take_a_name_being_created(self):
        # on origin/main: AttributeError: '_claim_name'
        self.hold("web2")
        err, _ = self.create(name="web2")
        self.assertIn("being created", err or "")
        self.assertEqual(self.be.calls, [])

    def test_a_session_may_not_take_a_thread_name_being_created(self):
        # the reverse, in flight: the thread's claim (kind thread) refuses every create door
        # on origin/main: AttributeError: '_claim_name'
        self.hold("api-comment-1", "thread", TSID)
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        sid, extra = km._create_sdk_session("api-comment-1", self.dir)
        self.assertEqual((sid, be.spawns), ("", []))
        self.assertIn("being created", extra.get("error") or "")
        self.assertIn("being created", km._fork_session(PARENT, "", "api-comment-1") or "")

    def test_the_default_name_skips_taken_and_in_flight_names(self):
        # on origin/main: AttributeError: '_claim_name' (the held name); without it the default would
        # come out 'api-comment-2', the very name in flight
        _write_threads(PARENT, [(TSID, "api-comment-1")])
        self.hold("api-comment-2", "thread", TSID2)
        err, tid = self.create()
        self.assertIsNone(err)
        self.assertEqual(km._comment_thread(PARENT, tid)["name"], "api-comment-3",
                         "the count says 2, but that name is being registered — never a namesake")
        self.assertEqual(self.be.calls[0][2].get("api-comment-3"), {"kind": "thread", "sid": tid},
                         "held while the fork registered")
        self.assertEqual(self.claims(), {"api-comment-2": {"kind": "thread", "sid": TSID2}}, "released after; the held one stands")

    def test_a_registered_thread_name_is_refused_by_every_wrapper_with_the_doors_pre_check_bypassed(self):
        # the claim's own snapshot must carry the thread names: a thread is never in the live set, so a
        # wrapper claiming against live names alone let a session land on a thread's name whenever the
        # door's unlocked pre-check was passed or skipped (review find F8)
        # on origin/main: AssertionError: ['api-comment-1'] != [] : the create landed on a thread's name
        _write_threads(PARENT, [(TSID, "api-comment-1")])
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        sid, extra = km._create_sdk_session("api-comment-1", self.dir)
        self.assertEqual(be.spawns, [], "the create landed on a thread's name")
        self.assertEqual(sid, "")
        self.assertTrue(extra.get("nameTaken"))
        self.assertIn("is a comment thread of", extra.get("error") or "")
        self.assertIn("is a comment thread of", km._fork_session(PARENT, "", "api-comment-1") or "")
        renames = []

        class _BE:
            def rename(self, sid, nm):
                renames.append(nm)
                return True
        ok, refusal = km._rename_claimed(_BE(), SID, "api-comment-1")
        self.assertEqual((ok, renames), (False, []))
        self.assertIn("is a comment thread of", refusal)
        self.assertEqual(self.claims(), {})

    def test_a_failing_row_save_propagates_and_frees_the_name(self):
        # the try/finally begins AT the claim: a _save_comments that raises used to leave the name held
        # for the kernel's life, every later create of it refused as in flight (review find F9)
        # on origin/main: AssertionError: Lists differ: [None] != ['thread'] : the name was held when the save blew up
        seen = []

        def boom(sid, data):
            seen.append(self.claims().get("review", {}).get("kind"))
            raise OSError("disk full")
        with mock.patch.object(km, "_save_comments", boom):
            with self.assertRaises(OSError):
                self.create(name="review")
        self.assertEqual(seen, ["thread"], "the name was held when the save blew up")
        self.assertEqual(self.claims(), {}, "a save that raised must not leave the name held")
        self.assertEqual(self.be.calls, [], "nothing forked behind a failed save")
        err, tid = self.create(name="review")
        self.assertIsNone(err, "the retry takes the freed name")
        self.assertEqual(km._comment_thread(PARENT, tid)["name"], "review")

    def test_a_thread_whose_fork_fails_frees_its_name(self):
        # on origin/main: AssertionError: None != 'thread' : the name was held when the fork blew up
        self.be.fork_fails = True
        err, _ = self.create(name="review")
        self.assertIn("thread not created", err or "")
        self.assertEqual(self.be.calls[0][2].get("review", {}).get("kind"), "thread",
                         "the name was held when the fork blew up")
        self.assertEqual(self.claims(), {})
        self.be.fork_fails = False
        err, tid = self.create(name="review")
        self.assertIsNone(err, "the retry takes the freed name")
        self.assertEqual(km._comment_thread(PARENT, tid)["name"], "review")


class DeadTabRenamePublishesTheName(_Base):
    """A DEAD tab's rename (the unowned route, _UNOWNED.rename → _rename_session) publishes names/<sid>
    synchronously, BEFORE the claim is released, so the next snapshot answers the new name; a backend
    that declines publishes nothing and the doors say so."""

    def setUp(self):
        super().setUp()
        names = Path(tempfile.mkdtemp())
        (names / SID).write_text("web\t/work/web\t#112233\t#ffffff\n")
        self.patch("NAMES", names)
        self.patch("_live_names", _REAL_LIVE_NAMES)      # the real registry reader over a private registry
        self.patch("_live_map", lambda: {SID: {}})       # a snapshot that lists the sid: what a release-time read answers
        self.patch("_codex", lambda: None)

    def test_the_snapshot_answers_the_new_name_when_the_claim_is_released(self):
        at_release, real_release = [], km._release_name
        self.patch("_release_name", lambda nm: (at_release.append(dict(km._live_names(km._live_map()))),
                                                real_release(nm)))
        ok, refusal = km._rename_claimed(km._UNOWNED, SID, "web2")
        self.assertEqual((ok, refusal), (True, ""))
        self.assertEqual(at_release, [{"web2": SID}], "the snapshot already answered the new name at the release")
        self.assertEqual((km.NAMES / SID).read_text(), "web2\t/work/web\t#112233\t#ffffff\n", "dir and colours preserved")
        self.assertEqual(self.claims(), {})

    def test_a_rename_the_backend_declined_publishes_nothing_and_is_reported(self):
        class _Declining:
            def rename(self, sid, nm):
                return False
        ok, refusal = km._rename_claimed(_Declining(), SID, "web2")
        self.assertEqual((ok, refusal), (False, ""), "the backend declined — the doors say so")
        self.assertEqual(km._live_names(km._live_map()), {"web": SID}, "a name the backend refused is never published")
        self.assertEqual(self.claims(), {})

    def test_a_dead_tab_rename_with_no_names_entry_is_reported_not_claimed(self):
        # on origin/main: AssertionError: 'web2' is not None — _set_name's silent return on an
        # unreadable entry read as renamed, and the doors said "renamed" over nothing written
        (km.NAMES / SID).unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._rename_session(SID, "web2"), "no entry to rewrite: the rename did not take")
        self.assertFalse((km.NAMES / SID).exists(),
                         "no entry is invented: one with an empty dir would break every transcript path")


class RenameFaultsThroughTheDoors(_Routes):
    """What the asker hears when the names file cannot follow a rename, through BOTH doors (the WS
    renameSession arm and POST /rename) over the unowned route (a DEAD tab: no running backend owns the
    sid, so Sessions.backend_for answers _UNOWNED, whose rename is the names-file rewrite) and a real
    names registry, with the fault injected BENEATH the writer (the atomic publish fails), so the file's
    state afterwards is the writer's doing. A dead tab's fault used to reach the asker as the
    pre-existing "is that session known to this kernel?" — a false cause — while the same fault one
    backend over (SDK) said the errno (review, 2026-09-08)."""

    def setUp(self):
        super().setUp()
        self.names = Path(tempfile.mkdtemp())
        (self.names / SID).write_text(LINE)
        self.patch("NAMES", self.names)
        self.patch("_live_names", _REAL_LIVE_NAMES)      # the real registry reader over a private registry
        self.patch("_kernel_knows", lambda sid: True)
        self.patch("_codex", lambda: None)
        self.patch_backend_for(lambda sid: km._UNOWNED)
        self.frames, self.err = [], io.StringIO()
        self.client = {"send": lambda s: self.frames.append(json.loads(s))}

    def as_dead(self):   # (not `dead`/`live`: _Base.setUp owns self.live, the snapshot dict)
        self.patch("_live_map", lambda: {})

    def ws(self, name="web2"):
        with contextlib.redirect_stderr(self.err):
            self.assertTrue(km._drive({"type": "renameSession", "id": SID, "name": name}, self.client))
        return self.frames[-1]

    def route(self, target, name="web2"):
        with contextlib.redirect_stderr(self.err):
            return self.post("/rename", {"target": target, "name": name})

    def test_a_dead_tab_fault_reaches_the_ws_asker_as_the_errno(self):
        # on origin/main: OSError: [Errno 28] No space left on device — escaped km._drive
        self.as_dead()
        with _publish_fails(self.names / SID):
            f = self.ws()
        self.assertEqual(f["type"], "warn", f)
        self.assertIn("the rename did not take — [Errno 28] No space left on device", f["text"])
        self.assertNotIn("is that session known", f["text"], "a fault is not an unknown session")
        self.assertEqual((self.names / SID).read_text(), LINE)
        self.assertEqual(sorted(p.name for p in self.names.iterdir()), [SID], "no temp left behind")
        self.assertEqual(self.claims(), {}, "the claim is released")

    def test_a_dead_tab_fault_reaches_the_route_asker_as_the_errno(self):
        # on origin/main: json.decoder.JSONDecodeError — the route answered a 500 traceback
        self.as_dead()
        with _publish_fails(self.names / SID):
            st, r = self.route(SID)                 # dead: no live name resolves, so the sid is the target
        self.assertEqual(st, 200, r)
        self.assertFalse(r.get("ok"), r)
        self.assertIn("the rename did not take — [Errno 28] No space left on device", r.get("error") or "")
        self.assertEqual(self.claims(), {})

    def test_a_dead_tab_with_no_entry_is_the_one_case_that_asks_if_the_session_is_known(self):
        # on origin/main: 'renamed' != 'warn' — the silent return read as renamed
        self.as_dead()
        (self.names / SID).unlink()
        f = self.ws()
        self.assertEqual(f["type"], "warn", f)
        self.assertIn("is that session known to this kernel", f["text"])
        self.assertFalse((self.names / SID).exists(), "no entry is invented")

    def test_a_dead_codex_tab_fault_reaches_the_asker_as_the_errno_too(self):
        # on origin/main: the Codex branch's catch mapped the raise to None → "is that session known to
        # this kernel?", a false cause — the same class this commit fixes for the tmux and SDK paths.
        # A REAL CodexBackend over its own state dir: a dead Codex tab is not owned (owns() is alive-only),
        # so the doors route it to the unowned route, whose dead-tab rename renames the Codex registry's
        # durable name first; the fault is beneath its real names writer (the atomic publish)
        cb = load_source("romp_codex_backend_names",
                              os.path.join(os.path.dirname(HERE), "kernel", "codex_backend.py"))
        st = Path(tempfile.mkdtemp())
        cx = cb.CodexBackend(st, client_factory=lambda: None)
        self.assertEqual(cx.spawn("web", "/work/web", sid=SID), SID)
        self.names = st / "names"
        (self.names / SID).write_text(LINE)      # the colours the palette picker adds later; the writer preserves them
        self.patch("NAMES", self.names)
        self.patch("_codex", lambda: cx)
        self.as_dead()
        with _publish_fails(self.names / SID):
            f = self.ws()
        self.assertEqual(f["type"], "warn", f)
        self.assertIn("the rename did not take — [Errno 28] No space left on device", f["text"])
        self.assertNotIn("is that session known", f["text"], "a fault is not an unknown session")
        self.assertEqual((self.names / SID).read_text(), LINE, "the real writer left the file byte-identical")
        self.assertEqual(sorted(p.name for p in self.names.iterdir()), [SID], "no temp left behind")
        self.assertEqual(cx._session(SID).name, "web", "the Codex backend's in-memory name never moved")
        rows = json.loads((st / "codex" / "registry.json").read_text())
        self.assertEqual(rows[SID]["name"], "web", "the Codex registry write was re-run with the old name")
        self.assertEqual(self.claims(), {}, "the claim is released")

    def _no_name_record(self):
        """names/<SID> reads with no name: 0 bytes, another writer's window or a damaged file. The writer's
        one re-read runs for real here (a short pause); the error-center ring is restored afterwards."""
        (self.names / SID).write_text("")
        saved = list(km._SDK_BOOT_PROBLEMS)
        self.addCleanup(lambda: km._SDK_BOOT_PROBLEMS.__setitem__(slice(None), saved))

    def test_a_dead_tab_rename_over_a_record_with_no_name_is_refused_with_the_cause(self):
        # before the guard: 'renamed' != 'warn', and the file read web2\t\t\t\n (cwd and colors erased)
        self.as_dead()
        self._no_name_record()
        f = self.ws()
        self.assertEqual(f["type"], "warn", f)
        self.assertIn("the rename did not take", f["text"])
        self.assertIn("names/%s reads with no name" % SID, f["text"], "the cause, naming the record")
        self.assertNotIn("is that session known", f["text"], "a record with no name is not an unknown session")
        self.assertEqual((self.names / SID).read_text(), "", "left byte for byte as it was")
        self.assertEqual(sorted(p.name for p in self.names.iterdir()), [SID], "no temp left behind")
        self.assertIn(SID, self.err.getvalue(), "the log names the sid")
        self.assertEqual(self.claims(), {}, "the claim is released")

    def test_a_dead_tab_rename_over_a_record_with_no_name_answers_the_route_ok_false(self):
        # before the guard: ok True, id SID, over a file rewritten as web2\t\t\t\n
        self.as_dead()
        self._no_name_record()
        st, r = self.route(SID)                     # dead: no live name resolves, so the sid is the target
        self.assertEqual(st, 200, r)
        self.assertFalse(r.get("ok"), r)
        self.assertIn("the rename did not take", r.get("error") or "")
        self.assertIn("reads with no name", r.get("error") or "")
        self.assertEqual((self.names / SID).read_text(), "")
        self.assertEqual(self.claims(), {})

class LockDiscipline(_Base):
    """The snapshot is taken OUTSIDE the claims lock by every caller; the check-and-insert is INSIDE it."""

    def test_no_door_takes_its_live_snapshot_under_the_claims_lock(self):
        # on origin/main: AttributeError: '_comment_promote_inner' (the create and fork doors of main
        # take no snapshot, so the lock-sampling stubs are never called before that patch)
        held = []
        self.patch("_live_names", lambda tm: held.append(km._name_claims_lock.locked()) or dict(self.live))
        self.patch("_thread_names", lambda: held.append(km._name_claims_lock.locked()) or {})
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        self.assertEqual(km._create_sdk_session("web", self.dir)[0], SID2)
        self.patch("_fork_session_inner", lambda *a, **k: None)
        self.assertIsNone(km._fork_session(SID, "", "web-fork"))
        self.patch("_comment_promote_inner", lambda *a, **k: None)
        self.assertIsNone(km._comment_promote(PARENT, TSID, "sidework"))
        self.patch("_revive_session_inner", lambda sid, client=None: None)
        self.patch("_name_of", lambda sid: "dead")
        km._revive_session(SID3)

        class _BE:
            def rename(self, sid, nm):
                return True
        self.assertEqual(km._rename_claimed(_BE(), SID, "web3"), (True, ""))

        class _Codex:
            def spawn(self, nm, cwd, bg="", fg="", sid=None, auth=""):
                return SID3
        self.patch("_codex_ready", lambda: True)
        self.patch("_codex", lambda: _Codex())
        self.assertEqual(km._create_codex_session("cx1", self.dir)[0], SID3)
        # the thread door too (the seventh the body counts; the first cut sampled six, review find 2026-09-08)
        self.thread_door()
        err, tid = km._comment_create(PARENT, "", "a passage", "a note", name="side")
        self.assertIsNone(err, "the thread door minted")
        self.assertGreaterEqual(len(held), 14, "every door took both snapshots (live + threads): seven doors")
        self.assertEqual(set(held), {False}, "a snapshot was taken while the claims lock was held")
        self.assertEqual(self.claims(), {})

    def test_the_check_and_the_insert_are_one_step_under_the_lock(self):
        # on origin/main: AttributeError: '_NAME_CLAIMS'
        outer = self

        class _Guarded(dict):
            def get(self, k, d=None):
                outer.assertTrue(km._name_claims_lock.locked(), "the pending set was read outside the lock")
                return dict.get(self, k, d)

            def __setitem__(self, k, v):
                outer.assertTrue(km._name_claims_lock.locked(), "the claim was written outside the lock")
                dict.__setitem__(self, k, v)

            def pop(self, k, d=None):
                outer.assertTrue(km._name_claims_lock.locked(), "the release ran outside the lock")
                return dict.pop(self, k, d)
        self.patch("_NAME_CLAIMS", _Guarded())
        self.assertEqual(km._claim_name("web", {}, "create"), "")
        self.assertIn("being created", km._claim_name("web", {}, "fork"))
        self.assertIn("already running", km._claim_name("api", {"api": SID}, "create"))
        km._release_name("web")
        self.assertEqual(dict(km._NAME_CLAIMS), {})
        self.assertFalse(km._name_claims_lock.locked(), "the lock is never left held")

    def test_the_refusal_names_what_the_holder_is_doing(self):
        self.hold("web2", "rename", SID)
        self.assertIn("being renamed to", km._claim_name("web2", {}, "create"))
        self.hold("web3", "revive", SID2)
        self.assertIn("being revived", km._claim_name("web3", {}, "create"))
        self.hold("web4", "thread", TSID)
        self.assertIn("being created", km._claim_name("web4", {}, "create"))
        # a revive cannot pick another name, so its refusal does not tell it to
        self.assertNotIn("pick another name", km._claim_name("web4", {}, "revive", SID3))
        self.assertIn("pick another name", km._claim_name("web4", {}, "create"))


class ClaimBeforeSnapshot(_Base):
    """The claim PRECEDES the snapshot. A door that took its live snapshot first and claimed after left a
    gap in which a rival could claim, register and release entirely unseen: the dict was empty again
    and the snapshot predated the registration, so BOTH minted (review find, 2026-09-08, reproduced by a
    deterministic probe on the first cut). Here one caller is parked inside its comments-store walk (an
    Event keyed on its thread id), a rival runs the same create to completion, then the parked caller
    resumes. On the old order the park sits AFTER the live snapshot and BEFORE the claim, the rival's
    whole life fits in it, and both mint; with the claim taken first the parked caller holds the name
    through its snapshot, the rival is refused by the dict, and exactly one mints. The third case parks
    a caller at the claim's door itself: on the old order after both snapshots, on the new before any."""

    def setUp(self):
        super().setUp()
        self.parked_ident, self.park_at = [None], "_thread_names"
        self.entered, self.gate = threading.Event(), threading.Event()
        real_claim = km._claim_name

        def parked_here(where):
            if where == self.park_at and threading.get_ident() == self.parked_ident[0]:
                self.entered.set()
                self.gate.wait(timeout=10)

        def thread_names():
            # the parked caller waits HERE, after its live snapshot on the old order and after its claim
            # on the new one; every other caller walks the store at once
            parked_here("_thread_names")
            return _REAL_THREAD_NAMES()

        def claim_name(nm, live_names, kind="create", sid=""):
            # or HERE, at the claim's door: after BOTH snapshots on the old order, before any on the new
            parked_here("_claim_name")
            return real_claim(nm, live_names, kind, sid)
        self.patch("_thread_names", thread_names)
        self.patch("_claim_name", claim_name)
        self.be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: self.be)
        self.thread_door()

    def park(self, fn, at="_thread_names"):
        """Run fn on its own thread and hold it at `at` (its store walk, or the claim's door); returns
        (thread, out)."""
        out = {}
        self.park_at = at

        def run():
            self.parked_ident[0] = threading.get_ident()
            out["v"] = fn()
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(self.entered.wait(timeout=10), "the parked caller reached %s" % at)
        return t, out

    def resume(self, t):
        self.gate.set()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "the parked caller finished")

    def test_two_creates_one_parked_in_its_snapshot_mint_exactly_one(self):
        # on the PR head: AssertionError: 2 != 1 : both creates minted a "web" (the rival claimed,
        # registered and released inside the parked caller's snapshot-to-claim gap, unseen by either check)
        t, out = self.park(lambda: km._create_sdk_session("web", self.dir))
        rival = km._create_sdk_session("web", self.dir)      # runs to completion while the other is parked
        self.resume(t)
        self.assertEqual(len(self.be.spawns), 1, 'both creates minted a "web"')
        self.assertEqual(rival[0], "", rival)
        self.assertTrue(rival[1].get("nameTaken"))
        self.assertIn("being created", rival[1].get("error") or "",
                      "the rival is refused by the claim the parked caller took before its snapshot")
        self.assertEqual(out["v"][0], SID2, "the parked caller, the claim's holder, is the one that mints")
        self.assertEqual(self.claims(), {})

    def test_a_thread_create_parked_in_its_store_walk_and_a_session_create_mint_exactly_one(self):
        # the thread door's gap was wider still on the old order (a row build and a wait on the comments
        # lock between its snapshots and its claim); on the PR head: AssertionError: 2 != 1 : both the
        # thread and the session minted a "web"
        t, out = self.park(lambda: km._comment_create(PARENT, "", "a passage", "a note", name="web"))
        rival = km._create_sdk_session("web", self.dir)
        self.resume(t)
        forks = [c[1] for c in self.tbe.calls if c[0] == "fork"]
        self.assertEqual(len(self.be.spawns) + len(forks), 1, 'both the thread and the session minted a "web"')
        self.assertEqual((rival[0], self.be.spawns), ("", []), "the session create is refused by the claim the thread holds")
        self.assertIn("being created", rival[1].get("error") or "")
        err, tid = out["v"]
        self.assertIsNone(err, "the thread, the claim's holder, is the one that mints")
        self.assertEqual([row.get("name") for row in km._load_comments(PARENT).get("threads", [])], ["web"])
        self.assertEqual(self.claims(), {})

    def test_a_session_create_parked_at_its_claim_sees_a_thread_that_finished_meanwhile(self):
        # the reverse: the thread's row is saved and its claim released while the session create is parked
        # at the claim's door (after both of its snapshots on the old order, so neither lists the thread);
        # on the PR head: AssertionError: ['review'] != [] : the session landed on a thread's name
        t, out = self.park(lambda: km._create_sdk_session("review", self.dir), at="_claim_name")
        err, tid = km._comment_create(PARENT, "", "a passage", "a note", name="review")
        self.assertIsNone(err)
        self.assertEqual(self.claims(), {}, "the thread has registered and released")
        self.resume(t)
        self.assertEqual(self.be.spawns, [], "the session landed on a thread's name")
        sid, extra = out["v"]
        self.assertEqual(sid, "")
        self.assertIn("is a comment thread of", extra.get("error") or "")
        self.assertEqual(self.claims(), {})


class ClaimReadsPastTheCycleScope(_Base):
    """A claim's liveness read bypasses the pusher cycle's scoped snapshots. On the pusher thread
    _live_map() serves the cycle-start liveness map (_live_scope.snapshot) and _name_of the
    cycle-start registry (_live_scope.names), and the comment-create door runs THERE through
    _retry_parked_creates: a same-name session created, registered and released earlier in the same
    cycle was in neither snapshot nor the dict, and a parked thread create minted the namesake (review
    find, 2026-09-08). The real _live_map and _live_names run here over a private registry, and
    the scope is set on the test thread exactly as the cycle sets it, aged to before `web2` existed."""

    def setUp(self):
        super().setUp()
        names = Path(tempfile.mkdtemp())
        (names / SID2).write_text("web2\t/work/web2\t#112233\t#ffffff\n")
        self.patch("NAMES", names)
        self.patch("_live_map", _REAL_LIVE_MAP)
        self.patch("_live_names", _REAL_LIVE_NAMES)
        saved = km.Sessions.__dict__["live"]
        km.Sessions.live = staticmethod(lambda: {SID2: {"state": "ready"}})   # web2 is live NOW
        self.addCleanup(setattr, km.Sessions, "live", saved)
        km._live_scope.snapshot, km._live_scope.names = {}, {}   # the cycle's snapshots: taken before web2 was made
        self.addCleanup(self._drop_scope)
        self.thread_door()

    @staticmethod
    def _drop_scope():
        km._live_scope.snapshot = km._live_scope.names = None

    def test_a_session_door_on_a_scoped_thread_sees_a_session_the_cycle_snapshot_predates(self):
        # on the PR head: AssertionError: Tuples differ: (SID2, ['web2']) != ('', []) : minted against the cycle-start snapshot
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        sid, extra = km._create_sdk_session("web2", self.dir)
        self.assertEqual((sid, be.spawns), ("", []), "the create minted a second web2 against the cycle-start snapshot")
        self.assertIn("already running", extra.get("error") or "")
        self.assertEqual((km._live_scope.snapshot, km._live_scope.names), ({}, {}),
                         "the cycle's scope is restored for the rest of the cycle")
        self.assertEqual(self.claims(), {})

    def test_a_parked_comment_create_retried_by_the_pusher_sees_it_too(self):
        # the real call chain: _retry_parked_creates on the scoped thread; on the PR head:
        # AssertionError: ['web2'] != [] : the retry minted a thread beside the live web2
        km._parked_creates.append({"sid": PARENT, "uuid": "", "exact": "a passage", "text": "a note",
                                   "name": "web2", "model": "", "effort": "", "fast": "", "color": "", "tries": 0})
        self.addCleanup(km._parked_creates.clear)
        km._retry_parked_creates()
        self.assertEqual([c[1] for c in self.tbe.calls if c[0] == "fork"], [], "the retry minted a thread beside the live web2")
        self.assertEqual([t.get("name") for t in km._load_comments(PARENT).get("threads", [])], [])
        self.assertEqual(km._parked_creates, [], "a refused create is dropped, never re-parked")
        self.assertEqual((km._live_scope.snapshot, km._live_scope.names), ({}, {}))
        self.assertEqual(self.claims(), {})


class UnverifiableThreadStore(_Base):
    """An unreadable comments store (_thread_names None) refuses every door: an unverifiable name never
    mints (the standing rule; _thread_names' docstring records the first cut that returned {} and
    silently reopened the door). Each refusal here survived replacement by minting blind until it was
    pinned (review find, 2026-09-08): on a mutant that falls through with names = {} the create spawns
    and the thread's row is written."""

    def setUp(self):
        super().setUp()
        self.live = {"web": SID}
        self.patch("_thread_names", lambda: None)
        self.thread_door()

    def test_every_session_door_refuses_and_leaves_the_name_free(self):
        be = _ParkingSdk(self.live, park=False)
        self.patch("_sdk", lambda: be)
        sid, extra = km._create_sdk_session("web2", self.dir)
        self.assertEqual((sid, be.spawns), ("", []), "the create minted with the thread store unreadable")
        self.assertTrue(extra.get("nameTaken"))
        self.assertIn("couldn't verify", extra.get("error") or "")
        forks = []
        self.patch("_fork_session_inner", lambda psid, at, nm, now=None, client=None: forks.append(nm) or None)
        self.assertIn("couldn't verify", km._fork_session(SID, "", "web-fork") or "")
        promotes = []
        self.patch("_comment_promote_inner", lambda p, t, nm, now=None, client=None: promotes.append(nm) or None)
        self.assertIn("couldn't verify", km._comment_promote(PARENT, TSID, "sidework") or "")
        renames = []

        class _BE:
            def rename(self, sid, nm):
                renames.append(nm)
                return True
        ok, refusal = km._rename_claimed(_BE(), SID, "web3")
        self.assertFalse(ok)
        self.assertIn("couldn't verify", refusal)
        revived, sent = [], []
        self.patch("_revive_session_inner", lambda sid, client=None: revived.append(sid))
        self.patch("_name_of", lambda sid: "dead")
        self.patch("_send_to_view", lambda app, msg, wid: sent.append(msg))
        km._revive_session(SID3)
        self.assertIn("couldn't verify", sent[0]["text"])
        self.assertEqual((forks, promotes, renames, revived), ([], [], [], []),
                         "no door acted on an unverifiable name")
        self.assertEqual(self.claims(), {}, "every refusal released its claim")

    def test_the_thread_door_refuses_an_explicit_and_a_default_name(self):
        for name in ("side", ""):
            err, tid = km._comment_create(PARENT, "", "a passage", "a note", name=name)
            self.assertIsNone(tid, name)
            self.assertIn("couldn't verify", err or "", name)
        self.assertEqual(self.tbe.calls, [], "nothing forked")
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [], "no row written")
        self.assertEqual(self.claims(), {})


if __name__ == "__main__":
    unittest.main()
