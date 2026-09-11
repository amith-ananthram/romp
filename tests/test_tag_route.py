#!/usr/bin/env python3
"""GET /views + POST /tag (the user 2026-08-23, manager/worker workflow): the worker roster IS a
session tag, so an agent reads the views blob over GET /views and keeps ONE tag current over
POST /tag. NOT the setTimelineViews WS op re-exposed — that op replaces the whole blob, and an
agent replaying a stale read would clobber the active view and every tag it never looked at;
/tag is a targeted merge on one NAMED tag (_edit_tag): live names resolve to sids, opaque
ids (dead sids, host-prefixed remote ids) pass through verbatim, unknown names refuse loudly.
Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import contextlib
import errno
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

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
km = load_source("romp_kernel_gr", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "22222222-3333-4444-5555-666666666666"
DEAD = "33333333-4444-5555-6666-777777777777"                        # a sid no live session answers to
REMOTE = "TESTHOST:11111111-2222-3333-4444-555555555555"             # a host-prefixed remote id


class _TagRouteHarness(unittest.TestCase):
    """The route harness alone -- the real Handler over HTTP, a private temp state dir per test, the
    live-name stub and the HTTP helpers -- with NO tests of its own: a class that needs the harness for
    cases of its own inherits this and runs only those. TagRoute below carries the route's fourteen cases;
    the store-fault classes used to inherit TagRoute and re-ran them under eight more names, 112 runs that
    told nothing new (review find, 2026-09-08). The door subclasses that predate them (HostForward and the
    rest) still inherit TagRoute on purpose: each changes a door the fourteen exercise."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        km._flags_cache.clear()
        self._saved = (km._live_map, km._live_names, km._mark_views_dirty)
        km._live_map = lambda: {}
        km._live_names = lambda tm: {"web": SID, "api": SID2}
        self.dirty = []                                       # the routes must poke the views push
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        (km._live_map, km._live_names, km._mark_views_dirty) = self._saved
        km.jd.STATE = self._state
        km._flags_cache.clear()
        self.td.cleanup()

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/tag" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def _views(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/views" % self.port,
            headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())


class TagRoute(_TagRouteHarness):
    """GET /views + POST /tag through the harness: the fourteen cases every route door must pass."""

    def test_get_views_serves_the_normalized_default(self):
        st, v = self._views()
        self.assertEqual(st, 200)
        self.assertEqual(v, {"active": "all", "tags": [],
                             "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}})   # per-surface lenses (2026-08-25)

    def test_create_resolves_a_live_name_and_mints_the_ui_id_shape(self):
        st, r = self._post({"name": "pool", "add": ["web"]})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        g = r.get("tag") or {}
        self.assertRegex(g.get("id") or "", r"^g[0-9a-z]+$",
                         "the UI's Date.now-base36 mint shape — one id shape either birthplace")
        self.assertEqual(g.get("name"), "pool")
        self.assertEqual(g.get("members"), [SID], "the live NAME resolved to its sid")
        self.assertTrue(self.dirty, "a successful edit marks views dirty — the dashboards' repaint signal")

    def test_sids_and_remote_ids_pass_through_verbatim(self):
        st, r = self._post({"name": "mixed", "add": [DEAD, REMOTE]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["members"], sorted([DEAD, REMOTE]),
                         "opaque ids stored as sent — the blob's own contract")

    def test_remove_drops_a_member_even_a_dead_one_by_sid(self):
        st, r = self._post({"name": "pool", "add": ["web", DEAD]})
        self.assertEqual(r["tag"]["members"], sorted([SID, DEAD]))
        st, r = self._post({"name": "pool", "remove": [DEAD]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["members"], [SID], "a dead member goes by sid")

    def test_color_is_stored_on_the_tag(self):
        self._post({"name": "pool", "add": ["web"]})
        st, r = self._post({"name": "pool", "color": "#DD42FF"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["color"], "#DD42FF")
        self.assertEqual(r["tag"]["members"], [SID], "a color edit does not clobber membership")

    def test_delete_removes_the_tag_and_the_active_view_falls_back(self):
        gid = "g1a2b3c"
        km._set_timeline_views({"active": gid, "hidden": [],
                                "tags": [{"id": gid, "name": "pool", "color": "",
                                            "members": [SID]}]})
        km._flags_cache.clear()
        st, v = self._views()
        self.assertEqual(v["active"], gid, "precondition: the tag IS the active view")
        st, r = self._post({"name": "pool", "delete": True})
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("deleted"))
        st, v = self._views()
        self.assertEqual(v["tags"], [])
        self.assertEqual(v["active"], "all", "the normalizer falls the orphaned active back to all")

    def test_an_unknown_add_name_refuses_loudly_and_writes_nothing(self):
        st, r = self._post({"name": "pool", "add": ["ghost"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("no live session named", r.get("error") or "")
        st, v = self._views()
        self.assertEqual(v["tags"], [], "a refused edit writes nothing — no member no session matches")

    def test_two_same_named_tags_refuse_any_edit(self):
        # a store ALREADY holding twins, written to the file: since the 2026-09-05 review
        # the write door refuses a second tag under a taken name, so twins come only from an older
        # kernel's store (or a hand edit)
        km._atomic_write(km._views_path(), json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "dup", "color": "", "members": []},
            {"id": "g2", "name": "dup", "color": "", "members": []}]}))
        km._flags_cache.clear()
        st, r = self._post({"name": "dup", "add": ["web"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("rename one in the dashboard first", r.get("error") or "")

    def test_the_tag_cap_refuses_a_33rd_instead_of_silently_dropping_it(self):
        tags33 = [{"id": "g%02d" % i, "name": "grp%02d" % i, "color": "", "members": []}
                  for i in range(32)]
        km._set_timeline_views({"active": "all", "hidden": [], "tags": tags33})
        km._flags_cache.clear()
        st, r = self._post({"name": "overflow", "add": []})
        self.assertFalse(r.get("ok"))
        self.assertIn("caps at 32", r.get("error") or "",
                      "the normalizer would drop the appended 33rd SILENTLY — the route must refuse")
        st, v = self._views()
        self.assertEqual(len(v["tags"]), 32)
        self.assertNotIn("overflow", [g["name"] for g in v["tags"]])

    def test_back_to_back_creates_mint_distinct_ids_and_delete_hits_only_its_tag(self):
        st, ra = self._post({"name": "alpha"})
        st, rb = self._post({"name": "beta"})
        ida, idb = ra["tag"]["id"], rb["tag"]["id"]
        self.assertNotEqual(ida, idb, "same-millisecond creates must not share an id")
        st, r = self._post({"name": "alpha", "delete": True})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual([g["id"] for g in v["tags"]], [idb],
                         "delete filters by id, so a shared id would have taken beta with it")

    def test_a_long_name_is_clamped_at_entry_so_it_stays_addressable(self):
        long_name = "x" * 44
        self._post({"name": long_name, "add": ["web"]})
        st, r = self._post({"name": long_name, "add": ["api"]})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual(len(v["tags"]), 1,
                         "the raw name must address the stored (clamped) tag — never mint a duplicate")
        self.assertEqual(v["tags"][0]["name"], "x" * 40)
        self.assertEqual(v["tags"][0]["members"],
                         [{"host": "", "sid": s} for s in sorted([SID, SID2])],
                         "stored members are canonical pairs (federation v0)")

    def test_a_host_prefixed_NAME_refuses_like_any_unknown_name(self):
        st, r = self._post({"name": "pool", "add": ["TESTHOST:exp-ghost"]})
        self.assertFalse(r.get("ok"), "host:NAME is not an id — storing it would never match a session")
        self.assertIn("no live session named", r.get("error") or "")

    def test_missing_name_is_a_400(self):
        st, r = self._post({"add": ["web"]})
        self.assertEqual(st, 400)

    def test_an_edit_merges_and_never_clobbers_the_rest_of_the_blob(self):
        GB = {"id": "gb", "name": "beta", "color": "#DD42FF", "members": ["s2"]}
        km._set_timeline_views({"active": "gb", "tags": [
            {"id": "ga", "name": "alpha", "color": "", "members": ["s1"]}, GB]})
        km._flags_cache.clear()
        seeded = next(g for g in km._timeline_views()["tags"] if g["id"] == "gb")
        st, r = self._post({"name": "alpha", "add": ["api"]})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual(v["active"], "gb", "the active view survives the merge")
        self.assertNotIn("hidden", v, "the hidden key is retired (2026-08-24) — never re-minted by an edit")
        # the seed write stamps mtime (creation IS an edit — tag federation v2); the pin is that
        # the edit to alpha touched NOTHING on beta, stamp included
        want = dict(GB, members=[{"host": "", "sid": "s2"}], mtime=seeded["mtime"])
        self.assertEqual([g for g in v["tags"] if g["id"] == "gb"], [want],
                         "the tag the edit never looked at is untouched (stored as pairs)")
        alpha = next(g for g in v["tags"] if g["id"] == "ga")
        self.assertEqual(alpha["members"], [{"host": "", "sid": s} for s in sorted(["s1", SID2])])


class HostForward(TagRoute):
    """POST /tag {"host": ...} — tag federation v0's edit path: the edit targets an ATTACHED
    kernel's store through the tunnel it already holds (Model A home-kernel ownership). The body
    forwards minus `host`; the TARGET resolves member names against ITS sessions; failures refuse
    loudly — an unreachable kernel must never silently no-op an edit."""

    def setUp(self):
        super().setUp()
        self._remotes_saved = dict(km._remotes)
        self._fwd_saved = km._remote_forward
        km._remotes.clear()
        km._remotes["alpha"] = {"host": "alpha", "status": "up"}
        km._remotes["down1"] = {"host": "down1", "status": "down"}
        self.forwarded = []
        km._remote_forward = lambda r, path, body: (self.forwarded.append((r["host"], path, body))
                                                    or {"ok": True, "tag": {"name": body.get("name")}})

    def tearDown(self):
        km._remotes.clear(); km._remotes.update(self._remotes_saved)
        km._remote_forward = self._fwd_saved
        super().tearDown()

    def test_host_edit_forwards_to_the_target_kernel_minus_the_host_key(self):
        st, r = self._post({"name": "team", "host": "alpha", "add": ["web"], "color": "#DD42FF"})
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(self.forwarded, [("alpha", "/tag",
                                           {"name": "team", "add": ["web"], "color": "#DD42FF"})],
                         "the target resolves names itself — the body forwards verbatim, minus host")
        self.assertEqual(km._timeline_views()["tags"], [],
                         "nothing lands on THIS kernel's store — the edit belongs to alpha")

    def test_unknown_and_down_hosts_refuse_loudly(self):
        st, r = self._post({"name": "team", "host": "ghost", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn('no attached kernel named "ghost"', r["error"])
        st, r = self._post({"name": "team", "host": "down1", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn("not reachable", r["error"])
        self.assertEqual(self.forwarded, [], "no forward is attempted either way")

    def test_a_dead_forward_surfaces_never_silently_noops(self):
        km._remote_forward = lambda r, path, body: None
        st, r = self._post({"name": "team", "host": "alpha", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn("never landed", r["error"])

    def test_a_string_delete_is_refused_before_the_host_forward(self):
        # the flag is checked ahead of the --host arm (review find, 2026-09-08: pinned by no test until
        # now), so a malformed delete never crosses to the home kernel, where an un-updated kernel
        # would still coerce it with bool()
        for bad in ("true", "false", 1):
            st, r = self._post({"name": "team", "host": "alpha", "delete": bad})
            self.assertEqual(st, 400, (bad, r))
            self.assertEqual(r.get("error"), "'delete' must be true or false, got %s" % json.dumps(bad))
        self.assertEqual(self.forwarded, [], "nothing reaches the tunnel while the flag is malformed")
        st, r = self._post({"name": "team", "host": "alpha", "delete": True})
        self.assertEqual(self.forwarded, [("alpha", "/tag", {"name": "team", "delete": True})],
                         "a real boolean forwards as itself")


class RenameAndHomeFrame(TagRoute):
    """Federation v1: /tag gains rename (collision-refusing), and a bare sid routed here from a
    THIRD kernel resolves into THIS kernel's own frame — viewer C adding kernel-B's session to our
    tag cannot know our name for B, but sids are global, so we look the sid up in our remotes'
    cached session lists and store the canonical pair ourselves."""

    def test_rename_lands_and_a_collision_refuses(self):
        self._post({"name": "alpha", "add": []})
        self._post({"name": "beta", "add": []})
        st, r = self._post({"name": "alpha", "rename": "gamma"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["tag"]["name"], "gamma")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["beta", "gamma"])
        st, r = self._post({"name": "gamma", "rename": "beta"})
        self.assertFalse(r["ok"])
        self.assertIn('a tag named "beta" already exists', r["error"])

    def test_a_third_kernels_bare_sid_lands_in_OUR_frame(self):
        # TESTHOST-A (this kernel) holds the tag; TESTHOST-B owns the session; viewer TESTHOST-C
        # routed the edit here with the bare sid tail — we know that sid as TESTHOST-B's
        bsid = "44444444-5555-6666-7777-888888888888"
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            km._remotes["TESTHOST-B"] = {"host": "TESTHOST-B", "status": "up", "sids": [bsid]}
            st, r = self._post({"name": "team", "add": [bsid]})
            self.assertTrue(r["ok"])
            self.assertEqual(km._timeline_views()["tags"][0]["members"],
                             [{"host": "TESTHOST-B", "sid": bsid}],
                             "the canonical pair carries OUR label for B — never the viewer's")
            # …while a sid nobody knows stays bare (legacy behavior: inert until known)
            ghost = "99999999-aaaa-bbbb-cccc-dddddddddddd"
            self._post({"name": "team", "add": [ghost]})
            self.assertIn({"host": "", "sid": ghost}, km._timeline_views()["tags"][0]["members"])
        finally:
            km._remotes.clear(); km._remotes.update(saved)


class EditTagOpPins(unittest.TestCase):
    """The WS op the dialog rides (federation v1) — source pins: only the BARE sid tail crosses
    kernels (this viewer's host labels mean nothing on the owner), the failure pushes a LOUD
    tagEditFailed to the asking dashboard, and a landed edit marks views dirty either way."""

    def test_the_op_tails_ids_forwards_and_fails_loudly(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "editTag"', src)
        self.assertIn('tail = lambda x: x.rsplit(":", 1)[-1]', src)
        self.assertIn('ans, err = _forward_tag_edit(host, body)', src)
        self.assertIn('{"type": "tagEditFailed", "host": host, "name": nm,', src)
        self.assertIn('(client or {}).get("wid") or ""', src, "the refusal goes to the ASKING dashboard")


class ForwardHelper(TagRoute):
    """_forward_tag_edit — the one channel every remote edit rides (romp tag --host, the dialog's
    editTag op): loud refusals, response passthrough, and the FAST ECHO (a landed edit drops the
    owner's poll gate and refreshes its cached views inline, so the optimistic copy reconciles
    within a push, not a poll period)."""

    def setUp(self):
        super().setUp()
        # its own name, never the harness's `_saved`: rebinding that tuple here made _TagRouteHarness.tearDown
        # restore km._mark_views_dirty to km._poll_remote_views for every test after this class (latent while
        # each test stubbed the mark; the heal's real mark met it, 2026-09-08)
        self._helper_saved = (dict(km._remotes), km._remote_forward, km._poll_remote_views)
        km._remotes.clear()
        km._remotes["alpha"] = {"host": "alpha", "status": "up", "_views_at": 12345.0,
                                "views": {"tags": []}}
        self.polled = []
        km._remote_forward = lambda r, path, body: {"ok": True, "tag": {"name": body.get("name")}}
        km._poll_remote_views = lambda r: (self.polled.append(r["host"]) or {"tags": [{"id": "g1", "name": "team", "members": []}]})

    def tearDown(self):
        km._remotes.clear(); km._remotes.update(self._helper_saved[0])
        km._remote_forward, km._poll_remote_views = self._helper_saved[1], self._helper_saved[2]
        super().tearDown()

    def test_a_landed_edit_echoes_fast(self):
        ans, err = km._forward_tag_edit("alpha", {"name": "team", "add": []})
        self.assertIsNone(err)
        self.assertTrue(ans["ok"])
        self.assertEqual(self.polled, ["alpha"], "the owner's views re-poll inline — the fast echo")
        self.assertNotIn("_views_at", km._remotes["alpha"], "…and the poll gate stays dropped for the loop")
        self.assertEqual(km._remotes["alpha"]["views"]["tags"][0]["name"], "team")

    def test_refusals_stay_loud(self):
        self.assertEqual(km._forward_tag_edit("ghost", {"name": "t"})[1],
                         'no attached kernel named "ghost" (see the network panel)')
        km._remotes["alpha"]["status"] = "down"
        self.assertIn("not reachable", km._forward_tag_edit("alpha", {"name": "t"})[1])
        km._remotes["alpha"]["status"] = "up"
        km._remote_forward = lambda r, path, body: None
        self.assertIn("never landed", km._forward_tag_edit("alpha", {"name": "t"})[1])


class GroupAliasSurvives(TagRoute):
    """The pre-rename surface (same-day rename, 2026-08-23): an un-updated remote's bin/romp still
    POSTs /group and reads the "group" response key — both stay as quiet aliases of /tag."""

    def test_post_group_still_merges_and_answers_with_both_keys(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/group" % self.port, data=json.dumps({"name": "legacy", "add": ["web"]}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["tag"]["name"], "legacy")
        self.assertEqual(resp["group"], resp["tag"], "the pre-rename key mirrors the tag row")
        self.assertEqual(resp["tag"]["members"], [SID], "a live name resolved through the alias route too")


class HttpFlagsMustBeBooleans(TagRoute):
    """The kernel's HTTP flag fields take JSON true/false and nothing else. Each used to be coerced with
    bool(), so the STRING "false" deleted a tag, turned a master bell on, armed auto-update, flipped
    check-in on a host and made a directory. Now a non-boolean is a 400 naming the field and the
    setting is untouched; `romp tag` / `romp checkin` and the pages send real booleans, so their calls
    are unchanged."""

    def _post_to(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_a_string_delete_refuses_and_the_tag_survives(self):
        self._post({"name": "pool", "add": ["web"]})
        for bad in ("false", "true", 1, "no"):
            st, r = self._post({"name": "pool", "delete": bad})
            self.assertEqual(st, 400, (bad, r))
            self.assertEqual(r.get("error"), "'delete' must be true or false, got %s" % json.dumps(bad))
            self.assertEqual([g["name"] for g in self._views()[1]["tags"]], ["pool"],
                             "the tag survives a refused delete (a string used to delete it)")
        st, r = self._post({"name": "pool", "delete": False})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["name"], "pool", "a real false is an edit, not a delete")
        st, r = self._post({"name": "pool", "delete": True})
        self.assertTrue(r.get("deleted"), r)
        self.assertEqual(self._views()[1]["tags"], [])

    def test_the_master_bells_take_only_booleans(self):
        for path, reader in (("/notify-all", km._notify_all_on), ("/notify-turns", km._notify_turns_on)):
            self.assertFalse(reader(), path)
            for bad in ("false", "true", 1, "no", "on"):
                st, r = self._post_to(path, {"on": bad})
                self.assertEqual(st, 400, (path, bad, r))
                self.assertEqual(r.get("error"), "'on' must be true or false, got %s" % json.dumps(bad))
                self.assertFalse(reader(), "%s: a refused flip leaves the bell as it was" % path)
            st, r = self._post_to(path, {"on": True})
            self.assertEqual((st, r.get("ok"), r.get("on")), (200, True, True), (path, r))
            self.assertTrue(reader(), path)
            st, r = self._post_to(path, {"on": False})
            self.assertEqual((st, r.get("on")), (200, False), (path, r))
            self.assertFalse(reader(), path)

    def test_a_long_flag_value_echoes_clipped_and_well_formed(self):
        st, r = self._post_to("/notify-all", {"on": "a" * 5000})
        self.assertEqual(st, 400, r)
        self.assertEqual(r.get("error"), "'on' must be true or false, got \"" + "a" * 60 + '\u2026"',
                         "the cut lands inside the quotes, marked -- never an unclosed quote or 5000 chars")
        self.assertLess(len(r["error"]), 100)
        self.assertFalse(km._notify_all_on())

    def test_a_container_flag_value_echoes_well_formed_too(self):
        # review find, 2026-09-08: a long string INSIDE a container used to clip to an unclosed quote
        st, r = self._post_to("/notify-all", {"on": {"nested": ["a" * 5000]}})
        self.assertEqual(st, 400, r)
        self.assertEqual(r.get("error"), "'on' must be true or false, got " + '{"nested": ["' + "a" * 60 + '\u2026"]}')
        self.assertFalse(km._notify_all_on())

    def test_an_explicit_null_flag_reads_as_absent(self):
        # the rule _as_bool states (review find, 2026-09-08): null is the absent case spelled out, so it
        # takes the route's default -- an edit, not a delete; the bell off -- where a string or a number
        # is refused
        self._post({"name": "pool", "add": ["web"]})
        st, r = self._post({"name": "pool", "delete": None})
        self.assertEqual((st, r.get("ok")), (200, True), r)
        self.assertEqual([g["name"] for g in self._views()[1]["tags"]], ["pool"], "null is not a delete")
        self._post_to("/notify-all", {"on": True})
        self.assertTrue(km._notify_all_on())
        st, r = self._post_to("/notify-all", {"on": None})
        self.assertEqual((st, r.get("ok"), r.get("on")), (200, True, False), r)
        self._post_to("/notify-all", {"on": True})
        st, r = self._post_to("/notify-all", {})
        self.assertEqual((st, r.get("on")), (200, False), "the same answer an absent field gets")

    def test_auto_update_takes_only_a_boolean(self):
        self.assertFalse(km._auto_update_remotes_on())
        for bad in ("false", "true", 1, "yes"):
            st, r = self._post_to("/tunnels/autoupdate", {"on": bad})
            self.assertEqual(st, 400, (bad, r))
            self.assertEqual(r.get("error"), "'on' must be true or false, got %s" % json.dumps(bad))
            self.assertFalse(km._auto_update_remotes_on(), "a refused flip never starts pushing code")
        st, r = self._post_to("/tunnels/autoupdate", {"on": True})
        self.assertEqual((st, r.get("on")), (200, True), r)
        self.assertTrue(km._auto_update_remotes_on())
        st, r = self._post_to("/tunnels/autoupdate", {"on": False})
        self.assertFalse(km._auto_update_remotes_on())

    def test_checkin_refuses_a_string_before_looking_the_host_up(self):
        calls = []
        saved = km.checkin_set
        km.checkin_set = lambda host, on: calls.append((host, on)) or {"host": host}
        try:
            for bad in ("false", "true", 0):
                st, r = self._post_to("/tunnels/checkin", {"host": "TESTHOST", "on": bad})
                self.assertEqual(st, 400, (bad, r))
                self.assertEqual(r.get("error"), "'on' must be true or false, got %s" % json.dumps(bad))
            self.assertEqual(calls, [], "nothing reaches the tunnel while the flag is malformed")
            st, r = self._post_to("/tunnels/checkin", {"host": "TESTHOST", "on": False})
            self.assertEqual((st, r.get("ok")), (200, True), r)
            self.assertEqual(calls, [("TESTHOST", False)], "a real boolean rides through as itself")
        finally:
            km.checkin_set = saved

    def test_new_refuses_a_string_mkdir_before_touching_the_disk(self):
        calls = []
        saved = km._resolve_create_dir
        km._resolve_create_dir = (lambda raw, create=False:
                                  calls.append((raw, create)) or ("", "stubbed: no directory here"))
        try:
            for bad in ("true", "false", 1):
                st, r = self._post_to("/new", {"name": "fresh", "dir": "/nonexistent/TESTHOST", "mkdir": bad})
                self.assertEqual(st, 400, (bad, r))
                self.assertEqual(r.get("error"), "'mkdir' must be true or false, got %s" % json.dumps(bad))
            self.assertEqual(calls, [], "no directory is resolved, let alone created, on a malformed flag")
            st, r = self._post_to("/new", {"name": "fresh", "dir": "/nonexistent/TESTHOST", "mkdir": True})
            self.assertEqual(calls, [("/nonexistent/TESTHOST", True)], "a real true asks for the create")
            self.assertEqual((st, r.get("ok"), r.get("error")), (200, False, "stubbed: no directory here"))
        finally:
            km._resolve_create_dir = saved
# ── the views store under a FAULT (the state-readers audit: the flag, order and bell stores' twin) ──────
# The reader used to fold ANY read fault (a transient EIO, an EACCES, torn bytes) to an empty blob and
# CACHE it under the file's real (mtime, size) key, so after one fault the store read as {} on every
# call until the file's stat moved; every read-modify-write door then wrote that emptiness plus its one
# edit back -- the user's whole tag set, every lens and the order -- under an ok:true ack, while every
# dashboard adopted the seq-less {} and its tag bar emptied with nothing said. Now the mutation path
# reads PROVED (_timeline_views_proved raises), every door refuses in its own shape with the file
# untouched, the display path serves its last-known-good uncached and files one notice per episode,
# and torn bytes are moved aside. Synthetic sids and hosts only; a private temp state dir per test
# (TagRoute.setUp), so no shared placeholder store is ever touched.


@contextlib.contextmanager
def _stat_fault(target):
    """Fail every stat of ONE path with an EACCES for the duration of the block: every reader stats
    BEFORE it reads (the display reader keys its cache on it), and a state dir that cannot be searched
    faults exactly there. Everything else stats normally."""
    real_stat = Path.stat
    tgt = str(target)

    def st(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EACCES, "injected EACCES")
        return real_stat(self, *a, **k)
    Path.stat = st
    try:
        yield
    finally:
        Path.stat = real_stat


@contextlib.contextmanager
def _stat_faults_after(target, n):
    """Path.stat on ONE path succeeds for its first `n` calls, then fails with EACCES: the display
    reader's in-lock re-stat (its second stat of the file, under _views_file_lock before a re-stamp) is
    the arm no other injector reaches. Yields the call counter."""
    real_stat = Path.stat
    tgt, calls = str(target), [0]

    def st(self, *a, **k):
        if str(self) == tgt:
            calls[0] += 1
            if calls[0] > n:
                raise OSError(errno.EACCES, "injected EACCES")
        return real_stat(self, *a, **k)
    Path.stat = st
    try:
        yield calls
    finally:
        Path.stat = real_stat


@contextlib.contextmanager
def _reads_fault(target):
    """Fail every byte read of ONE path with an EIO (the proved reader's read_bytes and the pre-fix
    reader's read_text alike) for the duration of the block; everything else reads normally."""
    real_rb, real_rt = Path.read_bytes, Path.read_text
    tgt = str(target)

    def rb(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rb(self, *a, **k)

    def rt(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rt(self, *a, **k)
    Path.read_bytes, Path.read_text = rb, rt
    try:
        yield
    finally:
        Path.read_bytes, Path.read_text = real_rb, real_rt


@contextlib.contextmanager
def _writes_fault(target):
    """Fail the PUBLISH of ONE state file with an ENOSPC for the duration of the block: _atomic_write
    writes `<name>.tmp.<pid>.<tid>.<n>` beside the file and renames it over, so failing every write_text
    of that shape is the disk refusing this file's publish while every other path, and every read, behaves."""
    real = Path.write_text
    prefix = Path(target).name + ".tmp."

    def wt(self, *a, **k):
        if self.name.startswith(prefix):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)
    Path.write_text = wt
    try:
        yield
    finally:
        Path.write_text = real


class _ClockAhead:
    """The kernel's `time` module with time() moved `by` seconds ahead and everything else delegated. The
    store's stamps (each tag's mtime, the blob's `at`) are whole seconds off the kernel's clock, so a case
    that needs a later write to land in a LATER second moves the clock instead of sleeping across the
    boundary (review find, 2026-09-08: 1.1 s of wall clock per run; the stamps live inside the blob, so a
    file utime would not move them). Kernel-local: km.time is the name the kernel's code reads; this
    module's `time` and the HTTP machinery's are untouched."""

    def __init__(self, by):
        self._by = by

    def time(self):
        return time.time() + self._by

    def __getattr__(self, name):
        return getattr(time, name)


@contextlib.contextmanager
def _clock_ahead(by):
    saved = km.time
    km.time = _ClockAhead(by)
    try:
        yield
    finally:
        km.time = saved


class _ViewsFaultMixin:
    """The store-fault harness on top of the route harness (_TagRouteHarness; a mixin, so it is not collected
    on its own -- and the harness, not TagRoute, so its fourteen cases run once, review find 2026-09-08): the fault
    registries and the deferred-heal map start and end empty (process-wide state -- a fault filed here
    must not silence a later test's), the notice ring is captured WITH the kind the fault machinery files
    under (a two-argument stub raises on `kind=`, and the machinery's own try swallows the notice), and
    helpers for a seeded store and a dashboard socket."""

    def setUp(self):
        super().setUp()
        km._state_fault_seen.clear()
        km._state_write_fault_seen.clear()
        vars(km).get("_views_heal_pending", {}).clear()
        km._VIEWS_RESTAMP_ERR[0] = None
        self.notices = []
        self._saved_notice = km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok, kind))

    def tearDown(self):
        km._sync_notice = self._saved_notice
        km._state_fault_seen.clear()
        km._state_write_fault_seen.clear()
        vars(km).get("_views_heal_pending", {}).clear()
        super().tearDown()

    def _seed(self, *names):
        """One tag per name, each holding "web" (SID), written through the route -- a real, stamped store;
        returns the file's bytes for the untouched-store assertions. The cache is left as the writes primed it."""
        for nm in names:
            st, resp = self._post({"name": nm, "add": ["web"]})
            self.assertTrue(resp.get("ok"), resp)
        return self._bytes()

    def _bytes(self):
        return km._views_path().read_bytes()

    def _client(self):
        sent = []
        return {"app": "timeline", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}, sent

    def _ws(self, msg, client):
        km.Handler._dispatch_ws(object.__new__(km.Handler), msg, client)

    def _faults(self, what="read"):
        """The store's fault notices of one kind (read / written), as (text, bell kind) pairs. A quarantine's
        "could not be parsed" notice is a different event and is not counted here."""
        return [(t, k) for t, ok, k in self.notices if not ok and "timeline-views.json could not be %s" % what in t]


class ViewsStoreUnreadableRefuses(_ViewsFaultMixin, _TagRouteHarness):
    """A store that EXISTS but cannot be read. The mutation path refuses in its own shape and writes nothing;
    the judge never folds; the display path serves what it last knew, caches nothing from the fault, and
    says so once per episode."""

    def test_a_tag_edit_is_refused_when_the_views_store_cannot_be_read(self):
        # on main the cold display read folded the EIO to {} and cached it; _edit_tag found no "workers",
        # CREATED one holding only api, and landed a ONE-tag store over the seeded two -- 200 ok:true
        before = self._seed("workers", "reviewers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            status, resp = self._post({"name": "workers", "add": ["api"]})
        self.assertEqual((status, resp.get("ok"), resp.get("retryable")), (200, False, True),
                         "the route's OWN refusal shape: romp tag reads .error, and curl -sf turns a 5xx into 'not reachable'")
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", resp["error"])
        self.assertIn("nothing was changed", resp["error"])
        self.assertEqual(self._bytes(), before, "the views file is byte-for-byte unchanged: nothing was written over an empty")
        self.assertEqual(self.dirty, [1, 1], "only the seeds marked the views dirty; the refusal pushed nothing")

    def test_a_stat_fault_serves_the_last_good_blob_and_refuses_the_edit(self):
        # the STAT arm: every reader stats before it reads, and a state dir that cannot be searched faults
        # there. On main ANY stat OSError read as "no store": the cache entry was forgotten and {} served.
        before = self._seed("workers")
        km._flags_cache.clear()
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["workers"])   # the known-good, primed
        with _stat_fault(km._views_path()):
            served = km._timeline_views()
            self.assertEqual([t["name"] for t in served["tags"]], ["workers"], "the last good blob, not {}")
            self.assertIn(str(km._views_path()), km._flags_cache, "the entry is kept: the store is unreadable, not gone")
            status, resp = self._post({"name": "workers", "add": ["api"]})
        self.assertEqual((status, resp.get("ok"), resp.get("retryable")), (200, False, True))
        self.assertIn("the tag store could not be read (stat failed: [Errno 13]", resp["error"])
        self.assertEqual(self._bytes(), before)
        self.assertEqual(len(self._faults()), 1, "one notice for the episode, from the display read")
        self.assertIn("timeline-views.json could not be read (stat failed: [Errno 13]", self._faults()[0][0])
        self.assertEqual(self._faults()[0][1], "refused", "filed under the bell's refused kind, not sync")

    def test_the_judge_never_folds_a_read_fault_to_an_empty_base(self):
        # the stale-writer guard's "previous truth" came from the display reader inside `except Exception:
        # base = {}`; a lens write (`edited: []`) then kept prev.values() -- nothing -- and stored `tags: []`
        self._seed("workers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            with self.assertRaises(km._StateUnreadable):
                km._judge_timeline_views({"active": "all", "tags": []}, edited=[])

    def test_a_torn_views_file_is_quarantined_aside_then_the_store_reads_empty(self):
        torn = b'{"active": "all", "tags": [ THIS IS NOT JSON'
        p = km._views_path()
        p.write_bytes(torn)
        km._flags_cache.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            v = km._timeline_views_proved()
        self.assertEqual(v["tags"], [], "the store starts empty ONLY after the bytes are preserved")
        q = list(km.jd.STATE.glob("timeline-views.json.corrupt-*"))
        self.assertEqual(len(q), 1, "the torn bytes were moved aside, not deleted")
        self.assertEqual(q[0].read_bytes(), torn, "the quarantine holds the ORIGINAL bytes for forensics")
        self.assertFalse(p.exists(), "the torn file was moved, not left in place for the next writer to overwrite")
        self.assertEqual(len([t for t, ok, k in self.notices if "moved aside" in t and k == "refused"]), 1,
                         "the dashboard hears the move once, under the refused kind")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._timeline_views()["tags"], [], "the display reader agrees: empty now, not a fault")
        self.assertEqual(self._faults(), [], "a quarantine is a stated event, not a read fault")

    def test_the_quarantine_notice_names_the_tags_and_lenses_it_held_not_settings(self):
        # review find, 2026-09-08: the notice said "the settings it held start over", the words for the flags,
        # order and bell stores; the views store holds the user's tags and lenses, and the notice says so
        p = km._views_path()
        p.write_bytes(b'{"active": "all", "tags": [ THIS IS NOT JSON')
        km._flags_cache.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()
        rows = [t for t, ok, k in self.notices if "moved aside" in t and k == "refused"]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("the tags and lenses it held start over empty until you set them again", rows[0])
        self.assertNotIn("settings", rows[0])

    def test_enoent_views_reads_empty_with_no_quarantine_and_no_fault_filed(self):
        try:
            km._views_path().unlink()
        except OSError:
            pass
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"], [], "a missing store is legitimately empty")
        self.assertEqual(km._timeline_views_proved()["tags"], [], "…and the mutation snapshot agrees")
        self.assertEqual(list(km.jd.STATE.glob("timeline-views.json.corrupt-*")), [])
        self.assertEqual(self.notices, [], "a missing file is not a fault: nothing is filed")
        self.assertEqual(km._state_fault_seen, {})

    def test_a_display_read_fault_never_raises_into_the_build_and_is_loud_once_per_episode(self):
        # build_feed / build_timeline / build_session run under _push's ONE outer try, so a display reader
        # that RAISED would abort every client's push. The display path has nothing known-good here (a cold
        # cache), so the feed carries the fault marker and NO blob (review find, 2026-09-08: the seq-less
        # empty default it carried was adopted by every dashboard as the store's word), and files exactly
        # ONE notice per fault episode.
        self._seed("workers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            feed1 = km.build_feed(int(time.time()))
            self.assertEqual(feed1.get("type"), "feed", "build_feed still returns a payload: the board is not wedged")
            self.assertNotIn("views", feed1, "nothing known-good: no blob, so no dashboard adopts an empty store")
            self.assertIn("the tag store could not be read (read failed: [Errno 5]", feed1["viewsFault"],
                          "the frame says why it carries no tags")
            self.assertEqual(len(self._faults()), 1, "exactly ONE notice the first time the fault is seen")
            self.assertIn("timeline-views.json could not be read (read failed: [Errno 5]", self._faults()[0][0])
            self.assertEqual(self._faults()[0][1], "refused")
            km.build_feed(int(time.time()))                       # a second build in the SAME episode files nothing new
            self.assertEqual(len(self._faults()), 1)
        km._flags_cache.clear()
        feed3 = km.build_feed(int(time.time()))
        self.assertEqual([t["name"] for t in feed3["views"]["tags"]], ["workers"],
                         "after the fault clears the real tag reads again: the reader never cached the empty")
        self.assertNotIn(str(km._views_path()), km._state_fault_seen,
                         "a clean read ends the episode, so a later fault speaks again (event-based)")

    def test_the_display_reader_never_caches_a_value_that_came_from_a_fault(self):
        # a reader that cached the fabricated empty under the file's (mtime, size) key kept serving it after
        # the fault cleared (same key, and a cache HIT never reads): one EIO, then {} until the file moved
        self._seed("workers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            self.assertEqual(km._timeline_views()["tags"], [], "a cold cache under a fault serves the empty default")
            self.assertNotIn(str(km._views_path()), km._flags_cache, "…and the fabricated empty is NOT cached under the file's key")
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["workers"], "the next read is the real store")

    def test_a_fault_serves_the_last_read_blob_and_caches_nothing_under_the_new_key(self):
        # the primed-cache case: a HIT under the same stat key never reads, so the file is moved on first
        # and the cache set back to what a reader that missed that write holds -- the faulted read is then a
        # MISS that must serve the LAST blob read, and must not cache it (or {}) under the NEW key, or the
        # recovered disk would never be read again
        self._seed("workers")
        km._flags_cache.clear()
        first = km._timeline_views()
        self.assertEqual([t["name"] for t in first["tags"]], ["workers"])
        key1 = km._flags_cache[str(km._views_path())][0]
        st, resp = self._post({"name": "reviewers", "add": ["api"]})   # the file moves on
        self.assertTrue(resp.get("ok"), resp)
        km._flags_cache[str(km._views_path())] = (key1, first)        # as held by a reader that missed the write
        st2 = km._views_path().stat()
        self.assertNotEqual(key1, (st2.st_mtime_ns, st2.st_size), "the edit must move the stat key, or this test reads nothing")
        with _reads_fault(km._views_path()):
            served = km._timeline_views()
            self.assertEqual([t["name"] for t in served["tags"]], ["workers"], "the LAST blob read: not {} and not the unread file")
            self.assertEqual(km._flags_cache[str(km._views_path())][0], key1, "nothing cached under the new key")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["reviewers", "workers"],
                         "the real, newer file reads once the fault clears")

    def test_a_stat_fault_under_the_re_stamp_lock_serves_the_last_good_blob_and_is_said_once(self):
        # the in-lock re-stat: the first stat (outside the lock) passes, the file reads, the re-stamp takes the
        # lock and stats again -- and THAT one faults. Main folded it to {}; the arm re-enters the reader, whose
        # stat arm serves the last good blob, files the fault once and caches nothing
        self._seed("workers")
        km._flags_cache.clear()
        good = km._timeline_views()                                  # the last good blob
        p = km._views_path()
        legacy = {"active": "all", "tags": [{"id": "gL", "name": "legacy", "color": "",
                                              "members": [{"host": "", "sid": SID}]}]}
        p.write_text(json.dumps(legacy))                             # a seq-less file: the read wants to re-stamp it
        km._flags_cache[str(p)] = (("stale", 0), good)               # an entry that misses (a reader that missed the write)
        with _stat_faults_after(p, 1) as calls, contextlib.redirect_stderr(io.StringIO()):
            served = km._timeline_views()
        self.assertGreaterEqual(calls[0], 2, "the re-stamp's in-lock re-stat was reached")
        self.assertEqual([t["name"] for t in served["tags"]], ["workers"], "the last good blob, not {}")
        self.assertEqual(km._flags_cache[str(p)], (("stale", 0), good), "nothing cached from the fault")
        self.assertEqual(len(self._faults()), 1, "said once")
        self.assertIn("stat failed: [Errno 13]", self._faults()[0][0])
        self.assertEqual(p.read_text(), json.dumps(legacy), "no re-stamp was written")


class ViewsWsRefusal(_ViewsFaultMixin, _TagRouteHarness):
    """The two dashboard doors under a store fault, through the real dispatcher: the poster is answered on
    its own socket with the fault in the person's words, the file is untouched, the socket lives."""

    LENS = {"active": "all", "tags": [], "actives": {"chat": {"none": True}}}   # a lens write's blob, as a dashboard posts it

    def test_a_lens_write_under_a_read_fault_is_refused_on_the_socket_and_the_tags_stand(self):
        # THE erasure on the whole-blob door: a lens or order write carries `edited: []`; the judge's lens_only
        # arm kept prev.values() -- EMPTY off the poisoned base -- and stored `tags: []`, acked ok:true
        before = self._seed("workers", "reviewers")
        km._flags_cache.clear()
        client, sent = self._client()
        with _reads_fault(km._views_path()):
            self._ws({"type": "setTimelineViews", "writeId": "w1", "edited": [], "views": dict(self.LENS)}, client)
        self.assertEqual(len(sent), 1, "answered on the posting socket")
        ack = sent[0]
        self.assertEqual((ack["type"], ack["writeId"], ack["ok"]), ("viewsAck", "w1", False))
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", ack["error"])
        self.assertIn("nothing was changed", ack["error"])
        self.assertEqual(ack["refused"], [], "a store fault is not a stale-writer refusal: no rows")
        self.assertEqual(self._bytes(), before, "the views file is byte-for-byte unchanged: `tags: []` did not land")
        self.assertTrue(client["alive"])
        km._flags_cache.clear()
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["reviewers", "workers"])

    def test_a_tag_create_under_a_read_fault_is_refused_and_keeps_every_tag(self):
        # on main _edit_tag read the poisoned {} and created the tag anew: a ONE-tag store landed, acked ok
        before = self._seed("workers", "reviewers")
        km._flags_cache.clear()
        client, sent = self._client()
        with _reads_fault(km._views_path()):
            self._ws({"type": "tagEdit", "writeId": "w2", "edit": {"op": "create", "name": "api", "sids": [SID2]}}, client)
        ack = sent[0]
        self.assertEqual((ack["type"], ack["writeId"], ack["ok"]), ("tagEditAck", "w2", False))
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", ack["error"])
        self.assertNotIn("tid", ack, "no tag was minted")
        self.assertEqual(self._bytes(), before)

    def test_a_rename_by_tid_under_a_read_fault_names_the_fault_not_a_deleted_tag(self):
        # on main the poisoned {} held no such tid, so the rename was refused as "that tag no longer exists" --
        # loud, and WRONG: the tag is right there in the file
        st, resp = self._post({"name": "workers", "add": ["web"]})
        tid = resp["tag"]["id"]
        before = self._bytes()
        km._flags_cache.clear()
        client, sent = self._client()
        with _reads_fault(km._views_path()):
            self._ws({"type": "tagEdit", "writeId": "w3", "edit": {"op": "rename", "tid": tid, "newName": "crew"}}, client)
        ack = sent[0]
        self.assertFalse(ack["ok"])
        self.assertNotIn("no longer exists", ack["error"], "the tag was not deleted; the store could not be read")
        self.assertIn("the tag store could not be read", ack["error"])
        self.assertEqual(self._bytes(), before)

    def test_a_view_change_whose_publish_fails_is_refused_with_the_fault_named_and_the_socket_kept(self):
        # the WRITE step (the maintainer's fold on PR #1019): the store reads, the judge passes, the publish hits
        # ENOSPC. On main the arm caught the OSError as a generic failure ("the write failed on the kernel:
        # [Errno 28] ... '<temp path>'") and filed nothing on the fault table; the door raises _StateUnwritable
        # now, filed once per episode, and the ack carries the person's words
        before = self._seed("workers")
        client, sent = self._client()
        p = km._views_path()
        with _writes_fault(p):
            self._ws({"type": "setTimelineViews", "writeId": "w4", "edited": [], "views": dict(self.LENS)}, client)
            self._ws({"type": "setTimelineViews", "writeId": "w5", "edited": [], "views": dict(self.LENS)}, client)
        self.assertTrue(client["alive"], "no OSError reached the receive loop")
        self.assertEqual([(a["writeId"], a["ok"]) for a in sent], [("w4", False), ("w5", False)], "every attempt is answered")
        self.assertIn("the tag store could not be written (write failed: [Errno 28] No space left on device)", sent[0]["error"])
        self.assertIn("nothing was changed", sent[0]["error"])
        self.assertNotIn(".tmp.", sent[0]["error"], "errno + strerror only, never the temp path")
        self.assertEqual(self._bytes(), before, "the views file is byte-for-byte unchanged")
        self.assertEqual(len(self._faults("written")), 1, "the fault is filed ONCE per episode, not per gesture")
        self.assertIn(str(p), km._state_write_fault_seen)
        self._ws({"type": "setTimelineViews", "writeId": "w6", "edited": [], "views": dict(self.LENS)}, client)   # the disk heals
        self.assertTrue(sent[-1]["ok"], sent[-1])
        self.assertNotIn(str(p), km._state_write_fault_seen, "a landed write ends the episode")
        km._flags_cache.clear()
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["workers"], "a lens write changes no tag")

    def test_a_move_under_a_read_fault_is_refused_with_the_fault_named_and_the_file_unchanged(self):
        # the move door (_move_tag_member) reads proved like the rest: a door reading the display reader folds
        # the fault to {} and refuses as "the tag to move out of no longer exists" -- its own text, and wrong
        st, r1 = self._post({"name": "workers", "add": ["web"]})
        st, r2 = self._post({"name": "reviewers", "add": ["api"]})
        before = self._bytes()
        km._flags_cache.clear()
        client, sent = self._client()
        with _reads_fault(km._views_path()):
            self._ws({"type": "tagEdit", "writeId": "w7",
                      "edit": {"op": "move", "tid_from": r1["tag"]["id"], "tid_to": r2["tag"]["id"], "sid": SID}}, client)
        ack = sent[0]
        self.assertEqual((ack["type"], ack["writeId"], ack["ok"]), ("tagEditAck", "w7", False))
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", ack["error"])
        self.assertNotIn("no longer exists", ack["error"], "the tags are right there; the store could not be read")
        self.assertEqual(self._bytes(), before)
        km._flags_cache.clear()
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["reviewers", "workers"])


class ViewsStoreUnwritableRefuses(_ViewsFaultMixin, _TagRouteHarness):
    """The views store READS but its PUBLISH fails (ENOSPC, EROFS, EACCES): the write step is a fault
    boundary too. On main the OSError out of _atomic_write escaped _edit_tag to do_POST's catch-all -- a
    500 traceback, which `romp tag` (curl -sf) reports as an unreachable kernel and a federation forward
    as a tunnel hiccup. Now the publish raises _StateUnwritable and the route answers its own shape."""

    def test_a_tag_edit_whose_publish_fails_is_refused_in_the_route_shape(self):
        before = self._seed("workers")
        p = km._views_path()
        with _writes_fault(p):
            st, r = self._post({"name": "workers", "add": ["api"]})          # a 500 traceback on main
            st2, r2 = self._post({"name": "workers", "add": ["api"]})        # and again, on the same full disk
        self.assertEqual(st, 200, "a failed publish rides the route's own 200 shape, never a 500")
        self.assertEqual((r["ok"], r["retryable"]), (False, True))
        self.assertIn("the tag store could not be written (write failed: [Errno 28] No space left on device)", r["error"])
        self.assertNotIn(".tmp.", r["error"], "errno + strerror only, never the temp path")
        self.assertEqual((st2, r2["ok"]), (200, False), "every attempt is answered")
        self.assertEqual(p.read_bytes(), before, "the views file is byte-for-byte unchanged")
        self.assertEqual(len(self.dirty), 1, "only the seed marked the views dirty")
        self.assertEqual(len(self._faults("written")), 1, "the fault is filed ONCE per episode, not per edit")
        st, r = self._post({"name": "workers", "add": ["api"]})              # the disk heals
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(sorted(r["tag"]["members"]), sorted([SID, SID2]), "the edit lands and ends the episode")
        self.assertNotIn(str(p), km._state_write_fault_seen)

    def test_the_setter_raises_the_plain_exception_never_the_os_error(self):
        # the WS receive loop re-raises (BrokenPipeError, ConnectionResetError, OSError) as a dead socket; a
        # plain Exception is one logged line. On main the setter raised the OSError itself.
        with _writes_fault(km._views_path()):
            with self.assertRaises(km._StateUnwritable) as cm:
                km._set_timeline_views({"active": "all", "tags": []})
        self.assertNotIsInstance(cm.exception, OSError)
        self.assertFalse(issubclass(km._StateUnwritable, OSError))
        self.assertEqual(str(cm.exception),
                         "timeline-views.json could not be written (write failed: [Errno 28] No space left on device)")
        self.assertFalse(km._views_path().exists(), "nothing was published")


class HealHasABoundaryInTheBuild(_ViewsFaultMixin, _TagRouteHarness):
    """_heal_timeline_views (a /clear or revive inherits its session's tag memberships) runs inside _ordered,
    inside every build. It reads PROVED once and hands its snapshot to the setter; a fault raises to
    _ordered's boundary, which places the fork, defers the heal and retries it on later passes -- the order
    publish that follows the splice marks the fork known, so without the retry a heal skipped over a
    transient fault never ran again. On main the heal read the display reader, folded to {}, and
    silently carried nothing."""

    FORK = "33333333-2222-3333-4444-555555555555"

    def setUp(self):
        super().setUp()
        self._saved_heal = (km._session_order_proved, km._session_order, km._name_of, km._heal_timeline_views)
        km._session_order_lkg[0] = None

    def tearDown(self):
        (km._session_order_proved, km._session_order, km._name_of, km._heal_timeline_views) = self._saved_heal
        super().tearDown()

    def _seed_tag_with(self, sid):
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "t1", "name": "workers", "color": "#123456",
                                                            "members": [sid]}]})
        km._flags_cache.clear()

    def _stub_order(self, order):
        km._session_order_proved = lambda: list(order)
        km._session_order = lambda: list(order)
        km._name_of = lambda sid: {SID: "web", SID2: "api", self.FORK: "web"}.get(sid, "")

    def _sessions(self):
        return [{"sid": SID2, "name": "api"}, {"sid": self.FORK, "name": "web"}, {"sid": SID, "name": "web"}]

    def test_the_heal_writes_off_its_one_snapshot_even_when_a_second_read_would_fault(self):
        # the fault begins AFTER the heal's read: a setter that re-read the store for its base would raise;
        # the heal hands its snapshot over and the membership lands off ONE proved read
        self._seed_tag_with(SID)
        real = km._timeline_views_proved
        calls = [0]

        def once_then_fault():
            calls[0] += 1
            if calls[0] > 1:
                raise km._StateUnreadable(km._views_path(), "read failed: [Errno 5] injected EIO")
            return real()
        km._timeline_views_proved = once_then_fault
        try:
            km._heal_timeline_views(SID, SID2)
        finally:
            km._timeline_views_proved = real
        km._flags_cache.clear()
        members = [t for t in real()["tags"] if t["name"] == "workers"][0]["members"]
        self.assertEqual(sorted(m["sid"] for m in members), sorted([SID, SID2]), "the fork inherited the tag")
        self.assertEqual(calls[0], 1, "exactly one proved read: the setter did not re-read")

    def test_ordered_survives_a_heal_that_raises_and_still_places_the_fork(self):
        self._stub_order([SID, SID2])

        def boom(old, new):
            raise km._StateUnreadable(km._views_path(), "read failed: [Errno 5] injected EIO")
        km._heal_timeline_views = boom
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = [s["sid"] for s in km._ordered(self._sessions())]
        self.assertEqual(out, [SID, self.FORK, SID2], "the fork slots after its same-name sibling despite the failed heal")
        self.assertIn("views heal for %s skipped" % self.FORK, err.getvalue())
        self.assertIn("could not be read", err.getvalue())
        self.assertEqual(km._views_heal_pending, {self.FORK: SID}, "deferred, to be retried")

    def test_ordered_survives_a_heal_whose_publish_fails_and_still_places_the_fork(self):
        # the write-step twin, through the REAL heal: the store reads, the fork's membership is computed, the
        # PUBLISH fails. On main that OSError left _ordered and reached every build that runs it.
        self._seed_tag_with(SID)
        self._stub_order([SID, SID2])
        p = km._views_path()
        before = p.read_bytes()
        err = io.StringIO()
        with _writes_fault(p), contextlib.redirect_stderr(err):
            out = [s["sid"] for s in km._ordered(self._sessions())]
        self.assertEqual(out, [SID, self.FORK, SID2])
        self.assertIn("views heal for %s skipped" % self.FORK, err.getvalue())
        self.assertIn("could not be written", err.getvalue())
        self.assertEqual(p.read_bytes(), before, "the views file is untouched")

    def test_a_deferred_heal_lands_on_a_later_pass_once_the_store_reads(self):
        self._seed_tag_with(SID)
        self._stub_order([SID, SID2])
        p = km._views_path()
        err = io.StringIO()
        with _reads_fault(p), contextlib.redirect_stderr(err):
            out1 = [s["sid"] for s in km._ordered(self._sessions())]
        self.assertEqual(out1, [SID, self.FORK, SID2])
        self.assertIn("views heal for %s skipped" % self.FORK, err.getvalue())
        self.assertEqual(km._views_heal_pending, {self.FORK: SID})
        self.assertIn(self.FORK, json.loads((km.jd.STATE / "session-order.json").read_text()),
                      "the order persisted with the fork in it: without the retry the heal would never run again")
        self._stub_order([SID, self.FORK, SID2])                          # the fork is known now; the store reads again
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            out2 = [s["sid"] for s in km._ordered(self._sessions())]
        self.assertEqual(out2, [SID, self.FORK, SID2])
        self.assertEqual(km._views_heal_pending, {}, "landed")
        self.assertIn("views heal for %s landed on retry" % self.FORK, err2.getvalue())
        km._flags_cache.clear()
        members = [t for t in km._timeline_views_proved()["tags"] if t["name"] == "workers"][0]["members"]
        self.assertIn(self.FORK, [m["sid"] for m in members], "the /clear'd session did not fall out of its tag")

    def test_a_build_completes_when_the_store_faults_under_the_heal(self):
        # end to end through the build: _ordered detects the same-name fork inside build_feed and runs the heal,
        # whose proved read faults. The build returns a payload, the fault row is filed, the file is untouched.
        self._seed_tag_with(SID)
        self._stub_order([SID, SID2])
        p = km._views_path()
        before = p.read_bytes()
        saved_alive = km._alive_sessions
        km._alive_sessions = lambda now, tmux: [{"sid": SID2, "name": "api", "path": "/api", "mtime": now - 5},
                                                {"sid": self.FORK, "name": "web", "path": "/web", "mtime": now - 1},
                                                {"sid": SID, "name": "web", "path": "/web", "mtime": now - 9}]
        err = io.StringIO()
        try:
            with _reads_fault(p), contextlib.redirect_stderr(err):
                feed = km.build_feed(int(time.time()))
        finally:
            km._alive_sessions = saved_alive
        self.assertEqual(feed.get("type"), "feed", "the build proceeded: nothing raised out of the heal")
        self.assertIn("views heal for %s skipped" % self.FORK, err.getvalue())
        self.assertEqual(p.read_bytes(), before)
        # the belt behind the braces: were a store fault ever to escape a handler, the recv loop's socket-failure
        # arm is (BrokenPipeError, ConnectionResetError, OSError) -- a plain Exception is logged, the socket kept
        self.assertFalse(issubclass(km._StateUnreadable, OSError))

    def _heal_rows(self):
        """The deferred heal's bell rows (text, kind): what the dashboard hears of a /clear'd session that sits
        outside its tag while the store faults."""
        return [(t, k) for t, ok, k in self.notices if not ok and "did not carry its tags" in t]

    def test_a_deferred_heal_rings_the_bell_once_and_the_retry_stays_quiet_while_the_store_still_faults(self):
        # review find, 2026-09-08: the deferral was a stderr line alone, which dies with the process while the
        # session sits outside its tag; the fork and promotion sites file a bell row for the same non-event. Now
        # the boundary files one too, under the refused kind, naming the session -- ONCE: the two quiet arms of
        # the retry (the fork re-detected as new while its heal is pending, as after a failed order publish; and
        # the pending heal's retry while the store still faults) add no line and no row
        self._seed_tag_with(SID)
        self._stub_order([SID, SID2])
        p = km._views_path()
        before = p.read_bytes()
        err = io.StringIO()
        with _reads_fault(p), contextlib.redirect_stderr(err):
            km._ordered(self._sessions())                                  # pass 1: deferred, said once
            km._ordered(self._sessions())                                  # pass 2: the order still lacks the fork, heal pending
            self._stub_order([SID, self.FORK, SID2])
            km._ordered(self._sessions())                                  # pass 3: the retry, the store still faulting
        self.assertEqual(err.getvalue().count("views heal for %s skipped" % self.FORK), 1, "the skip line once per deferral")
        self.assertNotIn("landed", err.getvalue())
        rows = self._heal_rows()
        self.assertEqual([k for t, k in rows], ["refused"], "ONE bell row, under the refused kind, like the spawn sites: %r" % rows)
        self.assertIn('"web" did not carry its tags across a /clear or revive: the tag store could not be read (read failed: [Errno 5]',
                      rows[0][0])
        self.assertIn("tag it again if the kernel restarts first", rows[0][0], "the row says what a restart costs")
        self.assertEqual(km._views_heal_pending, {self.FORK: SID}, "still pending: the next pass retries it")
        self.assertEqual(p.read_bytes(), before, "nothing written under the fault")

    def test_a_retried_heal_with_nothing_to_carry_settles_and_says_which(self):
        # the landing arm's other branch: the session held no tag, so the retry carries nothing and writes nothing,
        # and the deferral is over -- the map entry is popped (without that the retry would run on every pass)
        # and the bell keeps the deferral's one row
        self._seed_tag_with(SID2)                                          # a store that exists; "web" is in no tag
        self._stub_order([SID, SID2])
        p = km._views_path()
        with _reads_fault(p), contextlib.redirect_stderr(io.StringIO()):
            km._ordered(self._sessions())
        self.assertEqual(km._views_heal_pending, {self.FORK: SID})
        self.assertEqual(len(self._heal_rows()), 1, "the deferral rang once")
        before = p.read_bytes()
        self._stub_order([SID, self.FORK, SID2])
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            km._ordered(self._sessions())
        self.assertEqual(km._views_heal_pending, {}, "settled: nothing to carry is an answer, not a fault")
        self.assertIn("views heal for %s retried: nothing to carry" % self.FORK, err2.getvalue())
        self.assertEqual(p.read_bytes(), before, "no write: a heal with nothing to carry publishes nothing")
        self.assertEqual(len(self._heal_rows()), 1, "the landing adds no row: the bell said what did not happen, once")


class TagAckNamesTheFault(_ViewsFaultMixin, _TagRouteHarness):
    """The creation event's tag half (`romp new --in`, the picker's Tags row, a fork, a promoted thread):
    nothing is inherited or joined off a faulting store, and what did not happen is said -- in the ack's
    `tagError`, or at the spawn sites in a notice naming the child."""

    def test_romp_new_in_a_tag_under_a_read_fault_creates_nothing_and_names_the_fault(self):
        # on main _edit_tag read the poisoned {} and CREATED "workers" anew holding only the new session -- a
        # one-member tag over the user's set -- with tagsApplied ["workers", "reviewers"] and no tagError
        before = self._seed("workers", "reviewers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            ack = km._tag_ack(DEAD, "", ["workers", "reviewers"])
        self.assertEqual(ack["tagsRequested"], ["workers", "reviewers"])
        self.assertEqual(ack["tagsApplied"], [None, None], "no tag was joined, positionally")
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", ack.get("tagError", ""))
        self.assertEqual(self._bytes(), before)

    def test_a_child_does_not_inherit_under_a_fault_and_the_ack_says_so(self):
        self._seed("workers")                                    # holds "web" (SID), the parent
        before = self._bytes()
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            ack = km._tag_ack(DEAD, SID, [])
        self.assertIn("the tag store could not be read", ack.get("tagError", ""), "what did not happen is said")
        self.assertEqual(ack["tags"], [])
        self.assertEqual(self._bytes(), before)

    def test_inherit_raises_under_a_fault_so_no_spawn_reads_an_empty_parent(self):
        self._seed("workers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()):
            with self.assertRaises(km._StateUnreadable):
                km._inherit_tag_membership(SID, DEAD)
        km._flags_cache.clear()
        self.assertEqual(km._inherit_tag_membership(SID, DEAD), ["workers"], "…and lands once the store reads")

    def test_a_spawn_site_says_what_did_not_happen(self):
        # the fork and thread-promotion sites: the session exists by then, so the spawn stands and the child is
        # named in one refused-kind notice (the store's own episode notice cannot say which session landed
        # outside its group)
        e = km._StateUnreadable(km._views_path(), "read failed: [Errno 5] Input/output error")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._note_tags_not_inherited(SID, "web-2", e)
        rows = [(t, k) for t, ok, k in self.notices if not ok]
        self.assertEqual(len(rows), 1)
        self.assertIn('"web-2" did not inherit the tags of', rows[0][0])
        self.assertIn("the tag store could not be read (read failed: [Errno 5] Input/output error)", rows[0][0])
        self.assertEqual(rows[0][1], "refused")
        self.assertIn("did not inherit", err.getvalue())


class LegacyHiddenEntriesSurviveAProvedRead(_ViewsFaultMixin, _TagRouteHarness):
    """The display reader migrates a legacy blob's `hidden` entries into the archived tag on first read and
    persists that; the normalizer DROPS `hidden`. A mutation snapshot that read the file raw would hand a
    writer a blob without the legacy entries, and its write -- before the display reader's first read
    after boot -- would lose every one of them. The proved reader replays the same idempotent migration
    without persisting it; the writer's write carries it."""

    LEGACY = {"active": "all", "tags": [{"id": "t1", "name": "workers", "color": "#123456", "members": [SID]}],
              "hidden": ["44444444-2222-3333-4444-555555555555", "55555555-2222-3333-4444-555555555555"]}

    def test_the_proved_snapshot_carries_the_hidden_entries_as_archived_members(self):
        (km.jd.STATE / "timeline-views.json").write_text(json.dumps(self.LEGACY))
        km._flags_cache.clear()
        v = km._timeline_views_proved()
        arch = [t for t in v["tags"] if t["name"] == "archived"]
        self.assertEqual(len(arch), 1, "the migration is replayed on the mutation snapshot too")
        self.assertEqual(sorted(m["sid"] for m in arch[0]["members"]), sorted(self.LEGACY["hidden"]))
        self.assertNotIn("hidden", v)
        self.assertEqual(json.loads((km.jd.STATE / "timeline-views.json").read_text()), self.LEGACY,
                         "and it did NOT persist: the display reader owns that write")

    def test_an_edit_before_the_first_display_read_keeps_the_legacy_entries(self):
        # a guard (green on main too, where the display reader migrated and persisted before the edit): it pins
        # the proved reader's replay, which a snapshot reading the file raw would fail
        (km.jd.STATE / "timeline-views.json").write_text(json.dumps(self.LEGACY))
        km._flags_cache.clear()
        st, resp = self._post({"name": "workers", "add": ["api"]})   # an edit through the door, before any display read
        self.assertTrue(resp.get("ok"), resp)
        stored = json.loads((km.jd.STATE / "timeline-views.json").read_text())
        arch = [t for t in stored["tags"] if t["name"] == "archived"]
        self.assertEqual(len(arch), 1, "the edit's write carried the migrated archived tag")
        self.assertEqual(sorted(m["sid"] for m in arch[0]["members"]), sorted(self.LEGACY["hidden"]),
                         "the legacy hidden entries survived the edit (the normalizer would have dropped `hidden`)")
        self.assertNotIn("hidden", stored)
        self.assertTrue(all(t.get("mtime") for t in stored["tags"]), "every tag stamped, as the display reader's re-stamp would")
        km._flags_cache.clear()
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["archived", "workers"])

    def test_the_display_reader_still_persists_the_migration_once(self):
        # a guard (green on main too): the display reader's write is unchanged by the proved reader
        (km.jd.STATE / "timeline-views.json").write_text(json.dumps(self.LEGACY))
        km._flags_cache.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            v = km._timeline_views()
        self.assertEqual(sorted(t["name"] for t in v["tags"]), ["archived", "workers"])
        stored = json.loads((km.jd.STATE / "timeline-views.json").read_text())
        self.assertNotIn("hidden", stored, "persisted: the next read has nothing to migrate")
        self.assertEqual(sorted(t["name"] for t in stored["tags"]), ["archived", "workers"])


class ReaderRestampIsHousekeeping(_ViewsFaultMixin, _TagRouteHarness):
    """The reader's re-stamp on read writes through the state files' door with note=False: a publish that
    fails there is the reader's own once-per-error log line, never a notice and never a write-fault
    episode -- the first GESTURE that fails is what the user hears about. test_tag_edit_ack.py's
    ReaderRestampUnwritable pins the log line; its `notices == []` cannot see a notice filed with a `kind`
    (its stub takes none, and _note_state_fault swallows the TypeError), so this one observes the flag."""

    def test_the_re_stamps_failed_publish_is_a_log_line_not_a_notice_and_the_first_gesture_is(self):
        p = km._views_path()
        legacy = {"active": "all", "tags": [{"id": "gL", "name": "legacy", "color": "",
                                              "members": [{"host": "", "sid": SID}]}]}   # seq-less: the first read stamps it
        p.write_text(json.dumps(legacy))
        km._flags_cache.clear()
        err = io.StringIO()
        with _writes_fault(p), contextlib.redirect_stderr(err):
            v = km._timeline_views()
            self.assertEqual([t["name"] for t in v["tags"]], ["legacy"], "served as read, unstamped")
            self.assertNotIn("seq", v)
            lines = [ln for ln in err.getvalue().splitlines() if "could not be re-stamped" in ln]
            self.assertEqual(len(lines), 1, "the reader's own once-per-error line")
            self.assertIn("OSError", lines[0])
            self.assertIn("[Errno 28]", lines[0])
            self.assertEqual(self.notices, [], "housekeeping: no notice of any kind")
            self.assertNotIn(str(p), km._state_write_fault_seen, "no write-fault episode opened by the re-stamp")
            self.assertEqual(p.read_text(), json.dumps(legacy), "the file is untouched")
            # the first GESTURE that fails on the same full disk IS the notice, and opens the episode
            st, r = self._post({"name": "legacy", "add": ["api"]})
            self.assertEqual((st, r["ok"], r["retryable"]), (200, False, True))
            self.assertEqual(len(self._faults("written")), 1)
            self.assertIn(str(p), km._state_write_fault_seen)


class ForeignWriteBehindIsJudgedOnTheProvedRead(_ViewsFaultMixin, _TagRouteHarness):
    """A file written OUTSIDE the kernel (the timeline's Electron/Obsidian branch writes
    timeline-views.json itself) from an older copy, in the window before the pusher's next display read:
    the display reader judges it against the last served blob and re-stamps it (ForeignWriteJudged in
    test_tag_edit_ack.py). The proved reader must yield that SAME judged snapshot -- or every RMW door
    diffs its one edit against the foreign copy, no refusal anywhere, and stamps it past everything
    served, so every dashboard adopts the blessed stale copy."""

    def test_an_edit_in_the_window_after_a_foreign_write_builds_on_the_judged_store_not_the_foreign_copy(self):
        self._seed("workers")
        km._flags_cache.clear()
        older = json.loads(json.dumps(km._timeline_views()))          # a panel's copy: workers only, at T0, seq N1
        with _clock_ahead(2):                                          # the stamps are whole seconds: what follows lands in a later second
            self._seed("reviewers")                                    # created since; the cache holds both at seq N2
            served = km._timeline_views()
            self.assertEqual(sorted(t["name"] for t in served["tags"]), ["reviewers", "workers"])
            self.assertGreater(served["seq"], older["seq"])
            p = km._views_path()
            km._atomic_write(p, json.dumps(older))                     # the panel writes its older copy: seq behind, no cache clear
            st, resp = self._post({"name": "workers", "color": "#123456"})   # ONE edit, no fault, inside the window
            self.assertTrue(resp.get("ok"), resp)
            stored = json.loads(p.read_text())
            self.assertEqual(sorted(t["name"] for t in stored["tags"]), ["reviewers", "workers"],
                             "the store carries the last-served content plus the one edit: the tag the foreign copy lacked is back")
            self.assertEqual(next(t for t in stored["tags"] if t["name"] == "workers")["color"], "#123456", "the edit landed")
            self.assertGreater(stored["seq"], served["seq"], "ordered past everything served")
            self.assertTrue(any("outside the kernel" in t for t, ok, k in self.notices), "the foreign copy's refusal is said")
            km._flags_cache.clear()
            self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["reviewers", "workers"])


class AFaultWithNothingGoodToShowSaysSo(_ViewsFaultMixin, _TagRouteHarness):
    """The frames and the acks under a READ fault (review find, 2026-09-08). The display reader serves the
    last blob this kernel served, UNPROVED, and every carrier -- the tabOrder frame, the feed, the timeline
    skeleton, every write's ack -- embedded it as the store's word; with a COLD cache (a kernel that had never
    served the store) they embedded the seq-less empty default, which every dashboard adopts: the tag bar
    emptied with the fault said only on the bell, and a poster reverting on a refusal took that emptiness as
    its base. Now _views_payload builds every carrier from ONE display read: a blob rides MARKED
    (`viewsFault`, the fault in the person's words) when there is one, and with none the carrier holds the
    marker alone and no `views` key, which every pane reads as "keep what you hold". A clean read adds no
    key, so a clean frame and a clean ack are what they were."""

    LENS = ViewsWsRefusal.LENS

    def setUp(self):
        super().setUp()
        self._saved_alive = km._alive_sessions
        km._alive_sessions = lambda now, tmux: []                          # build_feed with no sessions: the frame's views half is the subject

    def tearDown(self):
        km._alive_sessions = self._saved_alive
        super().tearDown()

    def test_a_cold_cache_fault_puts_the_marker_alone_on_every_frame_and_no_empty_store(self):
        self._seed("workers", "reviewers")
        km._flags_cache.clear()                                            # a kernel that never served the store
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            frame = km._tab_order_frame([SID], [{"id": SID}], {SID})
            feed = km.build_feed(int(time.time()))
            payload = km._views_payload()
        self.assertEqual((frame["type"], feed["type"]), ("tabOrder", "feed"), "the build proceeded")
        for f in (frame, feed, payload):
            self.assertNotIn("views", f, "no blob: a seq-less empty default here is adopted by every dashboard as the store's word")
            self.assertIn("the tag store could not be read (read failed: [Errno 5]", f["viewsFault"])
        self.assertEqual(frame["live"], [SID], "the rest of the frame is whole")
        self.assertEqual(len(self._faults()), 1, "the once-per-episode notice still rides the bell")

    def test_a_warm_cache_fault_carries_the_last_served_blob_and_marks_it(self):
        self._seed("workers", "reviewers")                                  # the writes primed the cache
        good = km._timeline_views()
        p = km._views_path()
        p.write_bytes(p.read_bytes() + b"\n")                               # the file moved under the cache (a write outside the
        #                                                                     kernel): the next display read must read it, and cannot
        with _reads_fault(p), contextlib.redirect_stderr(io.StringIO()):
            frame = km._tab_order_frame([SID], [{"id": SID}], {SID})
        self.assertEqual(sorted(t["name"] for t in frame["views"]["tags"]), ["reviewers", "workers"])
        self.assertEqual(frame["views"]["seq"], good["seq"], "the last blob served, at its seq: no dashboard moves")
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", frame["viewsFault"], "and it is said to be unproved")
        with _stat_fault(p), contextlib.redirect_stderr(io.StringIO()):      # the stat-fault arm: the same answer
            frame2 = km._tab_order_frame([SID], [{"id": SID}], {SID})
        self.assertEqual(frame2["views"], frame["views"])
        self.assertIn("the tag store could not be read (stat failed:", frame2["viewsFault"])

    def test_a_clean_read_adds_no_marker_so_a_clean_frame_is_what_it_was(self):
        self._seed("workers")
        frame = km._tab_order_frame([SID], [{"id": SID}], {SID})
        self.assertNotIn("viewsFault", frame)
        self.assertEqual([t["name"] for t in frame["views"]["tags"]], ["workers"])
        self.assertEqual(set(km._views_payload()), {"views"})

    def test_a_refusal_ack_on_a_cold_cache_carries_the_marker_and_no_blob(self):
        before = self._seed("workers", "reviewers")
        km._flags_cache.clear()
        client, sent = self._client()
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            self._ws({"type": "setTimelineViews", "writeId": "w1", "edited": [], "views": dict(self.LENS)}, client)
        ack = sent[0]
        self.assertEqual((ack["type"], ack["ok"]), ("viewsAck", False))
        self.assertNotIn("views", ack, "a poster reverting on this refusal keeps what it holds")
        self.assertIsNone(ack["seq"])
        self.assertIn("the tag store could not be read", ack["viewsFault"])
        self.assertIn("nothing was changed", ack["error"])
        self.assertEqual(self._bytes(), before)

    def test_a_refusal_ack_on_a_warm_cache_carries_the_last_served_blob_marked_and_a_clean_ack_no_marker(self):
        self._seed("workers", "reviewers")
        good = km._timeline_views()
        client, sent = self._client()
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):   # a warm cache: the file's key cannot be checked
            self._ws({"type": "tagEdit", "writeId": "w2", "edit": {"op": "create", "name": "api", "sids": [SID2]}}, client)
        ack = sent[0]
        self.assertEqual((ack["type"], ack["ok"]), ("tagEditAck", False))
        self.assertEqual(sorted(t["name"] for t in ack["views"]["tags"]), ["reviewers", "workers"])
        self.assertEqual(ack["seq"], good["seq"], "the ack's seq is the blob's, the last one served")
        self.assertIn("the tag store could not be read (stat failed:", ack["viewsFault"])
        self.assertIn("nothing was changed", ack["error"])
        client2, sent2 = self._client()
        self._ws({"type": "setTimelineViews", "writeId": "w3", "edited": [], "views": dict(self.LENS)}, client2)
        clean = sent2[0]
        self.assertTrue(clean["ok"], clean)
        self.assertNotIn("viewsFault", clean, "a proved read carries no marker")
        self.assertEqual(clean["seq"], clean["views"]["seq"])


class GetViewsSaysSoToo(_ViewsFaultMixin, _TagRouteHarness):
    """GET /views under a READ fault: the route is the read half of `romp tag` and the poll source of tag
    federation, and it still served the display read's dict half -- under a cold-cache fault the seq-less empty
    default, under a 200 -- after every frame and ack had learned to say so (the 2026-09-08 review). A polling
    peer stores any 200 dict as this host's tags (_poll_remote_views) and keeps its last reading only on a
    non-200, so for the fault's duration every peer erased this host's tags in its own view, with nothing said
    there. Now the route answers like the frames: a last-good blob rides marked `viewsFault`, and nothing good
    is a retryable 503 the peer keeps its last reading through. The peer here is the harness's own server,
    dialled by the real poller, so the route is exercised as a peer sees it."""

    def _get_views(self):
        """GET /views as a script or a polling peer sees it: (status, body), a non-200 included."""
        req = urllib.request.Request("http://127.0.0.1:%d/views" % self.port,
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def _peer_row(self, reading):
        """A polling peer's row for THIS kernel, holding `reading` as its last one. No `_views_at`, so the
        first poll dials; a case that polls again pops the stamp itself (the rate gate is not the subject)."""
        return {"host": "TESTHOST", "local_port": self.port, "token": os.environ["ROMP_SERVE_TOKEN"], "views": reading}

    def test_a_cold_cache_fault_is_a_retryable_503_and_a_polling_peer_keeps_its_last_reading(self):
        self._seed("workers")
        marks = len(self.dirty)                                            # the seed's own mark
        st, good = self._get_views()
        self.assertEqual((st, [t["name"] for t in good["tags"]]), (200, ["workers"]))
        self.assertNotIn("viewsFault", good, "a clean read carries no marker: the route's body is what it was")
        peer = self._peer_row(good)
        km._flags_cache.clear()                                            # a kernel that never served the store
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            st, body = self._get_views()
            self.assertEqual(st, 503, "nothing good to show is a refusal, never the empty default under a 200")
            self.assertEqual((body["ok"], body["retryable"]), (False, True))
            self.assertIn("the tag store could not be read (read failed: [Errno 5]", body["error"])
            self.assertTrue(body["error"].endswith("retry"), body["error"])
            self.assertNotIn("timeline-views.json", body["error"], "the person's words; the notice names the file")
            self.assertNotIn("tags", body, "no tag list a reader could take for the store")
            self.assertIs(km._poll_remote_views(peer), good, "the peer keeps its last reading on the non-200, as on any blip")
            self.assertFalse(km._cache_remote_views(peer, km._poll_remote_views(peer)),
                             "...and stores nothing new: this host's tags stand on every one of the peer's dashboards")
            self.assertEqual(len(self.dirty), marks + 1,
                             "one repaint, for the stale marker's arrival on the peer's row (APeerShowsAFaultingHostsTagsAsStale); "
                             "the kept reading itself moved nothing, and the second poll of the same fault marked nothing")
            self.assertEqual(len(self._faults()), 1, "said once for the episode, however many GETs and polls")
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):      # the stat arm on a cold cache: the same answer
            st, body = self._get_views()
            self.assertEqual(st, 503)
            self.assertIn("the tag store could not be read (stat failed:", body["error"])
        st, v = self._get_views()                                          # the disk heals: the file's own read
        self.assertEqual((st, [t["name"] for t in v["tags"]], v["seq"]), (200, ["workers"], good["seq"]))
        self.assertNotIn("viewsFault", v)
        peer.pop("_views_at", None)
        self.assertFalse(km._cache_remote_views(peer, km._poll_remote_views(peer)),
                         "the healed reading is the one the peer kept: no change on the peer either side of the fault")

    def test_a_primed_cache_fault_serves_the_last_good_blob_marked_and_a_peer_stores_the_marker_once(self):
        self._seed("workers", "reviewers")                                  # the writes primed the cache
        st, good = self._get_views()
        peer = self._peer_row(good)
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):   # a warm cache: the file's key cannot be checked
            st, v = self._get_views()
            self.assertEqual(st, 200, "a last-good blob is served: a peer with no reading yet gets this host's tags")
            self.assertEqual((sorted(t["name"] for t in v["tags"]), v["seq"]), (["reviewers", "workers"], good["seq"]))
            self.assertIn("the tag store could not be read (stat failed:", v["viewsFault"], "and it is said to be unproved")
            self.assertEqual({k: x for k, x in v.items() if k != "viewsFault"}, good, "the marker is the only difference")
            self.assertEqual(v.get("tags") or v.get("groups"), good["tags"], "the bare `romp tag` listing reads the tags and ignores the key")
            marked = km._poll_remote_views(peer)
            self.assertEqual(marked, v, "the peer takes the 200 as this host's reading, marker and all")
            self.assertTrue(km._cache_remote_views(peer, marked), "the marker's arrival is one change on the peer")
            peer.pop("_views_at", None)
            self.assertFalse(km._cache_remote_views(peer, km._poll_remote_views(peer)),
                             "...and a later poll of the same fault is none: nothing rebuilds per pass")
            self.assertEqual(len(self._faults()), 1)
        st, v2 = self._get_views()                                          # the disk heals
        self.assertEqual((st, v2), (200, good), "unmarked again")
        peer.pop("_views_at", None)
        self.assertTrue(km._cache_remote_views(peer, km._poll_remote_views(peer)), "the marker's departure is the other change")
        self.assertEqual(peer["views"], good)


class TheHealRepaintsAtOnce(_ViewsFaultMixin, _TagRouteHarness):
    """The pusher caches the feed and the timeline on a signature (_fleet_view_sig) the store's HEAL does not
    move: the fault's start does (its once-per-episode notice bumps the sync-notice count, a signature input),
    so the payloads were rebuilt under `viewsFault` -- and then stood, marker and all (on a cold cache with no
    blob at all), until the clock bucket or an unrelated change rebuilt them. The read that ends an episode now
    marks the views dirty (_views_read_clean), exactly once per episode end, so the rebuild rides the heal."""

    def setUp(self):
        super().setUp()
        self._saved_alive = km._alive_sessions
        km._alive_sessions = lambda now, tmux: []                          # build_feed with no sessions: the views half is the subject

    def tearDown(self):
        km._alive_sessions = self._saved_alive
        super().tearDown()

    def test_the_clean_read_that_ends_an_episode_marks_the_views_dirty_once(self):
        self._seed("workers")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()                                           # a cold-cache episode opens
        self.assertIn(str(km._views_path()), km._state_fault_seen)
        n = len(self.dirty)
        km._timeline_views()                                               # the heal: the episode ends on this read
        self.assertEqual(len(self.dirty), n + 1, "one mark for the heal")
        self.assertNotIn(str(km._views_path()), km._state_fault_seen)
        km._timeline_views()                                               # a hit: no episode to end
        km._flags_cache.clear()
        km._timeline_views()                                               # a clean miss: none either
        self.assertEqual(len(self.dirty), n + 1, "a clean read with no episode open marks nothing")
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()                                           # a PRIMED-cache episode: the last blob served, marked
        self.assertIn(str(km._views_path()), km._state_fault_seen)
        km._timeline_views()
        self.assertEqual(len(self.dirty), n + 2, "one mark per episode end, primed or cold")
        km._flags_cache.clear()
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()
        km._views_path().unlink()                                          # the store goes MISSING under the fault
        self.assertEqual(km._timeline_views()["tags"], [], "empty is the truth now")
        self.assertEqual(len(self.dirty), n + 3, "the missing-store read ends the episode too, and marks once")

    def test_a_feed_cached_under_the_marker_is_rebuilt_on_the_heal_not_the_next_signature_change(self):
        # the pusher's real cache on ONE unchanging signature: without the mark that signature serves the marked
        # payload until something unrelated moves it (the clock bucket, a transcript, a notice)
        self._seed("workers")
        seq = km._timeline_views()["seq"]
        km._mark_views_dirty = self._saved[2]                              # the real mark: the cache it moves is the subject
        saved = (list(km._built_feed), km._views_dirty[0])
        try:
            km._flags_cache.clear()
            now, sig = int(time.time()), ("one signature",)
            with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
                feed = km._cached_feed(now, {}, sig)
                self.assertNotIn("views", feed)
                self.assertIn("the tag store could not be read", feed["viewsFault"])
                self.assertIs(km._cached_feed(now, {}, sig), feed, "the same signature serves the cached payload, marker and all")
            frame = km._tab_order_frame([SID], [{"id": SID}], {SID})        # the disk healed: the pusher's tabs-first read ends the episode
            self.assertEqual((frame["views"]["seq"], "viewsFault" in frame), (seq, False))
            healed = km._cached_feed(now, {}, sig)
            self.assertIsNot(healed, feed, "the heal marked the views dirty: the same signature rebuilds")
            self.assertNotIn("viewsFault", healed)
            self.assertEqual(([t["name"] for t in healed["views"]["tags"]], healed["views"]["seq"]), (["workers"], seq))
            self.assertIs(km._cached_feed(now, {}, sig), healed, "and one rebuild is all: the next serve is the cache")
        finally:
            km._built_feed[:] = saved[0]
            km._views_dirty[0] = saved[1]


class APeerShowsAFaultingHostsTagsAsStale(_ViewsFaultMixin, _TagRouteHarness):
    """The peer half of the route's fault contract (review find, 2026-09-09, on #1096). GetViewsSaysSoToo proves
    the poller keeps its last reading through the 503 and stores the marked blob; but the peer's ROW said
    nothing about either, so on the peer a faulting host was indistinguishable from a healthy one, and
    _apply_pending_tag_edits took the kept reading for a fresh one: a journaled delete or rename found its
    tag, forwarded, and retired on the host's fault refusal while the host's store never took it. Now the row
    wears the host's fault as `viewsFault` from the 503's body or the blob's marker until a clean 200 sheds it
    (the read that ends the episode, no timer), _views_client stamps every rendered tag of that host with it,
    and the apply stands down on a marked row. The host here is the harness's own server, dialled by the real
    poller and the real forward; the row is registered as an attached host, since the journal and the renderer
    both read the registry."""

    def setUp(self):
        super().setUp()
        self._peer_saved = (dict(km._remotes), km._remote_forward)
        self._fresh_journal()
        km._remotes.clear()
        self.peer = {"host": "TESTHOST", "status": "up", "local_port": self.port,
                     "token": os.environ["ROMP_SERVE_TOKEN"]}
        km._remotes["TESTHOST"] = self.peer

    def tearDown(self):
        km._remotes.clear(); km._remotes.update(self._peer_saved[0])
        km._remote_forward = self._peer_saved[1]
        self._fresh_journal()
        super().tearDown()

    def _fresh_journal(self):
        """The pending-edit journal (file + module cache) starts and ends empty: the cache is module state, and
        the harness swaps the state dir per test."""
        try:
            km._pending_tag_path().unlink()
        except OSError:
            pass
        with km._PENDING_TAG_LOCK:
            km._PENDING_TAG_CACHE["rows"] = None

    def _poll(self):
        """One real dial of the host's /views, the rate gate dropped (the gate is not the subject)."""
        self.peer.pop("_views_at", None)
        return km._poll_remote_views(self.peer)

    def _host_rows(self):
        """The host's tags as the peer's frames render them (_views_client's remoteTags rows for this host)."""
        return [t for t in km._views_client().get("remoteTags") or [] if t.get("host") == "TESTHOST"]

    def test_the_row_wears_the_hosts_fault_from_the_503_and_sheds_it_on_the_clean_read(self):
        self._seed("workers")
        st, good = self._views()
        self.assertTrue(km._cache_remote_views(self.peer, self._poll()), "the clean reading lands on the row")
        kept = self.peer["views"]
        self.assertEqual(kept, good)
        self.assertNotIn("viewsFault", self.peer, "a proved reading wears no marker")
        self.assertEqual([("viewsFault" in t) for t in self._host_rows()], [False], "...and renders fresh")
        marks = len(self.dirty)
        km._flags_cache.clear()                                            # a kernel that never served the store
        err = io.StringIO()
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(err):
            self.assertIs(self._poll(), kept, "the 503: the last reading is kept, as the route's contract says")
            self.assertIn("the tag store could not be read (read failed: [Errno 5]", self.peer["viewsFault"],
                          "...and the row says the host could not vouch for it")
            self.assertFalse(self.peer["viewsFault"].endswith("retry"), "the host's fault, not its advice to the poller")
            self.assertEqual(len(self.dirty), marks + 1, "the marker's arrival is one repaint on the peer")
            self.assertEqual([t["viewsFault"] for t in self._host_rows()], [self.peer["viewsFault"]],
                             "the host's rendered tags wear it: stale, not fresh")
            self.assertIs(self._poll(), kept)
            self.assertEqual(len(self.dirty), marks + 1, "a second poll of the same fault repaints nothing")
            self.assertEqual(len(self._faults()), 1, "the host's own notice is filed once; the peer files none of its own")
        self.assertEqual(err.getvalue().count("showing its last-known tags"), 1, "said once for the episode in the log")
        st, healed = self._views()                                         # the disk heals: the host's own read ends ITS episode
        self.assertEqual((st, healed), (200, good))                        # (and marks once, TheHealRepaintsAtOnce's subject: this kernel is the host too)
        n = len(self.dirty)
        self.assertEqual(self._poll(), good, "the peer's next poll: a clean 200")
        self.assertNotIn("viewsFault", self.peer, "the clean read is the event that sheds the marker")
        self.assertEqual(len(self.dirty), n + 1, "...and its departure is the other repaint on the peer")
        self.assertEqual([("viewsFault" in t) for t in self._host_rows()], [False])
        self.assertEqual(err.getvalue().count("showing its last-known tags"), 1, "the heal adds no line")

    def test_the_row_wears_the_marked_blobs_marker_too(self):
        self._seed("workers", "reviewers")                                  # the writes primed the cache
        self.assertTrue(km._cache_remote_views(self.peer, self._poll()))
        marks = len(self.dirty)
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):   # a warm cache: the marked blob
            marked = self._poll()
            self.assertIn("the tag store could not be read (stat failed:", marked["viewsFault"])
            self.assertEqual(self.peer["viewsFault"], marked["viewsFault"], "the blob's marker is the row's")
            self.assertEqual(len(self.dirty), marks + 1, "the marker's arrival is one repaint")
            self.assertTrue(km._cache_remote_views(self.peer, marked), "the marked reading lands, as GetViewsSaysSoToo proves")
            rows = self._host_rows()
            self.assertEqual(sorted(t["name"] for t in rows), ["reviewers", "workers"])
            self.assertEqual({t["viewsFault"] for t in rows}, {marked["viewsFault"]}, "every one of the host's tags reads stale")
            self.assertEqual(self._poll(), marked)
            self.assertEqual(self.peer["viewsFault"], marked["viewsFault"], "a later poll of the same fault changes nothing")
        clean = self._poll()                                               # the disk heals
        self.assertNotIn("viewsFault", clean)
        self.assertNotIn("viewsFault", self.peer, "shed on the clean read")
        self.assertTrue(km._cache_remote_views(self.peer, clean))
        self.assertFalse(any("viewsFault" in t for t in self._host_rows()), "and the host's tags render fresh again")

    def test_a_journaled_delete_stays_pending_on_a_kept_reading_and_retires_on_the_clean_one(self):
        self._seed("workers")                                              # the writes primed the cache
        self.assertTrue(km._cache_remote_views(self.peer, self._poll()), "the host's tags, as the ruling will see them")
        self.assertTrue(km._queue_pending_tag_edit("TESTHOST", {"name": "workers", "delete": True}))
        self.assertEqual(len(km._pending_tag_rows()), 1)
        before = self._bytes()
        with _stat_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):   # a warm cache: the marked blob
            self.assertEqual(km._apply_pending_tag_edits(self.peer), 0, "a marked blob decides nothing")
            self.assertEqual(len(km._pending_tag_rows()), 1, "the row waits for a read the host can vouch for")
            self.assertEqual(self._bytes(), before, "nothing was forwarded: the host's store is as it was")
        km._flags_cache.clear()                                            # a kernel that never served the store
        with _reads_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._apply_pending_tag_edits(self.peer), 0, "the 503: the kept reading decides nothing either")
            self.assertEqual(len(km._pending_tag_rows()), 1)
            self.assertIn("the tag store could not be read (read failed: [Errno 5]", self.peer["viewsFault"])
        self.assertEqual(self._bytes(), before, "nothing was forwarded while the read faulted (read once it can be)")
        self.assertEqual(km._apply_pending_tag_edits(self.peer), 1, "the disk heals: the clean read confirms, and the delete lands")
        self.assertNotIn("viewsFault", self.peer)
        self.assertEqual(km._pending_tag_rows(), [], "retired once, on evidence")
        self.assertEqual(km._timeline_views()["tags"], [], "the host's store took it")

    def test_a_retryable_refusal_from_the_host_keeps_the_row_too(self):
        # the other disk fault on the host: its store READS but the delete's PUBLISH fails, so its /tag answers
        # ok:false + retryable (ViewsStoreUnwritableRefuses); the apply took that for the host's ruling and retired
        # the row as "refused"
        self._seed("workers")
        self.assertTrue(km._cache_remote_views(self.peer, self._poll()))
        self.assertTrue(km._queue_pending_tag_edit("TESTHOST", {"name": "workers", "delete": True}))
        before = self._bytes()
        with _writes_fault(km._views_path()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._apply_pending_tag_edits(self.peer), 0)
            self.assertEqual(len(km._pending_tag_rows()), 1, "the host said retry: the row waits, never retired as refused")
            self.assertEqual(self._bytes(), before)
            self.assertEqual(len(self._faults("written")), 1)
        self.assertEqual(km._apply_pending_tag_edits(self.peer), 1, "the disk heals: the delete lands")
        self.assertEqual(km._pending_tag_rows(), [])
        self.assertEqual(km._timeline_views()["tags"], [])


if __name__ == "__main__":
    unittest.main()
