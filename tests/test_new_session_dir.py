#!/usr/bin/env python3
"""The new-session directory: completion, status, and the create-it-or-edit-it fork (the user 2026-07-28).

A session's cwd is fixed at creation, so a wrong path can't be fixed later — it was already rejected up
front. What was missing is everything around that rejection: the picker typed paths blind (a datalist of
past dirs and a native dialog that only ever showed the LOCAL machine), and a missing directory came back
as a toast the "Opening…" cue was covering, so the create looked like it silently did nothing.

Now the kernel that will OWN the session answers three questions over the wire — what does this path
complete to, what IS it, and shall I make it — which is also what makes the field work for a session on a
remote host: federation routes the same ops to that host's kernel, and it reads its own disk.

Synthetic paths in a temp dir only.
"""
import inspect
import json
import os
import tempfile
import threading
import types
import unittest
from unittest import mock
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_newdir", os.path.join(BIN, "romp-kernel"))


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: None)   # the temp tree is the OS's to reap; nothing here is precious


class ResolveCreateDir(_Dirs):
    def test_an_existing_directory_resolves(self):
        p, err = km._resolve_create_dir(self.tmp)
        self.assertIsNone(err)
        self.assertEqual(os.path.realpath(p), os.path.realpath(self.tmp))

    def test_blank_is_the_kernel_default(self):
        self.assertEqual(km._resolve_create_dir("")[0], km._default_create_dir())
        self.assertEqual(km._resolve_create_dir(None)[1], None)

    def test_a_missing_directory_is_refused_without_create(self):
        p, err = km._resolve_create_dir(os.path.join(self.tmp, "nope"))
        self.assertIsNone(p)
        self.assertIn("directory not found", err)

    def test_create_makes_the_whole_missing_chain(self):
        target = os.path.join(self.tmp, "a", "b", "c")
        p, err = km._resolve_create_dir(target, create=True)
        self.assertIsNone(err)
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(os.path.realpath(p), os.path.realpath(target))

    def test_a_file_in_the_way_is_never_created_over(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        for create in (False, True):
            p, err = km._resolve_create_dir(f, create=create)
            self.assertIsNone(p)
            self.assertIn("not a directory", err)
        self.assertTrue(os.path.isfile(f), "the file is untouched")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the mode bits")
    def test_a_creation_that_fails_reports_instead_of_pretending(self):
        blocked = os.path.join(self.tmp, "ro")
        os.makedirs(blocked)
        os.chmod(blocked, 0o500)
        self.addCleanup(os.chmod, blocked, 0o700)
        p, err = km._resolve_create_dir(os.path.join(blocked, "child"), create=True)
        self.assertIsNone(p)
        self.assertIn("could not create", err)


class DirStatus(_Dirs):
    def test_blank_reports_the_default_and_offers_nothing_to_create(self):
        st = km._dir_status("")
        self.assertTrue(st["isDefault"])
        self.assertTrue(st["isDir"])
        self.assertFalse(st["canCreate"])

    def test_an_existing_directory(self):
        st = km._dir_status(self.tmp)
        self.assertTrue(st["exists"])
        self.assertTrue(st["isDir"])
        self.assertFalse(st["isFile"])
        self.assertFalse(st["canCreate"])
        self.assertEqual(st["missing"], 0)

    def test_a_missing_path_names_the_deepest_ancestor_and_how_much_is_missing(self):
        st = km._dir_status(os.path.join(self.tmp, "x", "y", "z"))
        self.assertFalse(st["exists"])
        self.assertTrue(st["canCreate"])
        self.assertEqual(st["missing"], 3)
        self.assertEqual(os.path.realpath(os.path.expanduser(st["nearest"])), os.path.realpath(self.tmp))

    def test_a_file_can_never_be_created_into_a_directory(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        st = km._dir_status(f)
        self.assertTrue(st["isFile"])
        self.assertFalse(st["canCreate"], "no create offer for a path that is already something else")

    def test_tilde_and_vars_expand(self):
        st = km._dir_status("~")
        self.assertTrue(st["isDir"])
        os.environ["ROMP_TEST_DIR"] = self.tmp
        self.addCleanup(os.environ.pop, "ROMP_TEST_DIR", None)
        self.assertTrue(km._dir_status("$ROMP_TEST_DIR")["isDir"])


class DirCompletions(_Dirs):
    def setUp(self):
        super().setUp()
        for d in ("alpha", "album", "beta", ".hidden"):
            os.makedirs(os.path.join(self.tmp, d))
        open(os.path.join(self.tmp, "alfile"), "w").close()

    def names(self, raw, **kw):
        return [i["name"] for i in km._dir_completions(raw, **kw)["items"]]

    def test_a_trailing_slash_lists_the_children(self):
        self.assertEqual(self.names(self.tmp + "/"), ["album", "alpha", "beta"])

    def test_a_fragment_narrows_by_prefix(self):
        self.assertEqual(self.names(os.path.join(self.tmp, "al")), ["album", "alpha"])

    def test_files_are_never_offered(self):
        # a session cwd is a directory; "alfile" matches the prefix and must still not appear
        self.assertNotIn("alfile", self.names(os.path.join(self.tmp, "al")))

    def test_hidden_directories_appear_only_once_the_dot_is_typed(self):
        self.assertNotIn(".hidden", self.names(self.tmp + "/"))
        self.assertEqual(self.names(os.path.join(self.tmp, ".")), [".hidden"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self.names(os.path.join(self.tmp, "AL")), ["album", "alpha"])

    def test_the_reply_is_capped_and_says_so(self):
        big = os.path.join(self.tmp, "many")
        for i in range(8):
            os.makedirs(os.path.join(big, "d%d" % i))
        out = km._dir_completions(big + "/", limit=3)
        self.assertEqual(len(out["items"]), 3)
        self.assertTrue(out["truncated"])
        self.assertFalse(km._dir_completions(big + "/", limit=50)["truncated"])

    def test_an_unreadable_or_absent_base_answers_empty_rather_than_raising(self):
        out = km._dir_completions(os.path.join(self.tmp, "nothing-here", "x"))
        self.assertEqual(out["items"], [])
        self.assertFalse(out["truncated"])

    def test_paths_come_back_home_collapsed(self):
        out = km._dir_completions("~/")
        self.assertTrue(out["base"].startswith("~"), out["base"])
        for it in out["items"]:
            self.assertTrue(it["path"].startswith("~/"), it["path"])


class _Wire(_Dirs):
    """The two WS ops, driven through the real dispatcher with a fake client."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"app": "chat", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send(self, msg):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        return self.sent[-1] if self.sent else None


class DirCompleteOp(_Wire):
    def test_the_reply_carries_the_completions_the_status_and_the_request_id(self):
        r = self.send({"type": "dirComplete", "value": self.tmp + "/", "reqId": 12})
        self.assertEqual(r["type"], "dirCompletions")
        self.assertEqual(r["reqId"], 12, "echoed so a late reply for an older keystroke can be dropped")
        self.assertEqual(r["value"], self.tmp + "/")
        self.assertIn("items", r)
        self.assertTrue(r["status"]["isDir"])


class CreateSessionDirFork(_Wire):
    def setUp(self):
        super().setUp()
        self.spawned = []
        self._real_sdk = km._create_sdk_session
        self._real_ready = km._sdk_ready
        km._create_sdk_session = lambda nm, cwd, auth="", client=None, **kw: self.spawned.append((nm, cwd)) or ("TESTSID", {})
        km._sdk_ready = lambda: True
        self.addCleanup(setattr, km, "_create_sdk_session", self._real_sdk)
        self.addCleanup(setattr, km, "_sdk_ready", self._real_ready)

    def test_a_missing_directory_asks_instead_of_warning_into_the_void(self):
        target = os.path.join(self.tmp, "not", "yet")
        r = self.send({"type": "createSession", "name": "web", "dir": target, "backend": "sdk"})
        self.assertEqual(r["type"], "createDirMissing")
        self.assertEqual(r["name"], "web")
        self.assertTrue(r["status"]["canCreate"])
        self.assertEqual(r["status"]["missing"], 2)
        self.assertEqual(self.spawned, [], "nothing is created until the user answers")
        self.assertFalse(os.path.exists(target))

    def test_answering_create_makes_the_directory_and_starts_there(self):
        target = os.path.join(self.tmp, "not", "yet")
        self.send({"type": "createSession", "name": "web", "dir": target, "backend": "sdk", "mkdir": True})
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(os.path.realpath(self.spawned[0][1]), os.path.realpath(target))

    def test_a_file_in_the_way_stays_a_plain_warning_there_is_nothing_to_offer(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        r = self.send({"type": "createSession", "name": "web", "dir": f, "backend": "sdk"})
        self.assertEqual(r["type"], "warn")
        self.assertIn("not a directory", r["text"])

    def test_a_good_directory_still_just_starts(self):
        self.send({"type": "createSession", "name": "web", "dir": self.tmp, "backend": "sdk"})
        self.assertEqual(len(self.spawned), 1)
        self.assertNotIn("createDirMissing", [m.get("type") for m in self.sent])


class CreateSessionTags(_Wire):
    """createSession's `tags` + `parent` (tab groups on tags, the user 2026-09-04): the picker posts the
    Tags row it prefilled from the active tab; the kernel hands both to _create_sdk_session so they
    land BEFORE the first push. The same loud contract as POST /new: an unknown parent or a malformed
    tags list refuses the create with a warn frame and nothing is spawned; a refused tag edit after a
    successful spawn is warned, never swallowed. A Codex create takes both through the same seam
    (_create_codex_session): the tag store keys on the registry sid, not the backend, so the op
    used to refuse a Codex create asked into a group for no reason of its own. Synthetic sids only."""

    PARENT = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        super().setUp()
        self.spawned = []
        self.extra = {}
        saved = (km._create_sdk_session, km._sdk_ready,
                 km._create_codex_session, km._codex_ready)
        km._create_sdk_session = lambda nm, cwd, auth="", client=None, **kw: (
            self.spawned.append((nm, dict(kw))), ("TESTSID", dict(self.extra)))[1]
        km._sdk_ready = lambda: True
        km._create_codex_session = lambda nm, cwd, client=None, **kw: (
            self.spawned.append((nm, "codex", dict(kw))), ("TESTCODEX", dict(self.extra)))[1]
        km._codex_ready = lambda: True
        self.addCleanup(lambda: (setattr(km, "_create_sdk_session", saved[0]),
                                 setattr(km, "_sdk_ready", saved[1]),
                                 setattr(km, "_create_codex_session", saved[2]),
                                 setattr(km, "_codex_ready", saved[3])))
        # A private state root for the duration: the kernel's judge is one module object shared by every test
        # module in the process, and the names entry, goal store and cleared rows the create flow writes at its
        # import-bound root outlived this module and reached every later module's feed (T281).
        self._saved_state = km.jd.STATE
        km.jd._rebind_state(Path(tempfile.mkdtemp()))
        self.addCleanup(lambda: km.jd._rebind_state(self._saved_state))
        (km.jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "names" / self.PARENT).write_text("web\t/tmp\t#123456\twhite\n")

    def test_tags_and_a_known_parent_reach_the_create_as_keyword_args(self):
        self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk",
                   "tags": ["pool", " infra "], "parent": self.PARENT})
        self.assertEqual(len(self.spawned), 1)
        nm, kw = self.spawned[0]
        self.assertEqual(nm, "api")
        self.assertEqual(kw.get("tags"), ["pool", "infra"], "names trimmed; applied inside the create, before its push")
        self.assertEqual(kw.get("parent"), self.PARENT)

    def test_no_tags_asked_none_threaded(self):
        self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk"})
        nm, kw = self.spawned[0]
        self.assertEqual(kw.get("tags"), [])
        self.assertEqual(kw.get("parent"), "")

    def test_an_unknown_parent_refuses_loudly_and_spawns_nothing(self):
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk",
                       "parent": "99999999-0000-0000-0000-000000000000"})
        self.assertEqual(r["type"], "warn")
        self.assertIn("not a session this kernel knows", r["text"])
        self.assertEqual(self.spawned, [], "a child must never spawn outside the group it was asked into")

    def test_a_malformed_tags_list_refuses_loudly(self):
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk", "tags": ["", "x"]})
        self.assertEqual(r["type"], "warn")
        self.assertIn("tags must be", r["text"])
        self.assertEqual(self.spawned, [])

    def test_a_refused_tag_edit_after_the_spawn_is_warned_never_swallowed(self):
        self.extra = {"tags": [], "tagError": 'two tags are named "pool"'}
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk", "tags": ["pool"]})
        self.assertEqual(len(self.spawned), 1, "the session exists — the refusal rides beside it")
        self.assertEqual(r["type"], "warn")
        self.assertIn('two tags are named "pool"', r["text"])

    def test_an_unknown_backend_with_tags_refuses_instead_of_dropping_them(self):
        # two backends since the tmux backend's removal (2026-09-11): any other word is a warn naming both,
        # and neither arm spawns — the tags are not silently gone
        r = self.send({"type": "createSession", "name": "term1", "dir": self.tmp, "backend": "shell", "tags": ["pool"]})
        self.assertEqual(r["type"], "warn")
        self.assertIn("unknown backend", r["text"])
        self.assertIn("Claude Code", r["text"])
        self.assertIn("Codex", r["text"])
        self.assertEqual(self.spawned, [], "no spawn with the tags silently gone")

    def test_a_codex_create_takes_tags_and_a_parent_like_an_sdk_one(self):
        self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "codex",
                   "tags": ["pool", " infra "], "parent": self.PARENT})
        self.assertEqual(len(self.spawned), 1, "a Codex create asked into a group is spawned, not refused")
        nm, be, kw = self.spawned[0]
        self.assertEqual((nm, be), ("api", "codex"))
        self.assertEqual(kw.get("tags"), ["pool", "infra"], "names trimmed; applied inside the create, before its push")
        self.assertEqual(kw.get("parent"), self.PARENT)
        self.assertEqual(self.sent, [], "nothing to warn about")

    def test_a_codex_create_warns_a_refused_tag_edit_like_the_sdk_arm(self):
        self.extra = {"tags": [], "tagError": 'two tags are named "pool"'}
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "codex", "tags": ["pool"]})
        self.assertEqual(len(self.spawned), 1, "the session exists — the refusal rides beside it")
        self.assertEqual(r["type"], "warn")
        self.assertIn('two tags are named "pool"', r["text"])

    def test_a_codex_create_still_validates_parent_and_tags_up_front(self):
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "codex",
                       "parent": "99999999-0000-0000-0000-000000000000"})
        self.assertEqual(r["type"], "warn")
        self.assertIn("not a session this kernel knows", r["text"])
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "codex", "tags": ["", "x"]})
        self.assertIn("tags must be", r["text"])
        self.assertEqual(self.spawned, [], "a child must never spawn outside the group it was asked into")

    LIVE = "22222222-3333-4444-5555-666666666666"

    def _live_api(self):
        # a running session named "api", a private views store, and the focus reveal captured
        saved = (km._live_names, km._reveal_chat_for, km._mark_views_dirty, km.jd.STATE)
        focused = []
        km._live_names = lambda *_: {"api": self.LIVE}
        km._reveal_chat_for = lambda client, m: focused.append(m)
        km._mark_views_dirty = lambda: None
        km.jd._rebind_state(Path(tempfile.mkdtemp()))   # every derived dir moves with STATE (T281), not STATE alone
        km._flags_cache.clear()
        def restore():
            km._live_names, km._reveal_chat_for, km._mark_views_dirty = saved[:3]
            km.jd._rebind_state(saved[3])
            km._flags_cache.clear()
        self.addCleanup(restore)
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [self.PARENT]}]})
        return focused

    def test_a_live_name_with_tags_is_focused_and_warned_never_tagged_and_never_silently_dropped(self):
        # the picker's Tags row is a PREFILL from the active tab, not an ask: applying it to a running
        # session would move that session into the active tab's group unasked, and dropping it
        # silently breaks the fail-loudly rule. So: focus as ever, then one loud warn naming the path.
        focused = self._live_api()
        self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk", "tags": ["pool", "infra"]})
        self.assertEqual(focused, [{"type": "focus", "id": self.LIVE}], "the running session is focused first")
        self.assertEqual([m["type"] for m in self.sent], ["warn"])
        self.assertIn('"api" is already running; its tags were not changed', self.sent[0]["text"])
        self.assertIn("Tags menu", self.sent[0]["text"])
        self.assertEqual(self.spawned, [])
        tags = km._timeline_views()["tags"]
        self.assertEqual([t["name"] for t in tags], ["pool"], "infra was never minted")
        self.assertNotIn(self.LIVE, [m["sid"] for m in tags[0]["members"]], "the running session did not move")
        # no tags → the idempotent open stays silent, as before
        self.sent.clear()
        self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk"})
        self.assertEqual(self.sent, [])
        self.assertEqual(len(focused), 2)

    def test_parent_and_tags_are_validated_before_the_live_name_check(self):
        focused = self._live_api()
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk", "tags": ["", 3]})
        self.assertEqual(r["type"], "warn")
        self.assertIn("tags must be", r["text"])
        r = self.send({"type": "createSession", "name": "api", "dir": self.tmp, "backend": "sdk",
                       "parent": "99999999-0000-0000-0000-000000000000"})
        self.assertIn("not a session this kernel knows", r["text"])
        self.assertEqual(focused, [], "a malformed request is refused whether or not the name runs — nothing focused")
        self.assertEqual(self.spawned, [])

    def test_the_op_and_the_docs_say_how_the_live_name_case_differs_from_post_new(self):
        # the two doors disagree on a running name ON PURPOSE — /new's `tags` is always an explicit
        # --in (re-asserted like model/effort); the picker's is a prefill (warned, not applied). The
        # op's comment and the docs both state the difference, not a match.
        src = inspect.getsource(km.Handler._dispatch_ws)
        op = src[src.index('msg.get("type") == "createSession"'):src.index('msg.get("type") == "cancelCreate"')]
        live_arm = op[op.index("elif nm in live:"):op.index("elif _thread_name_refusal(nm, _thread_names())")]
        self.assertIn("its tags were not changed", live_arm)
        self.assertIn("existing:true arm differs on purpose", live_arm)
        self.assertNotIn("the same contract as", op)
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        side = " ".join(open(os.path.join(root, "docs", "read-side.md")).read().split())
        self.assertIn("Opening a running session inherits nothing: `/new` re-asserts an explicit `--in` on it, "
                      "while the picker's createSession op warns and leaves the running session's tags alone", side)
        guide = open(os.path.join(root, "docs", "guide.md")).read()
        self.assertIn("from\nthe picker, a name that already runs is focused and the Tags row is not applied", guide)


class NativeDialogAvailability(unittest.TestCase):
    """Browse… (and 📎) reach a dialog on the KERNEL's machine, and that machine may have no way to show
    one. It was osascript-only, so on Linux the click hit a missing binary, the OSError was swallowed, no
    reply came back, and the button sat there looking alive. Ask the desktop what it has; when the answer
    is nothing, say so instead of returning silence."""

    def cmd(self, kind="folder", platform="linux", which=(), env=None):
        have = set(which)
        with mock.patch.object(km.sys, "platform", platform), \
             mock.patch.object(km.shutil, "which", lambda e: ("/usr/bin/" + e) if e in have else None), \
             mock.patch.dict(os.environ, env or {}, clear=False):
            for k in ("DISPLAY", "WAYLAND_DISPLAY"):
                if not (env or {}).get(k):
                    os.environ.pop(k, None)
            return km._dialog_cmd(kind)

    def test_a_headless_machine_has_no_dialog_at_all(self):
        self.assertIsNone(self.cmd(which=("zenity",)),
                          "zenity with no screen to draw on is the same silent nothing in a new hat")
        self.assertIsNone(self.cmd(which=()))

    def test_a_linux_desktop_uses_whichever_picker_it_has(self):
        z = self.cmd(which=("zenity",), env={"DISPLAY": ":0"})
        self.assertEqual(z[0], "zenity")
        self.assertIn("--directory", z)                       # a session cwd is a folder, never a file
        self.assertNotIn("--directory", self.cmd("file", which=("zenity",), env={"DISPLAY": ":0"}))
        k = self.cmd(which=("kdialog",), env={"WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual(k[0], "kdialog")                     # KDE, and Wayland counts as a desktop
        self.assertIn("--getexistingdirectory", k)
        self.assertIn("--getopenfilename", self.cmd("file", which=("kdialog",), env={"DISPLAY": ":0"}))

    def test_macos_still_uses_osascript_and_needs_no_display_var(self):
        c = self.cmd(platform="darwin", which=())
        self.assertEqual(c[0], "osascript")
        self.assertIn("choose folder", c[-1])
        self.assertIn("choose file", self.cmd("file", platform="darwin", which=())[-1])

    def test_the_capability_is_what_the_ui_is_told(self):
        with mock.patch.object(km, "_dialog_cmd", lambda kind: None):
            self.assertFalse(km._native_dialogs())
        with mock.patch.object(km, "_dialog_cmd", lambda kind: ["zenity"]):
            self.assertTrue(km._native_dialogs())

    def test_a_cancelled_or_failed_dialog_is_None_not_a_crash(self):
        with mock.patch.object(km, "_dialog_cmd", lambda kind: ["picker"]):
            with mock.patch.object(km.subprocess, "run",
                                   lambda *a, **k: types.SimpleNamespace(stdout="  /tmp/chosen \n")):
                self.assertEqual(km._run_dialog("folder"), "/tmp/chosen")
            with mock.patch.object(km.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="\n")):
                self.assertIsNone(km._run_dialog("folder"), "cancelled → empty stdout")
            with mock.patch.object(km.subprocess, "run", mock.Mock(side_effect=OSError("gone"))):
                self.assertIsNone(km._run_dialog("folder"))

    def test_the_reason_names_the_actual_cause_and_what_to_do_instead(self):
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None); os.environ.pop("WAYLAND_DISPLAY", None)
            why = km._no_dialog_why("folder")
            self.assertIn("No folder dialog", why)
            self.assertIn("no desktop session", why)
            self.assertNotIn("zenity", why, "no point telling a headless box to install a picker")
            self.assertIn("Type the folder path", why)
            f = km._no_dialog_why("file")
            self.assertIn("No file dialog", f, "the 📎 refusal is about a FILE, not a folder")
            self.assertIn("paste its path", f)
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
            self.assertIn("zenity", km._no_dialog_why("folder"), "a desktop with no picker CAN install one")


class NativeDialogWire(_Wire):
    """What the client sees: the capability up front, and a spoken refusal if a click lands anyway."""

    def test_the_session_list_advertises_whether_browse_can_work(self):
        for cap in (True, False):
            with mock.patch.object(km, "_native_dialogs", lambda c=cap: c):
                self.sent.clear()
                r = self.send({"type": "requestSessions"})
                self.assertEqual(r["type"], "sessionList")
                self.assertIs(r["nativeDialogs"], cap)

    def test_a_click_a_headless_kernel_cannot_serve_is_answered_not_swallowed(self):
        with mock.patch.object(km, "_native_dialogs", lambda: False):
            for msg, said in (({"type": "browseDir"}, "No folder dialog"),
                              ({"type": "browseDir", "target": "gear"}, "No folder dialog"),
                              ({"type": "pickFile"}, "No file dialog")):
                self.sent.clear()
                r = self.send(msg)
                self.assertIsNotNone(r, "silence is the bug: %r returned nothing" % (msg,))
                self.assertEqual(r["type"], "warn")
                self.assertIn(said, r["text"])

    def test_a_kernel_that_can_show_one_says_nothing_and_opens_it(self):
        opened = []
        before = set(threading.enumerate())            # join only the threads THIS test starts (T230b):
        #                                                joining every daemon in the process made this
        #                                                test's runtime scale with the suite's leaked
        #                                                threads (~122 s measured, 2 s per leak)
        with mock.patch.object(km, "_native_dialogs", lambda: True), \
             mock.patch.object(km, "_pick_folder", lambda: opened.append("folder") or ""), \
             mock.patch.object(km, "_pick_file", lambda: opened.append("file") or ""):
            for msg in ({"type": "browseDir"}, {"type": "pickFile"}):
                self.sent.clear()
                self.send(msg)
                self.assertEqual([m for m in self.sent if m.get("type") == "warn"], [],
                                 "an available dialog must not be talked about, only shown")
        for t in threading.enumerate():                      # the dialog runs off the message loop
            if t not in before and t is not threading.current_thread() and t.daemon:
                t.join(timeout=2)
        self.assertEqual(sorted(opened), ["file", "folder"])

    def test_the_gear_learns_the_same_thing_from_its_own_route(self):
        # The gear has no socket — it fetches /version — so the capability rides there too, beside the
        # default directory it already reads.
        src = inspect.getsource(km._version_info)
        self.assertIn('"nativeDialogs": _native_dialogs()', src)
        self.assertIn("v.nativeDialogs", _gear())
        self.assertIn("ddb.style.display", _gear(), "no dialog on this machine → no Browse button")


def _gear():
    return Path(os.path.dirname(HERE), "ui", "webview", "gear.js").read_text()


class HeadlessParity(unittest.TestCase):
    def test_the_new_route_can_create_the_directory_too(self):
        import inspect
        src = inspect.getsource(km.Handler)
        self.assertIn('mk, ferr = _as_bool(b.get("mkdir"), "mkdir")', src,
                      "mkdir is a checked boolean: a string \"false\" used to create the directory")
        self.assertIn('_resolve_create_dir(b.get("dir"), create=mk)', src,
                      "`romp new` gets the same create-it answer the dashboard offers")
        self.assertIn('"dirStatus": _dir_status(b.get("dir"))', src,
                      "a headless caller is told WHY, in the same shape the picker reads")


if __name__ == "__main__":
    unittest.main()
