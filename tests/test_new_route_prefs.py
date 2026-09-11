#!/usr/bin/env python3
"""POST /new's per-spawn model/effort (the user 2026-08-14): applied via the park-aware setters on
CREATE and on the idempotent existing:true open (a nightly re-brief re-asserts them), echoed in the
response so a caller can be loud when ignored; absent keys touch nothing.

Drives the REAL Handler over HTTP (the test_kernel_ws_auth.py pattern). Synthetic only — placeholder
UUIDs, temp dirs, no session state touched (the setters are recorded, never executed).
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source

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

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"
SID3 = "33333333-4444-5555-6666-777777777777"   # the Codex arm's synthetic child


class NewRoutePrefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.calls = []
        self._saved = (km._live_names, km._live_map, km._set_model_or_park,
                       km._set_effort_or_park, km.Sessions.backend_for,
                       km._sdk_ready, km._create_sdk_session, km._push_soon)
        km._live_map = lambda: []
        km._set_model_or_park = lambda be, sid, v: self.calls.append(("model", sid, v))
        km._set_effort_or_park = lambda be, sid, v: self.calls.append(("effort", sid, v))
        km.Sessions.backend_for = staticmethod(lambda sid: object())
        km._push_soon = lambda: None

    def tearDown(self):
        (km._live_names, km._live_map, km._set_model_or_park,
         km._set_effort_or_park, km.Sessions.backend_for,
         km._sdk_ready, km._create_sdk_session, km._push_soon) = self._saved

    def _post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/new" % self.port,
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_existing_open_reasserts_model_and_effort_and_echoes_them(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir,
                        "model": "claude-fable-5", "effort": "ultracode"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["existing"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertEqual(r.get("effort"), "ultracode")
        self.assertIn(("model", SID, "claude-fable-5"), self.calls)
        self.assertIn(("effort", SID, "ultracode"), self.calls)

    def test_existing_open_without_prefs_touches_nothing(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir})
        self.assertTrue(r["ok"])
        self.assertNotIn("model", r)
        self.assertNotIn("effort", r)
        self.assertEqual(self.calls, [])

    def test_fresh_sdk_create_applies_both_and_echoes_them(self):
        km._live_names = lambda *_: {}
        km._sdk_ready = lambda: True
        # the create path owns the prefs now — applied between spawn and connect, so the FIRST
        # connect carries them (the 2026-08-16 -m drop); the stub mirrors that seam
        km._create_sdk_session = (lambda nm, cwd, auth="", prefs=None, client=None, env=None, **kw:
                                  (SID2, km._apply_new_session_prefs(SID2, prefs or {})))
        r = self._post({"name": "opt", "dir": self.dir,
                        "model": "claude-fable-5", "effort": "ultracode"})
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertEqual(r.get("effort"), "ultracode")
        self.assertIn(("model", SID2, "claude-fable-5"), self.calls)
        self.assertIn(("effort", SID2, "ultracode"), self.calls)

    def test_model_alone_applies_and_echoes_only_model(self):
        km._live_names = lambda *_: {"opt": SID}
        r = self._post({"name": "opt", "dir": self.dir, "model": "claude-fable-5"})
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("model"), "claude-fable-5")
        self.assertNotIn("effort", r)
        self.assertEqual(self.calls, [("model", SID, "claude-fable-5")])


class NewRouteEnv(unittest.TestCase):
    """POST /new's per-spawn "env" (the user 2026-08-17), the spawn-time slice: validated server-side
    (a bad name refuses the WHOLE request with a 400 — fail-loudly, never a silent skip), born into
    the SDK spawn so the eager connect already carries it, re-asserted through the park-aware
    set_env on the idempotent existing:true open, and echoed back like model/effort. SDK-only: the
    payload rides the per-sid flag-settings file, which the Codex backend never reads — asked of a
    Codex session or the Codex arm, /new says so instead of pretending. Synthetic values only
    (FEATURE_FLAG=1 shapes, never anything credential-shaped — gitleaks reads this repo too)."""

    class _SdkBe:
        """A backend double WITH the set_env capability (the SDK shape)."""

        def set_env(self, sid, env):
            return True

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.calls = []
        self.created = []
        self._saved = (km._live_names, km._live_map, km._set_env_or_park,
                       km.Sessions.backend_for, km._sdk_ready, km._create_sdk_session,
                       km._push_soon)
        km._live_map = lambda: []
        km._live_names = lambda *_: {}
        km._set_env_or_park = lambda be, sid, v: self.calls.append(("env", sid, v))
        km.Sessions.backend_for = staticmethod(lambda sid: self._SdkBe())
        km._sdk_ready = lambda: True
        km._create_sdk_session = (lambda nm, cwd, auth="", prefs=None, client=None, env=None, **kw:
                                  (self.created.append((nm, auth, env)),
                                   (SID2, km._apply_new_session_prefs(SID2, prefs or {})))[1])
        km._push_soon = lambda: None

    def tearDown(self):
        (km._live_names, km._live_map, km._set_env_or_park,
         km.Sessions.backend_for, km._sdk_ready, km._create_sdk_session,
         km._push_soon) = self._saved

    def _post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/new" % self.port,
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_a_bad_name_refuses_the_whole_request(self):
        for env in ({"9BAD": "1"}, {"BAD-NAME": "1"}, {"": "1"}):
            code, body = self._post({"name": "opt", "dir": self.dir, "env": env})
            self.assertEqual(code, 400, "bad env name %r must 400, not spawn" % env)
            self.assertFalse(body["ok"])
            self.assertIn("[A-Za-z_][A-Za-z0-9_]*", body["error"],
                          "the error teaches the alphabet, not just refuses")
        self.assertEqual(self.created, [], "nothing may be created on a refused request")
        self.assertEqual(self.calls, [])

    def test_a_non_object_or_non_string_value_refuses(self):
        code, body = self._post({"name": "opt", "dir": self.dir, "env": "FEATURE_FLAG=1"})
        self.assertEqual(code, 400)
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {"FEATURE_FLAG": 1}})
        self.assertEqual(code, 400)
        self.assertIn("FEATURE_FLAG", body["error"], "the offender is named — fail loudly")

    def test_falsy_junk_env_refuses_not_swallowed(self):
        # `or None` used to collapse ALL falsy values to "not asked": a script serializing env as
        # false/0/"" got an acked spawn silently missing its env — 0 skipped while 1 400'd
        for junk in (False, 0, "", []):
            code, body = self._post({"name": "opt", "dir": self.dir, "env": junk})
            self.assertEqual(code, 400, "falsy junk %r must 400, not spawn env-less" % (junk,))
            self.assertFalse(body["ok"])
            self.assertIn("env", body["error"], "the refusal names the offending key")
        self.assertEqual(self.created, [], "nothing may be created on a refused request")
        self.assertEqual(self.calls, [])

    def test_null_env_still_means_not_asked(self):
        code, body = self._post({"name": "opt", "dir": self.dir, "env": None})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.created, [("opt", "", None)], "null = absent, the don't-touch contract")
        self.assertNotIn("env", body)
        self.assertEqual(self.calls, [])

    def test_a_nul_byte_in_a_value_refuses(self):
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {"X": "a\x00b"}})
        self.assertEqual(code, 400, "a NUL value is unfulfillable by any process environment")
        self.assertFalse(body["ok"])
        self.assertIn("X", body["error"], "the offender is named — fail loudly")
        self.assertIn("NUL", body["error"])
        self.assertEqual(self.created, [])
        self.assertEqual(self.calls, [])

    def test_env_is_born_into_the_spawn_and_echoed(self):
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {"FEATURE_FLAG": "1"}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.created, [("opt", "", {"FEATURE_FLAG": "1"})],
                         "the reg must be born with the env — BEFORE the eager connect, "
                         "not patched in behind it")
        self.assertEqual(body.get("env"), {"FEATURE_FLAG": "1"},
                         "the echo is the ack the CLI is loud about missing")
        self.assertIn(("env", SID2, {"FEATURE_FLAG": "1"}), self.calls,
                      "the prefs pass re-asserts (set_env's unchanged-skip makes it free)")

    def test_no_env_asked_none_threaded(self):
        code, body = self._post({"name": "opt", "dir": self.dir})
        self.assertEqual(code, 200)
        self.assertEqual(self.created, [("opt", "", None)])
        self.assertNotIn("env", body)
        self.assertEqual(self.calls, [], "nothing asked, nothing re-asserted")

    def test_existing_session_reasserts_through_set_env(self):
        km._live_names = lambda *_: {"opt": SID}
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {"FEATURE_FLAG": "1"}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"] and body["existing"])
        self.assertEqual(self.calls, [("env", SID, {"FEATURE_FLAG": "1"})],
                         "re-asserted if <name> already runs — the model/effort contract")
        self.assertEqual(body.get("env"), {"FEATURE_FLAG": "1"})

    def test_an_explicit_empty_env_clears_a_running_session(self):
        # the clear-all door: {} is the replace-not-merge contract's limiting case (a re-run
        # declares the FULL env, and the empty declaration is "none") — before this, a spawn-time
        # debugging var could never be removed from a running session, only overwritten
        km._live_names = lambda *_: {"opt": SID}
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"] and body["existing"])
        self.assertEqual(self.calls, [("env", SID, {})],
                         "an explicit {} must reach set_env, which clears by replacing")
        self.assertEqual(body.get("env"), {}, "the clear ask is echoed like any other env ask")

    def test_an_explicit_empty_env_on_a_fresh_spawn_is_vacuous_but_echoed(self):
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.created, [("opt", "", {})],
                         "spawn's own `if env:` makes the empty declaration naturally vacuous")
        self.assertEqual(body.get("env"), {})

    def test_an_existing_non_sdk_session_refuses_loudly(self):
        km._live_names = lambda *_: {"opt": SID}
        km.Sessions.backend_for = staticmethod(lambda sid: object())   # no set_env — the Codex shape
        code, body = self._post({"name": "opt", "dir": self.dir, "env": {"FEATURE_FLAG": "1"}})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"], "a session that can't take the env must say so, not drop it")
        self.assertIn("needs a Claude Code session", body["error"])   # the backend's name since T288

    def test_an_unknown_backend_is_a_400_naming_both_backends(self):
        # two backends since the tmux backend's removal (2026-09-11): any other value is refused up front,
        # by name, and nothing is created — the env is not silently dropped on a spawn that never happens
        code, body = self._post({"name": "term1", "dir": self.dir,
                                 "backend": "shell", "env": {"FEATURE_FLAG": "1"}})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"], "an unknown backend is a refusal, not a default")
        self.assertIn("sdk", body["error"])
        self.assertIn("codex", body["error"])
        self.assertEqual(self.created, [], "the refusal comes BEFORE any spawn")


class NewRouteTags(unittest.TestCase):
    """POST /new's `parent` + `tags` (tab groups on tags, the user 2026-09-04). `romp new` run inside a
    session sends its own ROMP_SID as `parent`, so the child inherits the parent's tag memberships;
    `--in <tag>` rides as `tags`. Both land inside _create_sdk_session BEFORE its direct push (the
    stub mirrors that seam through the real _tag_new_session). Validated up front like env — an
    unknown parent or a malformed list is a 400 and nothing is created. The idempotent existing:true
    open never INHERITS (no creation event) but re-asserts an explicit --in like model/effort/env; a
    thread's name tags nothing and says so; an unknown backend is a 400. The `tags` echo is the child's
    names after everything, so the CLI is loud when a kernel drops the ask. A Codex spawn takes the
    same parent/tags through the same seam (the tag store keys on the registry sid, not the
    backend): before that, the Codex arm applied nothing and echoed nothing, so a plain `romp new`
    from inside a session on a Codex-default box landed outside its parent's group while the CLI
    blamed an older kernel. Synthetic sids only."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.created = []
        self.created_codex = []
        self._saved = (km._live_names, km._live_map, km.Sessions.backend_for, km._sdk_ready,
                       km._create_sdk_session, km._push_soon, km._mark_views_dirty,
                       km.jd.STATE, km._codex_ready, km._create_codex_session, km._default_backend)
        km.jd.STATE = __import__("pathlib").Path(self.td.name)
        km._flags_cache.clear()
        (km.jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "names" / SID).write_text("web\t/tmp\t#123456\twhite\n")
        km._live_map = lambda: []
        km._live_names = lambda *_: {}
        km.Sessions.backend_for = staticmethod(lambda sid: object())
        km._sdk_ready = lambda: True
        km._mark_views_dirty = lambda: None
        km._push_soon = lambda: None

        def create(nm, cwd, auth="", prefs=None, client=None, env=None, parent="", tags=()):
            # the real seam: tags land INSIDE the create, before its push — mirrored here
            self.created.append((nm, parent, list(tags)))
            extra = km._apply_new_session_prefs(SID2, prefs or {})
            if parent or tags:
                extra.update(km._tag_ack(SID2, parent, tags))
            return SID2, extra
        km._create_sdk_session = create

        km._codex_ready = lambda: True
        def create_codex(nm, cwd, client=None, parent="", tags=()):
            # the same seam as the SDK stub: tags land INSIDE the create, before its push
            self.created_codex.append((nm, parent, list(tags)))
            return SID3, (km._tag_ack(SID3, parent, tags) if parent or tags else {})
        km._create_codex_session = create_codex

    def tearDown(self):
        (km._live_names, km._live_map, km.Sessions.backend_for, km._sdk_ready,
         km._create_sdk_session, km._push_soon, km._mark_views_dirty,
         km.jd.STATE, km._codex_ready, km._create_codex_session, km._default_backend) = self._saved
        km._flags_cache.clear()
        self.td.cleanup()

    def _post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/new" % self.port,
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _members(self, name):
        t = next((t for t in km._timeline_views()["tags"] if t["name"] == name), None)
        return sorted(m["sid"] for m in (t or {"members": []})["members"])

    def test_a_fresh_spawn_inherits_its_parents_tags_and_echoes_them(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.created, [("api", SID, [])], "the parent reaches the create — inheritance rides it")
        self.assertEqual(body.get("tags"), ["pool"], "the echo the CLI checks")
        self.assertEqual(self._members("pool"), sorted([SID, SID2]), "the child joined; the parent kept it")

    def test_in_tags_join_named_tags_created_on_first_use_and_echo_with_the_inherited(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID, "tags": ["infra"]})
        self.assertEqual(code, 200)
        self.assertEqual(sorted(body.get("tags") or []), ["infra", "pool"])
        self.assertEqual(self._members("infra"), [SID2], "--in mints the tag like POST /tag would")

    def test_a_parent_with_no_tags_is_an_honest_empty_echo(self):
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("tags"), [], "asked → echoed, even when there was nothing to inherit")

    def test_no_parent_no_tags_no_echo(self):
        code, body = self._post({"name": "api", "dir": self.dir})
        self.assertEqual(code, 200)
        self.assertNotIn("tags", body, "not asked → not echoed (the model/effort/env contract)")
        self.assertEqual(self.created, [("api", "", [])])

    def test_a_live_parent_name_resolves_like_fork_does(self):
        km._live_names = lambda *_: {"web": SID}
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "parent": "web"})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("tags"), ["pool"])

    def test_an_unknown_parent_is_a_400_and_nothing_is_created(self):
        code, body = self._post({"name": "api", "dir": self.dir, "parent": "99999999-0000-0000-0000-000000000000"})
        self.assertEqual(code, 400, "an unknown parent refuses the WHOLE request — never a child spawned outside its group")
        self.assertFalse(body["ok"])
        self.assertIn("not a session this kernel knows", body["error"])
        self.assertEqual(self.created, [])

    def test_a_malformed_tags_list_is_a_400(self):
        for junk in ("pool", ["pool", ""], [3], {"a": 1}):
            code, body = self._post({"name": "api", "dir": self.dir, "tags": junk})
            self.assertEqual(code, 400, "malformed tags %r must 400, not spawn untagged" % (junk,))
            self.assertIn("tags must be", body["error"])
        self.assertEqual(self.created, [])

    def test_existing_open_never_inherits_but_reasserts_an_explicit_in(self):
        km._live_names = lambda *_: {"api": SID2}
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID})
        self.assertEqual(code, 200)
        self.assertTrue(body["existing"])
        self.assertEqual(self._members("pool"), [SID], "no creation event → no inheritance (the ruling)")
        self.assertEqual(body.get("tags"), [], "…and the echo tells the truth about the running session")
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID, "tags": ["infra"]})
        self.assertEqual(body.get("tags"), ["infra"], "an explicit --in re-asserts, like model/effort/env")
        self.assertEqual(self._members("infra"), [SID2])
        self.assertEqual(self._members("pool"), [SID], "still no inheritance")
        self.assertEqual(self.created, [], "nothing created on either open")

    def test_a_refused_tag_edit_rides_beside_the_ack(self):
        # twins written to the file: the write door refuses a second tag under a taken name, so a
        # store holding twins predates that kernel (or was hand-edited)
        km._atomic_write(km._views_path(), json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "twin", "members": []}, {"id": "g2", "name": "twin", "members": []}]}))
        km._flags_cache.clear()
        code, body = self._post({"name": "api", "dir": self.dir, "tags": ["twin"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], "the session exists — the refusal cannot undo it")
        self.assertEqual(body.get("tags"), [])
        self.assertIn("two tags are named", body.get("tagError") or "", "…so it is named, never swallowed")

    def test_an_unknown_backend_is_refused_before_anything_is_created(self):
        # two backends since the tmux backend's removal (2026-09-11): any other value is a 400 that names
        # both, and neither arm runs — the tags and the parent are not silently dropped
        code, body = self._post({"name": "term1", "dir": self.dir, "backend": "shell", "tags": ["pool"]})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"], "no spawn with the tags silently dropped")
        self.assertIn("sdk", body["error"])
        self.assertIn("codex", body["error"])
        code, body = self._post({"name": "term1", "dir": self.dir, "backend": "shell", "parent": SID})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])
        self.assertEqual((self.created, self.created_codex), ([], []), "the refusal comes BEFORE either arm")

    def test_a_normalized_in_name_echoes_positionally_as_applied_not_as_dropped(self):
        # the store trims and clamps names (_edit_tag, _VIEWS_MAX_NAME); the CLI compared its raw
        # --in against `tags` and warned "did not apply" for a tag that WAS applied. tagsRequested /
        # tagsApplied are positional, so it can say "applied as <name>" instead
        long = "a" * (km._VIEWS_MAX_NAME + 5)
        code, body = self._post({"name": "api", "dir": self.dir, "tags": [" pool ", long]})
        self.assertEqual(code, 200)
        self.assertEqual(body["tagsRequested"], [" pool ", long], "as sent")
        self.assertEqual(body["tagsApplied"], ["pool", "a" * km._VIEWS_MAX_NAME], "what each landed as")
        self.assertEqual(sorted(body["tags"]), sorted(["pool", "a" * km._VIEWS_MAX_NAME]))
        self.assertEqual(self._members("pool"), [SID2])

    def test_an_unknown_auto_parent_creates_the_session_untagged_and_says_so(self):
        # the CLI's default parent is its own ROMP_SID (parentAuto); against a kernel that never ran
        # the caller — a scratch kernel on another port — that must not be a 400 naming a sid the
        # user never typed. Untagged, with parentIgnored in the ack and the tags ask still answered.
        U = "99999999-0000-0000-0000-000000000000"
        code, body = self._post({"name": "api", "dir": self.dir, "parent": U, "parentAuto": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body.get("parentIgnored"), U)
        self.assertEqual(body.get("tags"), [], "the ask was answered: nothing inherited")
        self.assertEqual(self.created, [("api", "", [])], "created with no parent")
        # an explicit --in beside the ignored parent still lands, and its echo wins
        code, body = self._post({"name": "api", "dir": self.dir, "parent": U, "parentAuto": True, "tags": ["infra"]})
        self.assertEqual(body.get("tags"), ["infra"])
        self.assertEqual(body.get("parentIgnored"), U)
        # without the marker the same parent is the 400 the explicit contract promises
        code, body = self._post({"name": "api", "dir": self.dir, "parent": U})
        self.assertEqual(code, 400)

    def test_a_codex_spawn_inherits_and_joins_like_an_sdk_one(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "backend": "codex", "parent": SID, "tags": ["infra"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["id"], SID3)
        self.assertEqual(self.created_codex, [("api", SID, ["infra"])],
                         "parent and tags reach the Codex create — applied before its push, like the SDK arm")
        self.assertEqual(self.created, [], "no SDK session was minted in its place")
        self.assertEqual(sorted(body.get("tags") or []), ["infra", "pool"], "the echo the CLI checks")
        self.assertEqual(body.get("tagsRequested"), ["infra"])
        self.assertEqual(body.get("tagsApplied"), ["infra"])
        self.assertEqual(self._members("pool"), sorted([SID, SID3]), "the child joined; the parent kept it")
        self.assertEqual(self._members("infra"), [SID3], "--in mints the tag like POST /tag would")

    def test_a_plain_new_on_a_codex_default_box_inherits_its_parents_tags(self):
        # the reported case: no --codex, no --in — the CLI's auto parent alone, against a kernel
        # whose default backend is Codex. This used to land untagged with a misleading
        # "older kernel?" warning, because the echo was missing
        km._default_backend = lambda: "codex"
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [SID]}]})
        code, body = self._post({"name": "api", "dir": self.dir, "parent": SID, "parentAuto": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.created_codex, [("api", SID, [])])
        self.assertEqual(body.get("tags"), ["pool"], "the echo is present, so the CLI does not blame the kernel")
        self.assertNotIn("parentIgnored", body)
        self.assertEqual(self._members("pool"), sorted([SID, SID3]))

    def test_a_codex_spawn_with_an_ignored_auto_parent_says_so(self):
        U = "99999999-0000-0000-0000-000000000000"
        code, body = self._post({"name": "api", "dir": self.dir, "backend": "codex", "parent": U, "parentAuto": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body.get("parentIgnored"), U)
        self.assertEqual(body.get("tags"), [], "the ask was answered: nothing inherited")
        self.assertEqual(self.created_codex, [("api", "", [])], "created with no parent")

    def test_a_codex_spawn_asked_nothing_echoes_nothing(self):
        code, body = self._post({"name": "api", "dir": self.dir, "backend": "codex"})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        self.assertNotIn("tags", body, "not asked → not echoed (the model/effort/env contract)")
        self.assertEqual(self.created_codex, [("api", "", [])])

    def test_a_codex_spawn_refuses_env_loudly(self):
        # the Codex backend has no set_env: a Codex thread runs on the shared app-server. The arm
        # used to swallow the env silently; it refuses up front now
        code, body = self._post({"name": "api", "dir": self.dir, "backend": "codex", "env": {"X": "1"}})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"], "no Codex spawn with the env silently dropped")
        self.assertIn("needs a Claude Code session", body["error"])
        self.assertEqual(self.created_codex, [], "the refusal comes before the spawn")

    def test_a_threads_name_answers_the_tags_ask_with_nothing_applied_and_the_reason(self):
        TSID = "77777777-8888-9999-0000-111111111111"
        saved = km._thread_names
        km._thread_names = lambda: {"side": (TSID, SID)}
        try:
            code, body = self._post({"name": "side", "dir": self.dir, "tags": ["pool"], "parent": SID})
        finally:
            km._thread_names = saved
        self.assertEqual(code, 200)
        self.assertTrue(body.get("thread"))
        self.assertEqual(body.get("tags"), [])
        self.assertEqual(body.get("tagsApplied"), [None], "positional: the one --in did not land")
        self.assertIn("comment thread", body.get("tagError") or "", "…and the CLI's warning carries why")
        self.assertEqual(self.created, [])


if __name__ == "__main__":
    unittest.main()
