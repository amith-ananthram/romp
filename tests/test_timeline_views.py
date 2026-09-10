#!/usr/bin/env python3
"""Session views (the user 2026-08-18; TAG model 2026-08-23): one timeline-views.json blob under
STATE — {"active", "tags"} (the hidden set retired 2026-08-24, migrated into an "archived" tag) — deciding which sessions show on the timeline lanes AND the
chat tab strip. TWO built-in sentinels: "all" — the DEFAULT (2026-08-24) — shows every session
(literally everything since 2026-08-24); "untagged" keeps the old default's meaning (a TAG marks a SPECIALIZED
session, excluded from the untagged view and shown under its tag views). "all" used to MEAN
untagged, so reinterpreting it lands every legacy blob on the new All default. A tagged
session is a BACKGROUND session: still judged and carded, surfaced by the feed, the pickers, and
the "N more" cue. The legacy "groups" key (pre-rename files and un-updated panels) reads as tags.
Local-kernel persisted (a viewer display pref, not federated). These pin the storage helpers, the
visibility decision, the normalizer + migration, the churn heal, the WS op, the payload echoes,
and the reveal rule. Synthetic only."""
import json
import os
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_tv", os.path.join(BIN, "romp-kernel"))
jd = km.jd

G1 = {"id": "g1", "name": "pool", "color": "#DD42FF", "members": ["s2", "s3"]}


class TagOrderRoundTrip(unittest.TestCase):
    """The union DISPLAY order (the user 2026-08-25): a NAME list on the views blob, viewer-side —
    so a dragged remote-homed tag holds its position without any cross-kernel write. The normalizer
    passes it through (clamped, deduped); pre-order blobs round-trip without the key."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_order_survives_normalize_and_store(self):
        blob = {"active": "all", "tags": [G1], "tagOrder": ["experiments", "pool", "remotename"]}
        n = km._norm_timeline_views(blob)
        self.assertEqual(n["tagOrder"], ["experiments", "pool", "remotename"],
                         "names pass through unvalidated — a remote-homed name is unknowable here by design")
        km._set_timeline_views(blob)
        self.assertEqual(km._timeline_views()["tagOrder"], ["experiments", "pool", "remotename"],
                         "the drag persists: the stored blob re-reads with the order intact")
        self.assertEqual(km._views_client()["tagOrder"], ["experiments", "pool", "remotename"],
                         "…and the rendered blob every client holds carries it")

    def test_absent_stays_absent_and_junk_drops(self):
        n = km._norm_timeline_views({"active": "all", "tags": [G1]})
        self.assertNotIn("tagOrder", n, "pre-order blobs round-trip without the key")
        n = km._norm_timeline_views({"tags": [G1], "tagOrder": [3, "", None, "pool", "pool"]})
        self.assertEqual(n["tagOrder"], ["pool"], "junk entries drop quietly; duplicates collapse")
        n = km._norm_timeline_views({"tags": [G1], "tagOrder": "pool"})
        self.assertNotIn("tagOrder", n, "a wrong-typed order drops whole, never raises")

    def test_lens_and_order_entries_read_on_the_stored_name_basis(self):
        """The 2026-09-05 review: tag rows were clamped AND stripped while lens
        and order entries were only clamped, so a store that already held a padded twin ("web "
        beside "web") read both rows as "web" on its first post-upgrade read while its lens and
        order still said "web " — the surface filtered to it showed no session, the tag fell to the
        end of the order. One basis everywhere: cap, then strip; empty after the strip drops."""
        padded = {"id": "g2", "name": "web ", "color": "", "members": ["s1"]}
        n = km._norm_timeline_views({"tags": [padded], "tagOrder": ["web ", " pool", "   "],
                                     "actives": {"timeline": {"tags": ["web "]}, "chat": {"tags": ["  "]},
                                                 "outline": {"tags": ["x" * 60 + "  "]}}})
        self.assertEqual(n["tags"][0]["name"], "web")
        self.assertEqual(n["tagOrder"], ["web", "pool"], "order entries strip like the rows; blanks drop")
        self.assertEqual(n["actives"]["timeline"], {"tags": ["web"]}, "the lens names the stored tag again")
        self.assertEqual(n["actives"]["chat"], {"all": True}, "a lens of blanks is no selection: All")
        self.assertEqual(n["actives"]["outline"], {"tags": ["x" * km._VIEWS_MAX_NAME]}, "cap first, then strip — the row's own order")
        rendered = {"tags": [{"id": "g2", "name": "web", "members": ["s1"]}]}       # the client shape _lens_visible reads
        self.assertTrue(km._lens_visible(rendered, n["actives"]["timeline"], "s1"),
                        "the padded lens admits the tag's member: the two spellings are one name")


class TimelineViews(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_default_shows_everything(self):
        # fresh blobs open on "all" — and since 2026-08-24 that sentinel means truly-ALL, so a
        # legacy blob persisted when "all" meant untagged lands on the new default automatically
        v = km._timeline_views()
        self.assertEqual(v, {"active": "all", "tags": [],
                         "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}})   # per-surface lenses seed from the scalar (2026-08-25)
        self.assertTrue(km._view_visible(v, "anything"))

    def test_all_shows_every_session_and_untagged_the_tagless_ones(self):
        # ALL — the default — shows LITERALLY EVERYTHING (the user 2026-08-24, retiring the hidden
        # set outright: the tag system covers backgrounding); a legacy hidden key is ignored
        km._set_timeline_views({"active": "all", "hidden": ["s9"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()   # _view_visible reads the RENDERED shape (string members + remoteTags)
        self.assertTrue(km._view_visible(v, "s9"), "a legacy hidden entry is IGNORED — nothing hides from All")
        self.assertTrue(km._view_visible(v, "s2"), "TAGGED → All still shows it")
        self.assertTrue(km._view_visible(v, "s1"), "untagged → shown")
        # the untagged view keeps the old default's meaning under its own sentinel (the user
        # 2026-08-23 TAG rule: tagging says "specialized — out of the main view")
        km._set_timeline_views({"active": "untagged", "hidden": ["s9"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()
        self.assertEqual(v["active"], "untagged", "the sentinel survives the normalizer round-trip")
        self.assertTrue(km._view_visible(v, "s9"), "…and legacy hidden does not hide in untagged either")
        self.assertFalse(km._view_visible(v, "s2"), "TAGGED → out of the untagged view")
        self.assertTrue(km._view_visible(v, "s1"), "tagless → shown")
        km._set_timeline_views({"active": "g1", "hidden": ["s2"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()
        self.assertTrue(km._view_visible(v, "s2"), "a tag view shows exactly its members")
        self.assertFalse(km._view_visible(v, "s1"), "…and nothing else")

    def test_legacy_groups_key_reads_as_tags(self):
        # a pre-rename timeline-views.json (or an un-updated Obsidian panel posting the whole blob)
        # must lose nothing on upgrade; when BOTH keys appear, "tags" is the authoritative one
        v = km._norm_timeline_views({"active": "g1", "hidden": [], "groups": [G1]})
        want = dict(G1, members=[{"host": "", "sid": "s2"}, {"host": "", "sid": "s3"}])
        self.assertEqual(v["tags"], [want], "the legacy key migrates in — string members become pairs")
        self.assertEqual(v["active"], "g1", "…and its active tag survives the read")
        self.assertNotIn("groups", v, "the normalized shape carries tags only")
        both = km._norm_timeline_views({"tags": [G1], "groups": [{"id": "gX", "name": "stale"}]})
        self.assertEqual([t["id"] for t in both["tags"]], ["g1"], "tags wins when both keys appear")

    def test_normalizer_drops_junk_and_falls_back_to_all(self):
        v = km._norm_timeline_views({"active": "ghost", "hidden": ["a", 7, "", "a"],
                                     "tags": [{"id": "g1", "name": "x" * 99, "members": ["m", 3]},
                                              {"noid": True}, "junk"]})
        self.assertEqual(km._norm_timeline_views({"hidden": 7, "tags": "nope"}),
                         {"active": "all", "tags": [],
                          "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}},
                         "wrong-TYPED fields drop instead of raising")
        self.assertEqual(km._norm_timeline_views({"tags": [{"id": "g", "members": 3}]})["tags"][0]["members"],
                         [], "a wrong-typed members list drops, never raises")
        self.assertEqual(v["active"], "all", "an active tag that does not exist falls back to all")
        self.assertEqual(km._norm_timeline_views({"active": "untagged"})["active"], "untagged",
                         "the untagged sentinel passes the whitelist — a rewrite here is SILENT and"
                         " reads as flicker after the client's optimistic hold expires")
        self.assertNotIn("hidden", v, "a legacy hidden key is dropped outright (retired 2026-08-24)")
        self.assertEqual(len(v["tags"]), 1)
        self.assertEqual(len(v["tags"][0]["name"]), km._VIEWS_MAX_NAME)
        self.assertEqual(v["tags"][0]["members"], [{"host": "", "sid": "m"}])

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._timeline_views()["tags"], [])
        km._set_timeline_views({"tags": [{"id": "g9", "name": "pool", "members": ["s9"]}]})
        self.assertEqual([t["id"] for t in km._timeline_views()["tags"]], ["g9"], "mtime+size key sees the write")

    def test_churn_heal_copies_membership(self):
        # COPY, never move (the hidden half retired with the set, 2026-08-24): a still-alive
        # same-name session would have its state stolen by a move
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "g1", "name": "pool", "members": ["old", "other"]}]})
        km._heal_timeline_views("old", "new")
        v = km._timeline_views()
        self.assertEqual(v["tags"][0]["members"],
                         [{"host": "", "sid": x} for x in ("new", "old", "other")],
                         "the fork inherits membership; the old sid keeps it")
        before = json.loads((jd.STATE / "timeline-views.json").read_text())
        km._heal_timeline_views("stranger", "new2")   # untouched sid → no write at all
        self.assertEqual(json.loads((jd.STATE / "timeline-views.json").read_text()), before)

    def test_ordered_fork_splice_heals_views(self):
        # the same name-keyed splice that inherits the ORDER slot carries the views state with it
        km._write_session_order(["old"])
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / "old").write_text("web\t/tmp\t#123456\twhite\n")
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": ["old"]}]})
        km._ordered([{"sid": "old", "name": "web"}, {"sid": "new", "name": "web"}])
        self.assertEqual(km._timeline_views()["tags"][0]["members"],
                         [{"host": "", "sid": "new"}, {"host": "", "sid": "old"}],
                         "membership copied, so the fork keeps its tag")

    def test_stored_hidden_entries_migrate_into_the_archived_tag_once(self):
        # the ONE-TIME MIGRATION (the user 2026-08-24, retiring hide-from-chat outright): a blob
        # persisted by the pre-retirement normalizer still carries hidden entries — the first read
        # maps them into an "archived" tag (muted color), drops the key, and persists; they show
        # under All (nothing hides from All now) and stay out of the untagged view.
        raw = {"active": "all", "hidden": ["s7", "s8"], "tags": [{"id": "g1", "name": "pool", "members": ["s2"]}]}
        (jd.STATE / "timeline-views.json").write_text(json.dumps(raw))
        km._flags_cache.clear()
        # ONE write (the 2026-09-05 review): the migration used to persist through the
        # setter, whose own read of the previous blob found the same un-migrated file and re-entered
        # the migration — 321 nested writes for one read, ending only at the recursion limit
        writes = []
        real = km._atomic_write
        km._atomic_write = lambda path, text, mode=None: (writes.append(1), real(path, text, mode))[1]
        try:
            v = km._timeline_views()
        finally:
            km._atomic_write = real
        self.assertEqual(len(writes), 1, "the migration is one write")
        self.assertNotIn("hidden", v)
        arch = next(t for t in v["tags"] if t["name"] == "archived")
        self.assertEqual(arch["members"], [{"host": "", "sid": "s7"}, {"host": "", "sid": "s8"}])
        self.assertEqual(arch["color"], "#6b7280", "a muted slate — never a status color")
        on_disk = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertNotIn("hidden", on_disk, "the mapping persisted — the next read has nothing to migrate")
        rendered = km._views_client()   # _view_visible reads the RENDERED shape (string members)
        self.assertTrue(km._view_visible(rendered, "s7"), "archived sessions SHOW under All")
        self.assertFalse(km._view_visible(dict(rendered, active="untagged"), "s7"), "…and stay out of untagged (tagged now)")
        # idempotent: a second read (fresh cache) neither duplicates members nor re-mints the tag
        km._flags_cache.clear()
        v2 = km._timeline_views()
        self.assertEqual([t["name"] for t in v2["tags"]].count("archived"), 1)
        self.assertEqual(next(t for t in v2["tags"] if t["name"] == "archived")["members"], arch["members"])
        # an install with NO hidden entries never mints the tag
        (jd.STATE / "timeline-views.json").write_text(json.dumps({"active": "all", "tags": []}))
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"], [], "minted only when hidden entries exist")

    def test_a_padded_archived_tag_is_found_on_the_stored_basis_so_the_hidden_entries_migrate_into_it(self):
        # the 2026-09-05 review: the lookup compared the file's RAW spelling, so a legacy
        # store holding "archived " (a padded twin from before the name basis) missed it, a second
        # archived tag was minted, the door's collision pass refused that one, and the hidden entries
        # migrated into nothing — the hide intent lost under a "name collision" notice
        raw = {"active": "all", "hidden": ["s7"], "tags": [{"id": "g9", "name": "archived ", "color": "#123456", "members": []}]}
        (jd.STATE / "timeline-views.json").write_text(json.dumps(raw))
        km._flags_cache.clear()
        notices = []
        saved = km._sync_notice
        km._sync_notice = lambda text, ok=True: notices.append((text, ok))
        try:
            v = km._timeline_views()
        finally:
            km._sync_notice = saved
        self.assertEqual([(t["id"], t["name"]) for t in v["tags"]], [("g9", "archived")],
                         "one archived tag, the file's own, read on the stored basis")
        self.assertEqual(v["tags"][0]["members"], [{"host": "", "sid": "s7"}], "the hidden entry migrated into it")
        self.assertEqual(notices, [], "a migration that fits is a stamp, not a refusal")
        on_disk = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertNotIn("hidden", on_disk)
        self.assertEqual(len(on_disk["tags"]), 1)

    def test_ws_op_persists_via_normalizer(self):
        # the handler body is _set_timeline_views + _mark_views_dirty; pin the setter's normalization
        km._set_timeline_views({"active": "g9", "hidden": ["x"], "tags": []})
        v = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertEqual(v["active"], "all")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "setTimelineViews"', src)
        self.assertIn('_set_timeline_views(msg["views"], edited=edited)', src, "the door passes the edited ids down: the setter files its notice by the ack's rule")

    def test_payloads_echo_the_views_blob(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('**_views_payload(),', src, "the timeline payload carries the RENDERED shape (through the one carrier, which marks "
                      "a blob a read fault left unproved or sends the marker alone, 2026-09-08)")
        self.assertIn('"palette": pal.colors(_palette_name()),', src, "and the palette, for tag colors in every host")
        self.assertIn('_send_tab_order(c, tab_order, tab_meta, tmux)', src, "tabOrder pushes carry it (through the one frame builder, T258)")
        # every tabOrder frame is built by ONE helper (2026-09-06: the frame also carries selfHost; 2026-09-07: it is
        # built per client through _send_tab_order, so a reconnecting client's skeleton list can ride it; 2026-09-08:
        # the views ride the one carrier, which marks a blob a read fault left unproved or sends the marker alone)
        self.assertIn('fr = {"type": "tabOrder", "order": list(order), "tabs": tabs, "selfHost": _self_host(),\n'
                      '          **_views_payload(), "live": sorted({str(x) for x in live})}', src, "tabOrder frames carry it")
        self.assertIn('**_views_payload(), "live":', src, "…which carries the blob")

    def test_web_boot_exposes_the_set_views_hook(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        # the hook carries the write's id since 2026-09-05, so the kernel's viewsAck can name it, and the
        # tag ids the write changed (`edited`), so a refusal on an untouched tag is acked ok
        self.assertIn("window.__rompTimelineSetViews=function(views,writeId,edited)", src)
        self.assertIn('post({type:"setTimelineViews",views:views,writeId:writeId,edited:edited||[]});', src)

    # ── tag federation v0 (the user 2026-08-24): canonical pairs, the rendered union, remote views ──

    def test_member_pairs_round_trip_all_three_spellings(self):
        # strings ("sid", "host:sid") and pair dicts all land as canonical pairs; the rendered
        # client shape respells them viewer-relative — a lossless round trip either way
        km._set_timeline_views({"active": "all", "hidden": [], "tags": [
            {"id": "g1", "name": "mixed", "members": ["s1", "alpha:s2", {"host": "beta", "sid": "s3"}]}]})
        km._flags_cache.clear()
        stored = km._timeline_views()["tags"][0]["members"]
        self.assertEqual(stored, [{"host": "", "sid": "s1"}, {"host": "alpha", "sid": "s2"},
                                  {"host": "beta", "sid": "s3"}])
        rendered = km._views_client()["tags"][0]["members"]
        self.assertEqual(rendered, ["s1", "alpha:s2", "beta:s3"],
                         "the rendering spelling is exactly the pre-pairs client contract")

    def _attach(self, host, views, status="up"):
        km._remotes[host] = {"host": host, "status": status, "views": views}

    def test_remote_tags_join_the_client_shape_read_only_and_host_stamped(self):
        # a tag created on kernel alpha, with a member of its own, one of beta's, and one of OURS —
        # the sid-first respelling: alpha's home member wears alpha's host here, beta's keeps
        # alpha's label for beta (it joins whenever beta is attached here too), and OUR session
        # (known locally by sid — uuids are global) renders bare, so the lane join lands
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / "00000000-aaaa-bbbb-cccc-000000000001").write_text("web\t/tmp\t#123456\twhite\n")
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"active": "all", "hidden": [], "tags": [
                {"id": "g9", "name": "team", "color": "#DD42FF",
                 "members": [{"host": "", "sid": "rs1"}, {"host": "beta", "sid": "rs2"},
                             {"host": "snape", "sid": "00000000-aaaa-bbbb-cccc-000000000001"}]}]})
            v = km._views_client()
            self.assertEqual(v["remoteTags"], [{
                "id": "alpha:g9", "host": "alpha", "name": "team", "color": "#DD42FF",
                "members": ["alpha:rs1", "beta:rs2", "00000000-aaaa-bbbb-cccc-000000000001"]}])
            # …and picking it filters exactly like a local tag, falling OPEN when the kernel detaches
            v["active"] = "alpha:g9"
            self.assertTrue(km._view_visible(v, "alpha:rs1"))
            self.assertFalse(km._view_visible(v, "somebody-else"))
            gone = {"active": "alpha:g9", "hidden": [], "tags": []}
            self.assertTrue(km._view_visible(gone, "somebody-else"),
                            "a vanished remote tag falls open, never trapping the viewer in an empty view")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_a_tag_view_is_the_name_keyed_union_across_kernels(self):
        # user ruling 2026-08-24: a tag IS its name — whichever store's id is active, the view shows
        # the union of every same-name tag's members, local and remote joined. The stored duplicates
        # stay separate (anti-clobber); this is the read, and both client mirrors pin it identically.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("TESTHOST-A", {"tags": [{"id": "g1", "name": "team", "color": "#DD42FF",
                                                  "members": [{"host": "", "sid": "rs1"}]}]})
            km._set_timeline_views({"active": "all", "hidden": [],
                                    "tags": [{"id": "gL", "name": "team", "color": "#123456",
                                              "members": ["local1"]}]})
            km._flags_cache.clear()
            v = km._views_client()
            v["active"] = "gL"                     # the LOCAL id activates the union
            self.assertTrue(km._view_visible(v, "local1"))
            self.assertTrue(km._view_visible(v, "TESTHOST-A:rs1"), "the remote store's member joins")
            self.assertFalse(km._view_visible(v, "other"))
            v["active"] = "TESTHOST-A:g1"          # …and so does the remote id — same union
            self.assertTrue(km._view_visible(v, "local1"))
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_same_name_tags_on_two_kernels_stay_two_entries(self):
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"tags": [{"id": "g1", "name": "team", "members": []}]})
            self._attach("beta", {"tags": [{"id": "g1", "name": "team", "members": []}]})
            ids = [t["id"] for t in km._views_client()["remoteTags"]]
            self.assertEqual(ids, ["alpha:g1", "beta:g1"], "host-disambiguated — never a silent merge")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_a_padded_remote_name_renders_on_the_stored_basis_so_a_lens_can_pick_it(self):
        """The 2026-09-05 review: the review put every lens and order entry on the stored name
        basis (cap, then strip) while a REMOTE tag's name still rendered clamped only — so a remote
        kernel on an older build (or a padded remote store) serving "web " rendered "web ", the lens
        that picked it stored "web", and _lens_visible (exact equality, mirrored by tag-lens.ts)
        admitted none of its members: the surface filtered to that tag showed no session, silently."""
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("peer", {"tags": [
                {"id": "r1", "name": "web ", "color": "", "members": [{"host": "", "sid": "s9"}]},
                {"id": "r2", "name": "x" * 60 + "  ", "color": "", "members": []},
                {"id": "r3", "name": "   ", "color": "", "members": []}]})
            km._set_timeline_views({"active": "all",
                                    "tags": [{"id": "gL", "name": "api", "color": "", "members": ["s1"]}],
                                    "actives": {"timeline": {"tags": ["web "]}}, "tagOrder": ["web ", "api"]})
            km._flags_cache.clear()
            v = km._views_client()
            self.assertEqual([t["name"] for t in v["remoteTags"]], ["web", "x" * km._VIEWS_MAX_NAME, "tag"],
                             "cap, then strip — the rows' own basis; empty after the strip is the default name")
            self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]})
            self.assertTrue(km._view_visible(v, "peer:s9", "timeline"),
                            "the lens stored as \"web\" admits the remote tag's member")
            self.assertFalse(km._view_visible(v, "s1", "timeline"), "…and only it")
            self.assertEqual(v["tagOrder"], ["web", "api"], "the order entry ranks the rendered remote name")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_the_union_joins_a_padded_remote_twin_to_the_local_tag(self):
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("peer", {"tags": [{"id": "r1", "name": "web ", "color": "",
                                            "members": [{"host": "", "sid": "s9"}]}]})
            km._set_timeline_views({"active": "all",
                                    "tags": [{"id": "gL", "name": "web", "color": "", "members": ["local1"]}]})
            km._flags_cache.clear()
            v = km._views_client()
            v["active"] = "gL"
            self.assertTrue(km._view_visible(v, "peer:s9"),
                            "a tag is its name: the padded remote twin joins the local tag's view")
            v["active"] = "peer:r1"
            self.assertTrue(km._view_visible(v, "local1"), "…from either id")
            self.assertTrue(km._lens_visible(v, {"tags": ["web"]}, "peer:s9"))
            self.assertFalse(km._lens_visible(v, {"none": True}, "peer:s9"), "its member is tagged, so not untagged")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_a_pending_edit_on_a_padded_remote_name_is_captured_joined_and_applied_on_the_basis(self):
        """The pending-edit journal (tag federation v2) matched a row's name to the host's raw tag name
        at capture and at late-apply, and joined it to the rendered row for the `pending` badge —
        every one of those is on the stored basis now, so a padded remote name is one name there too."""
        saved = dict(km._remotes)
        seams = (km._poll_remote_views, km._remote_forward)

        def fresh_journal():
            try:
                km._pending_tag_path().unlink()
            except OSError:
                pass
            with km._PENDING_TAG_LOCK:
                km._PENDING_TAG_CACHE["rows"] = None
        fresh_journal()
        try:
            km._remotes.clear()
            r = {"host": "peer", "status": "up", "local_port": 1, "token": "",
                 "views": {"tags": [{"id": "r1", "name": "web ", "color": "", "members": []}]}}
            km._remotes["peer"] = r
            self.assertTrue(km._queue_pending_tag_edit("peer", {"name": " web", "delete": True}),
                            "the user's spelling, padded differently again")
            row = km._pending_tag_rows()[-1]
            self.assertEqual((row["name"], row["tagId"]), ("web", "r1"),
                             "journaled on the basis, with the cached tag's id")
            v = km._views_client()
            self.assertEqual(v["remoteTags"][0].get("pending"), "delete", "the badge joins the rendered row")
            self.assertEqual(v["pendingTagEdits"], [{"host": "peer", "name": "web", "op": "delete"}])
            forwarded = []
            km._poll_remote_views = lambda r: {"tags": [{"id": "r1", "name": "web ", "color": "", "members": []}]}
            km._remote_forward = lambda r, path, body: (forwarded.append((path, body))
                                                        or {"ok": True, "deleted": True})
            self.assertEqual(km._apply_pending_tag_edits(r), 1, "the late apply finds the padded tag by its basis")
            self.assertEqual(forwarded, [("/tag", {"name": "web ", "delete": True})],
                             "…and addresses the host in its own spelling, which its door strips")
            self.assertEqual(km._pending_tag_rows(), [])
        finally:
            km._poll_remote_views, km._remote_forward = seams
            km._remotes.clear(); km._remotes.update(saved)
            fresh_journal()

    def test_a_down_kernels_CACHE_keeps_contributing_and_active_hidden_stay_local(self):
        # REVERSED from v0 (the user 2026-08-24, the untagged-view bug): visibility must not flap
        # with a peer's restart, so the last-known tags keep excluding from untagged and keep their
        # views pickable while the link reconnects. Bounded staleness — the auto-reconnect heals
        # within a pass, and detach pops the row and its cache with it.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"tags": [{"id": "g1", "name": "team", "members": []}]}, status="down")
            v = km._views_client()
            self.assertEqual([t["id"] for t in v.get("remoteTags") or []], ["alpha:g1"],
                             "the cached read stands while the kernel reconnects")
            # a remote-tag ACTIVE survives normalization (validated at read time, ":" is the marker);
            # hidden stays exactly the viewer-local list — neither is federated state
            n = km._norm_timeline_views({"active": "alpha:g1", "hidden": ["h1"], "tags": []})   # legacy hidden: dropped below
            self.assertEqual(n["active"], "alpha:g1")
            self.assertNotIn("hidden", n, "legacy hidden dropped (retired 2026-08-24); active stays local")
            # a client echoing the derived remoteTags back never persists it
            n2 = km._norm_timeline_views({"active": "all", "remoteTags": [{"id": "x"}], "tags": []})
            self.assertNotIn("remoteTags", n2)
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_untagged_excludes_by_the_UNION_both_tag_homes(self):
        # the user's repro (2026-08-24): they tagged a session from the chat while showing only
        # untagged, and it STAYED — the untagged branch counted local tags only, so a REMOTE-homed
        # tag never excluded. A tag is its NAME wherever it homes: held by any kernel's tag = tagged.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            # remote-homed: the viewer's kernel holds no tag at all; alpha's tag holds two sessions —
            # one of alpha's own, and one of OURS (the local sid, known here)
            (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
            (jd.STATE / "names" / "00000000-aaaa-bbbb-cccc-000000000002").write_text("cards\t/tmp\t#123456\twhite\n")
            self._attach("alpha", {"tags": [{"id": "g1", "name": "workers", "color": "#DD42FF",
                                             "members": [{"host": "", "sid": "rsid1"},
                                                         {"host": "viewer", "sid": "00000000-aaaa-bbbb-cccc-000000000002"}]}]})
            km._set_timeline_views({"active": "untagged", "hidden": [], "tags": []})
            km._flags_cache.clear()
            v = km._views_client()
            self.assertFalse(km._view_visible(v, "alpha:rsid1"),
                             "a remote-homed tag excludes its member from untagged")
            self.assertFalse(km._view_visible(v, "00000000-aaaa-bbbb-cccc-000000000002"),
                             "…including OUR OWN session it holds — the exact reported case")
            self.assertTrue(km._view_visible(v, "someone-else"))
            # local-homed: unchanged behavior, pinned beside it
            km._set_timeline_views({"active": "untagged", "hidden": [],
                                    "tags": [{"id": "gL", "name": "local", "members": ["locsid"]}]})
            km._flags_cache.clear()
            v = km._views_client()
            self.assertFalse(km._view_visible(v, "locsid"))
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_per_surface_lenses_the_truth_table(self):
        # the multi-select decision (the user 2026-08-25): union over selected buckets, none = in
        # no tag home (name-keyed, remote included), All admits everything, surfaces independent
        views = {"active": "all",
                 "actives": {"chat": {"tags": ["workers"]},
                             "timeline": {"none": True, "tags": ["infra"]}},
                 "tags": [{"id": "g1", "name": "workers", "members": ["s1"]},
                          {"id": "g2", "name": "infra", "members": ["s2"]}],
                 "remoteTags": [{"id": "alpha:g9", "host": "alpha", "name": "workers",
                                 "members": ["alpha:r1"]}]}
        # chat: workers only — name-keyed union pulls the remote member in too
        self.assertTrue(km._view_visible(views, "s1", surface="chat"))
        self.assertTrue(km._view_visible(views, "alpha:r1", surface="chat"))
        self.assertFalse(km._view_visible(views, "s2", surface="chat"))
        self.assertFalse(km._view_visible(views, "loose", surface="chat"))
        # timeline: infra ∪ no-tags — arbitrary combinations, independent of chat's pick
        self.assertTrue(km._view_visible(views, "s2", surface="timeline"))
        self.assertTrue(km._view_visible(views, "loose", surface="timeline"))
        self.assertFalse(km._view_visible(views, "s1", surface="timeline"), "tagged, not selected")
        # a missing lens falls open (a surface never named yet)
        self.assertTrue(km._view_visible(views, "anything", surface="feed"))
        # the scalar path is untouched — legacy callers and their pins keep working
        self.assertTrue(km._view_visible(views, "anything"))

    def test_migration_seeds_every_surface_from_the_scalar_active(self):
        # lossless legacy (the user 2026-08-25): a blob without `actives` reads as if each surface
        # had selected the old shared view — untagged seeds the none bucket, a tag id its NAME
        v = km._norm_timeline_views({"active": "untagged"})
        self.assertEqual(v["actives"], {"chat": {"none": True}, "timeline": {"none": True},
                                        "outline": {"none": True}})
        v = km._norm_timeline_views({"active": "g1",
                                     "tags": [{"id": "g1", "name": "workers", "members": []}]})
        self.assertEqual(v["actives"], {"chat": {"tags": ["workers"]},
                                        "timeline": {"tags": ["workers"]},
                                        "outline": {"tags": ["workers"]}})
        # once a blob CARRIES actives, they win — and a surface absent from it reads All, never
        # a re-seed (the scalar may lag the lenses; seeding again would resurrect a stale pick)
        v = km._norm_timeline_views({"active": "untagged",
                                     "actives": {"chat": {"tags": ["workers"]}}})
        self.assertEqual(v["actives"]["chat"], {"tags": ["workers"]})
        self.assertEqual(v["actives"]["timeline"], {"all": True})

    def test_lens_normalization_never_strands_an_empty_selection(self):
        v = km._norm_timeline_views({"actives": {"chat": {"tags": []}, "timeline": {"none": False}}})
        self.assertEqual(v["actives"]["chat"], {"all": True})
        self.assertEqual(v["actives"]["timeline"], {"all": True})
        # unknown names are KEPT — they may live on a linked kernel (name-keyed federation)
        v = km._norm_timeline_views({"actives": {"chat": {"tags": ["only-on-alpha"]}}})
        self.assertEqual(v["actives"]["chat"], {"tags": ["only-on-alpha"]})

    def test_focus_never_mutates_the_views_blob(self):
        # A focus is a PEEK, not a view edit (the user 2026-08-24, superseding the 2026-08-18/19/23
        # reveal rule this test used to pin): the chat opens an out-of-view session as an EPHEMERAL
        # peek tab client-side, so _reveal_chat_for must leave timeline-views.json byte-identical and
        # never mark views dirty — for EVERY case the old rule used to rewrite (hidden under All,
        # tagged from untagged, tagless from a tag view, hidden+tagged, unknown sid), and for the
        # confirmRevive shape too. The focus/shell send pair itself is pinned by
        # tests/test_kernel_mobile.py::RevealRouting.
        G = {"id": "g1", "name": "pool", "members": ["s2"]}
        dirty = []
        saved = km._mark_views_dirty
        km._mark_views_dirty = lambda: dirty.append(1)
        try:
            for blob, sid in [
                ({"active": "g1", "hidden": [], "tags": [G]}, "s9"),      # tagless from a tag view
                ({"active": "untagged", "hidden": [], "tags": [G]}, "s2"),  # tagged from untagged
                ({"active": "all", "hidden": ["sX"], "tags": [G]}, "sX"),   # hidden under All
                ({"active": "all", "hidden": ["s2"], "tags": [G]}, "s2"),   # hidden AND tagged
                ({"active": "g1", "hidden": ["sX"], "tags": [G]}, "sX"),    # hidden tagless, tag view
                ({"active": "g1", "hidden": [], "tags": [G]}, "s2"),        # already visible member
            ]:
                km._set_timeline_views(blob)
                before = (jd.STATE / "timeline-views.json").read_bytes()
                km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": sid})
                km._reveal_chat_for({"wid": "w1"}, {"type": "confirmRevive", "id": sid, "name": "web"})
                self.assertEqual((jd.STATE / "timeline-views.json").read_bytes(), before,
                                 "a focus gesture rewrote the views blob (%s → %s)" % (blob, sid))
            self.assertEqual(dirty, [], "no gratuitous views-dirty wake for a pure focus")
        finally:
            km._mark_views_dirty = saved


class TagInheritance(unittest.TestCase):
    """Tab groups are TAGS (the user 2026-09-04): a session spawned from another — a fork, a `romp new`
    from inside a session, a promoted comment thread — joins every local tag holding its parent, at
    the creation event. _inherit_tag_membership is _heal_timeline_views' sibling for a child with a
    NEW name (the name-keyed heal never fires for it). COPY, never move; no-op for an untagged
    parent; idempotent; local tags only (a remote-homed tag is the accepted v1 gap). _tag_new_session
    is the /new + createSession composite: inherit, then join the named tags (created on first use),
    and echo the child's names. Synthetic sids only."""

    P = "11111111-2222-3333-4444-555555555555"
    C = "66666666-7777-8888-9999-000000000000"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty)
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        jd.STATE, km._mark_views_dirty = self.saved
        self.td.cleanup()

    def _members(self, name):
        t = next(t for t in km._timeline_views()["tags"] if t["name"] == name)
        return [m["sid"] for m in t["members"] if m["host"] == ""]

    def test_the_child_joins_every_local_tag_holding_the_parent_and_the_parent_keeps_them(self):
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "g1", "name": "pool", "members": [self.P, "other"]},
            {"id": "g2", "name": "infra", "members": [self.P]},
            {"id": "g3", "name": "unrelated", "members": ["other"]}]})
        got = km._inherit_tag_membership(self.P, self.C)
        self.assertEqual(sorted(got), ["infra", "pool"], "the inherited names come back — the /new echo")
        self.assertEqual(sorted(self._members("pool")), sorted([self.C, self.P, "other"]), "COPY: the parent keeps its tag")
        self.assertEqual(sorted(self._members("infra")), sorted([self.C, self.P]))
        self.assertEqual(self._members("unrelated"), ["other"], "a tag the parent is not in is untouched")
        self.assertTrue(self.dirty, "the write wakes the pusher so the sectioned strip re-renders now")

    def test_an_untagged_parent_is_a_no_op_that_writes_nothing(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": ["other"]}]})
        before = (jd.STATE / "timeline-views.json").read_bytes()
        self.dirty.clear()
        self.assertEqual(km._inherit_tag_membership(self.P, self.C), [])
        self.assertEqual((jd.STATE / "timeline-views.json").read_bytes(), before, "no store write at all")
        self.assertEqual(self.dirty, [], "…and no gratuitous wake")
        self.assertEqual(km._inherit_tag_membership("", self.C), [], "a missing parent is not an error")
        self.assertEqual(km._inherit_tag_membership(self.P, self.P), [], "self-inheritance is meaningless")

    def test_idempotent_a_second_run_adds_nothing(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [self.P]}]})
        km._inherit_tag_membership(self.P, self.C)
        km._inherit_tag_membership(self.P, self.C)
        self.assertEqual(sorted(self._members("pool")), sorted([self.C, self.P]), "the normalizer dedups pairs")

    def test_a_remote_homed_parent_tag_is_not_inherited_here_the_documented_v1_gap(self):
        # the parent held only by kernel alpha's tag (a remoteTags entry, read-only here): this kernel
        # cannot write that store, so the child inherits nothing — documented, not silent divergence
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            km._remotes["alpha"] = {"host": "alpha", "status": "up", "views": {"tags": [
                {"id": "g9", "name": "team", "members": [{"host": "viewer", "sid": self.P}]}]}}
            self.assertEqual(km._inherit_tag_membership(self.P, self.C), [])
            self.assertEqual(km._timeline_views()["tags"], [], "no local tag minted to mirror the remote one")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_tag_new_session_inherits_then_joins_the_named_tags_and_echoes_the_union(self):
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [self.P]}]})
        names, err = km._tag_new_session(self.C, self.P, ["infra"])
        self.assertIsNone(err)
        self.assertEqual(sorted(names), ["infra", "pool"], "the echo is the child's names AFTER both steps")
        self.assertEqual(self._members("infra"), [self.C], "a named tag is created on first use, like POST /tag")
        self.assertIn(self.C, self._members("pool"))

    def _twins(self):
        """A store ALREADY holding two tags named "twin" — written to the file, not through the door:
        the whole-blob path refuses a second tag under a name another tag holds, so twins can only
        come from an older kernel's store (or a hand edit)."""
        km._atomic_write(km._views_path(), json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "twin", "members": []}, {"id": "g2", "name": "twin", "members": []}]}))
        km._flags_cache.clear()

    def test_tag_new_session_reports_a_refused_edit_beside_what_did_land(self):
        self._twins()
        names, err = km._tag_new_session(self.C, "", ["twin", "infra"])
        self.assertIn("two tags are named", err or "", "the first refusal is named — never swallowed")
        self.assertEqual(names, ["infra"], "the tags that could land, did")

    def test_resolve_parent_sid_live_name_known_sid_and_the_loud_unknown(self):
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / self.P).write_text("web\t/tmp\t#123456\twhite\n")
        live = {"api": self.C}
        self.assertEqual(km._resolve_parent_sid("api", live), (self.C, None), "a live name resolves")
        self.assertEqual(km._resolve_parent_sid(self.P, live), (self.P, None), "a sid with a names/ entry resolves")
        self.assertEqual(km._resolve_parent_sid("", live), ("", None), "nothing named = nothing to inherit, no error")
        sid, err = km._resolve_parent_sid("99999999-0000-0000-0000-000000000000", live)
        self.assertIsNone(sid)
        self.assertIn("not a session this kernel knows", err)
        sid, err = km._resolve_parent_sid("nope", live)
        self.assertIsNone(sid, "an unknown name is refused too")

    def test_tags_error_shapes(self):
        self.assertIsNone(km._tags_error(["pool", "infra"]))
        self.assertIsNone(km._tags_error([]))
        self.assertIn("list", km._tags_error("pool"))
        self.assertIn("non-empty", km._tags_error(["pool", ""]))
        self.assertIn("non-empty", km._tags_error([3]))

    def test_a_remote_member_wearing_the_parents_sid_does_not_count_as_holding_it(self):
        # the LOCAL-member check: members are (host, sid) pairs and only a host=="" pair is this
        # kernel's session — a remote kernel's member carrying the same sid string is another host's
        # session. Matching on sid alone would copy that tag onto the child.
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "team", "members": ["alpha:" + self.P]}]})
        before = (jd.STATE / "timeline-views.json").read_bytes()
        self.dirty.clear()
        self.assertEqual(km._inherit_tag_membership(self.P, self.C), [], "not held here → nothing inherited")
        self.assertEqual((jd.STATE / "timeline-views.json").read_bytes(), before, "no store write at all")
        self.assertEqual(self.dirty, [])

    def test_the_inherit_copies_the_store_and_stamps_the_tag_so_a_stale_echo_cannot_strip_the_child(self):
        # _timeline_views() hands out ONE cached object per file version. An in-place append on it
        # would make the setter's prev == new at write time, so the tag's mtime would not move — and a
        # dashboard echoing the blob it was served BEFORE the inherit would pass the stale-writer guard
        # and silently strip the child (the 2026-08-31 loss class the guard exists for).
        T0 = int(time.time()) - 100
        served = {"active": "all", "at": T0, "tags": [{"id": "g1", "name": "pool", "color": "", "mtime": T0,
                                                      "members": [{"host": "", "sid": self.P}]}]}
        km._atomic_write(km._views_path(), json.dumps(served))
        km._flags_cache.clear()
        cached = km._timeline_views()
        self.assertEqual(km._inherit_tag_membership(self.P, self.C), ["pool"])
        self.assertEqual([m["sid"] for m in cached["tags"][0]["members"]], [self.P], "the cached blob was not mutated")
        self.assertGreater(km._timeline_views()["tags"][0]["mtime"], T0, "the write moved the tag's mtime")
        km._set_timeline_views(json.loads(json.dumps(served)))     # the stale echo: the pre-inherit blob, whole
        self.assertIn(self.C, self._members("pool"), "the guard kept the newer membership — the child survives")

    def test_tag_ack_echoes_the_request_positionally_with_the_stored_spelling(self):
        # the store trims a name and clamps it to _VIEWS_MAX_NAME; compared raw against `tags`, an
        # applied name read as "not applied" (the CLI's false warning) — tagsApplied says what each
        # request landed as, by position, so "applied as <name>" and "not applied" are distinct
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [self.P]}]})
        long = "a" * (km._VIEWS_MAX_NAME + 5)
        a = km._tag_ack(self.C, self.P, [" pool ", long, "infra"])
        self.assertEqual(a["tagsRequested"], [" pool ", long, "infra"], "as sent, in order")
        self.assertEqual(a["tagsApplied"], ["pool", "a" * km._VIEWS_MAX_NAME, "infra"], "trimmed, clamped, minted")
        self.assertEqual(sorted(a["tags"]), sorted(["pool", "a" * km._VIEWS_MAX_NAME, "infra"]), "the session's names after both")
        self.assertNotIn("tagError", a)
        self.assertEqual(km._tag_new_session(self.C, "", ["infra"]), (a["tags"], None),
                         "_tag_new_session is the (names, error) view of the same call")

    def test_tag_ack_marks_a_refused_slot_none_beside_the_ones_that_landed(self):
        self._twins()
        a = km._tag_ack(self.C, "", ["twin", "qa"])
        self.assertEqual(a["tagsApplied"], [None, "qa"])
        self.assertEqual(a["tags"], ["qa"])
        self.assertIn("two tags are named", a["tagError"])

    def test_resolve_parent_sid_maps_a_comment_thread_to_the_session_it_lives_in(self):
        # a thread's CLI carries the THREAD's sid as ROMP_SID, and a thread holds no tags (it has no
        # tab) — so a `romp new` run inside a thread inherits from the tab-bearing session it is of
        T = "77777777-8888-9999-0000-111111111111"
        P = self.P
        class Be:
            def owns(self, sid): return sid in (T, P)
            def thread_of(self, sid): return P if sid == T else ""
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: Be())
        try:
            self.assertEqual(km._resolve_parent_sid(T, {}), (P, None), "the thread's tag parent is its threadOf")
            self.assertEqual(km._resolve_parent_sid(P, {}), (P, None), "a session is its own")
        finally:
            km.Sessions.backend_for = saved

    def test_parent_from_request_honours_the_clis_auto_marker_for_an_unknown_parent_only(self):
        # `romp new` sends ROMP_SID as parent by default (parentAuto); aimed at a kernel that never
        # ran the caller, that is "nothing to inherit", named in the ack — not a 400 for a sid the
        # user never typed. An explicit parent (no marker: the picker, a raw caller) still refuses.
        U = "99999999-0000-0000-0000-000000000000"
        self.assertEqual(km._parent_from_request({"parent": U, "parentAuto": True}, {}), ("", None, U))
        sid, err, ign = km._parent_from_request({"parent": U}, {})
        self.assertIsNone(sid)
        self.assertIn("not a session this kernel knows", err)
        self.assertEqual(ign, "")
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / self.P).write_text("web\t/tmp\t#123456\twhite\n")
        self.assertEqual(km._parent_from_request({"parent": self.P, "parentAuto": True}, {}), (self.P, None, ""),
                         "a known auto parent inherits as ever")
        self.assertEqual(km._parent_from_request({}, {}), ("", None, ""))

    def test_a_dashboards_full_blob_write_cannot_land_inside_the_inherits_read_to_write_window(self):
        # the WS setTimelineViews write takes _views_lock like every other writer. Unlocked, a
        # dashboard blob landing between the inherit's read and its write either had its own added
        # member clobbered by the inherit's stale-read write, or made the inherit's write fail the
        # stale-writer guard while the inherit still returned the tag name (a child reported tagged,
        # not tagged). Locked, it waits: the inherit lands whole first, and the dashboard's blob is
        # then judged whole by the guard (its stale copy of the tag refused, loudly — never silently).
        T0 = int(time.time()) - 100
        served = {"active": "all", "at": T0, "tags": [{"id": "g1", "name": "pool", "color": "", "mtime": T0,
                                                      "members": [{"host": "", "sid": self.P}]}]}
        km._atomic_write(km._views_path(), json.dumps(served))
        km._flags_cache.clear()
        entered, release = threading.Event(), threading.Event()
        real_read = km._timeline_views_proved   # the RMW doors' seam (a proved read)
        inheriting = []
        def stalled_read():
            v = real_read()
            if threading.current_thread() in inheriting and not entered.is_set():
                entered.set()             # parked INSIDE the locked window: after the read, before the write
                release.wait(5)
            return v
        km._timeline_views_proved = stalled_read
        got = []
        def inherit():
            inheriting.append(threading.current_thread())
            got.append(km._inherit_tag_membership(self.P, self.C))
        client = {"app": "timeline", "alive": True, "send": lambda s: None}
        dash = json.loads(json.dumps(served))
        dash["tags"][0]["members"].append({"host": "", "sid": "other"})
        t1 = threading.Thread(target=inherit, daemon=True)
        t2 = threading.Thread(target=lambda: km.Handler._dispatch_ws(object.__new__(km.Handler),
                                                                      {"type": "setTimelineViews", "views": dash}, client), daemon=True)
        try:
            t1.start()
            self.assertTrue(entered.wait(5), "the inherit parks inside its window")
            t2.start()
            t2.join(0.5)
            self.assertTrue(t2.is_alive(), "the full-blob write waits for the lock — it cannot land inside the window")
            self.assertEqual(self._members("pool"), [self.P], "…and nothing reached the store meanwhile")
        finally:
            release.set()
            t1.join(5)
            t2.join(5)
            km._timeline_views_proved = real_read
        self.assertEqual(got, [["pool"]])
        self.assertIn(self.C, self._members("pool"), "the inherit's write landed; the dashboard's stale copy could not strip it")

    def test_a_tag_edit_cannot_land_inside_the_heals_read_to_write_window(self):
        # _heal_timeline_views (fsid churn: a /clear or revive of a TAGGED session, run from the
        # session-order builder on push and handler threads) takes _views_lock like every other
        # writer. Unlocked, a POST /tag edit landing between the heal's read and its write was lost
        # one way or the other: the heal's stale copy overwrote the edit (the added member gone while
        # _edit_tag had already answered success), or the stale-writer guard refused the heal's copy
        # and the /clear'd session's new fsid fell out of its tag for good (the heal fires once per
        # new sid). Locked, the edit waits and both land. The sibling of the setTimelineViews pin
        # above; a mutant that drops the heal's lock survived the whole suite until this one.
        T0 = int(time.time()) - 100
        served = {"active": "all", "at": T0, "tags": [{"id": "g1", "name": "pool", "color": "", "mtime": T0,
                                                      "members": [{"host": "", "sid": "old"}]}]}
        km._atomic_write(km._views_path(), json.dumps(served))
        km._flags_cache.clear()
        entered, release = threading.Event(), threading.Event()
        real_read = km._timeline_views_proved   # the RMW doors' seam (a proved read)
        healing = []
        def stalled_read():
            v = real_read()
            if threading.current_thread() in healing and not entered.is_set():
                entered.set()             # parked INSIDE the heal's window: after its read, before its write
                release.wait(5)
            return v
        km._timeline_views_proved = stalled_read
        def heal():
            healing.append(threading.current_thread())
            km._heal_timeline_views("old", "new")
        edited = []
        t1 = threading.Thread(target=heal, daemon=True)
        t2 = threading.Thread(target=lambda: edited.append(km._edit_tag("pool", add=["other"])), daemon=True)
        try:
            t1.start()
            self.assertTrue(entered.wait(5), "the heal parks inside its window")
            t2.start()
            t2.join(0.5)
            self.assertTrue(t2.is_alive(), "POST /tag's edit waits for the lock — it cannot land inside the window")
            self.assertEqual(self._members("pool"), ["old"], "…and nothing reached the store meanwhile")
        finally:
            release.set()
            t1.join(5)
            t2.join(5)
            km._timeline_views_proved = real_read
        self.assertEqual(sorted(self._members("pool")), ["new", "old", "other"], "both writers landed whole, in turn")
        self.assertEqual(len(edited), 1)
        self.assertIsNone(edited[0][1], "the edit was not refused")
