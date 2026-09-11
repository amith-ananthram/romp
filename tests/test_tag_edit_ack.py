#!/usr/bin/env python3
"""The tags dialog's write path is acknowledged (the user 2026-09-05, who lost a batch of tag
renames and assignments made in "Manage tags").

Root cause: the dialog posted the WHOLE views blob for every edit, built from its own un-echoed
optimistic copy, so a burst of edits carried the pre-burst `at` stamp. The store's stale-writer
guard (test_views_stale_writer.py) stamps a tag on its first edit and then judges the second edit
to the same tag — a create followed by typing its name — stale against the client's OWN previous
write, refuses it, and tells only stderr and the sync notices; the poster never learned, kept
building from the refused copy, and the dialog snapped to the store ("tag N", no members) once the
user paused.

Two changes at the kernel's door, both pinned here through the real WS dispatcher:
  - `tagEdit` — a TARGETED op (create / rename / recolor / addMember / removeMember / delete, by
    tag NAME) applied through _edit_tag, the read-modify-write merge the /tag route already uses,
    which deep-copies the store and so always carries the newest evidence. Never refused as stale.
  - every views write is ANSWERED on the posting socket: {tagEditAck|viewsAck, writeId, ok,
    views, error?, refused?}. The blob in the ack is the post-write client blob, the poster's new
    base; a refusal names what was refused and why, in plain words.
The whole-blob `setTimelineViews` stays for lens and order edits, with the guard unchanged (its
stderr and sync-notice paths still fire) plus the ack. Frames pushed to other clients are unchanged.
Synthetic sids only."""
import contextlib
import errno
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
# That is the loads' import-time floor only: every test runs under a state root of its own (_own_state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_tagack", os.path.join(BIN, "romp-kernel"))

SID1 = "11111111-2222-3333-4444-555555555501"
SID2 = "11111111-2222-3333-4444-555555555502"
SID3 = "11111111-2222-3333-4444-555555555503"
WEB = {"id": "gA", "name": "web", "color": "#3b82f6", "members": [SID1]}


def store_tag(name):
    return next((t for t in km._timeline_views()["tags"] if t["name"] == name), None)


def _own_state(test):
    """A state root of the test's own, bound into the judge here and put back and removed by a cleanup on
    the same test (a cleanup, so it runs after tearDown). The judge is ONE module object per process (a
    SourceFileLoader load of a name already in sys.modules re-executes into that object), so jd.STATE is
    shared with every module an xdist worker collected, and a fixture elsewhere that left it bound to a
    removed directory reached this module: _atomic_write recreates the parent and a direct write_text does
    not, so a test whose first touch of the views file is a write_text (a fixture written before any seed)
    failed with FileNotFoundError when it ran first after such a fixture (LegacyStoreStampedOnce,
    LegacyTagsStampedOnFirstRead, MigrationStampsTheArchivedTag, ReaderRestampKeepsTheDiskCap and
    ReaderRestampUnwritableNamesTheDrop as classes, ReaderRestampUnwritable's read-only test alone). The
    suite's shared-state check (tests/conftest.py, _shared_state_restored) fails the test that leaves such
    a binding; a root per test makes this module independent of that check and of test order, and the
    cleanup drops this root's entries from the two path-keyed caches. OwnStateRoot pins the shape."""
    saved = km.jd.STATE
    root = tempfile.mkdtemp()
    km.jd._rebind_state(Path(root))
    views = str(km._views_path())

    def restore():
        km._flags_cache.pop(views, None)
        km._VIEWS_SEQ_FLOOR.pop(views, None)
        km.jd._rebind_state(saved)
        try:
            os.chmod(root, 0o755)    # the unwritable-store tests leave the dir 0o555 if they fail before their finally
        except OSError:
            pass
        shutil.rmtree(root, ignore_errors=True)
    test.addCleanup(restore)
    return Path(root)


class _Wire(unittest.TestCase):
    """A dashboard's timeline socket, through the real dispatcher; every ack it receives is kept."""

    def setUp(self):
        _own_state(self)
        self.notices = []
        self._sync = km._sync_notice
        km._sync_notice = lambda text, ok=True: self.notices.append((text, ok))
        self.handler = object.__new__(km.Handler)
        self.sent = []
        self.client = {"app": "timeline", "wid": "w-dash", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}

    def tearDown(self):
        km._sync_notice = self._sync

    @contextlib.contextmanager
    def real_notices(self):
        """The notices as a dashboard receives them: filed through the REAL ring for the block's duration
        (the harness captures them in-process otherwise), so the test reads them back through
        _sync_notice_rows — the served row, under the kernel's cap — while the block is open."""
        km._sync_notice = self._sync
        with km._SYNC_LOCK:
            saved = list(km._SYNC_NOTICES)
            km._SYNC_NOTICES.clear()
        try:
            yield
        finally:
            km._sync_notice = lambda text, ok=True: self.notices.append((text, ok))
            with km._SYNC_LOCK:
                km._SYNC_NOTICES[:] = saved

    def post(self, msg, client=None):
        """Dispatch one client frame; return the reply it produced on that socket (or None)."""
        c = client or self.client
        before = len(self.sent) if c is self.client else None
        km.Handler._dispatch_ws(self.handler, msg, c)
        if before is None:
            return None
        new = self.sent[before:]
        self.assertLessEqual(len(new), 1, "one write, at most one reply")
        return new[0] if new else None

    def seed(self):
        """Tag `web` holding SID1, written through the door so it is stamped (any earlier edit does this)."""
        ack = self.post({"type": "setTimelineViews", "writeId": "w0",
                         "views": {"active": "all", "tags": [dict(WEB)]}})
        self.assertEqual((ack["type"], ack["ok"], ack["refused"]), ("viewsAck", True, []))
        return ack["views"]


class OwnStateRoot(_Wire):
    """_own_state: the binding an earlier test left behind decides nothing about where this module's
    tests write, and comes back untouched afterwards. setUp leaves jd.STATE the way a neighbour's
    tearDown can (bound to a temp root that has been removed, the prior root not put back) BEFORE
    _Wire.setUp runs, so the helper is all that stands between a direct write_text and
    FileNotFoundError; without it the test here fails as the legacy-fixture classes did. The root the
    class found is registered as the FIRST cleanup, so it runs last and the class leaves jd.STATE as it
    found it: the dangling binding is made inside the test and undone by it, which is what the suite's
    shared-state check (tests/conftest.py, _shared_state_restored) requires of every test."""

    def setUp(self):
        found = km.jd.STATE
        self.addCleanup(km.jd._rebind_state, found)    # registered first, so it runs last: the root found here
                                                        # comes back after _own_state's own restore has run
        gone = Path(tempfile.mkdtemp())
        gone.rmdir()
        km.jd._rebind_state(gone)                       # a temp root, removed, never put back
        self.gone = gone
        super().setUp()

    def test_a_dangling_binding_left_by_an_earlier_test_is_not_inherited_by_a_direct_write(self):
        p = km._views_path()
        p.write_text(json.dumps({"active": "all", "tags": [dict(WEB)]}))   # a legacy fixture: write_text before any
                                                                            # seed; FileNotFoundError under a dangling root
        self.assertNotEqual(p.parent, self.gone, "the write landed under a root of this test's own, not the dangling one")
        self.assertTrue(p.parent.is_dir(), "and that root exists")
        self.assertIsNotNone(store_tag("web"), "and the reader reads the file back from it")


class TargetedTagEdits(_Wire):
    """The tagEdit message: {type, writeId, edit: {op, tid?, name?, newName?, color?, sid?|sids?}}.
    Every op but create addresses the tag by its stored id (the 2026-09-05 review):
    a create's ack returns the kernel-minted `tid` and `name`; the op rides nested under `edit` so
    the message has no top-level `name` or `session` a federation router could read as an address."""

    def edit(self, write_id, edit, client=None):
        return self.post({"type": "tagEdit", "writeId": write_id, "edit": edit}, client=client)

    def test_the_users_burst_create_then_rename_then_assign_before_any_frame_all_land_and_each_is_acked(self):
        """The exact gesture sequence that was lost, as targeted ops: nothing waits for an echo, and
        the store ends up holding the typed name and the assigned sessions."""
        self.seed()
        a1 = self.edit("w1", {"op": "create", "color": "#1EA1EB"})
        self.assertEqual((a1["type"], a1["writeId"], a1["ok"]), ("tagEditAck", "w1", True))
        tid = a1["tid"]
        self.assertTrue(isinstance(tid, str) and tid.startswith("g"), "the KERNEL mints the id and the ack returns it")
        self.assertEqual(a1["name"], "tag 1", "…and the default name, minted unique in the store")
        self.assertEqual(store_tag("tag 1")["id"], tid)
        a2 = self.edit("w2", {"op": "rename", "tid": tid, "newName": "notes-api"})
        self.assertEqual((a2["writeId"], a2["ok"], a2["tid"], a2["name"]), ("w2", True, tid, "notes-api"))
        self.assertEqual(store_tag("notes-api")["id"], tid, "the rename typed before any echo LANDS")
        self.assertIsNone(store_tag("tag 1"))
        a3 = self.edit("w3", {"op": "addMember", "tid": tid, "sids": [SID2, SID3]})
        self.assertEqual((a3["writeId"], a3["ok"]), ("w3", True))
        self.assertEqual(sorted(m["sid"] for m in store_tag("notes-api")["members"]), sorted([SID2, SID3]),
                         "the assignment lands too — the second half of the report")
        self.assertEqual(self.notices, [], "a targeted edit is never judged stale: it is built from the store")
        # the ack carries the poster's NEW BASE: the rendered client blob, stamped, with the edit in it
        v = a3["views"]
        self.assertTrue(v.get("at"), "the ack blob carries the store's write stamp")
        t = next(x for x in v["tags"] if x["id"] == tid)
        self.assertEqual((t["name"], sorted(t["members"])), ("notes-api", sorted([SID2, SID3])))
        self.assertTrue(t.get("mtime"), "…and the per-tag mtime the guard reads")
        self.assertNotIn("error", a3)

    def test_a_create_is_named_around_a_leftover_default_named_tag(self):
        """The round trip the dialog's own "tag N" from a row count could not make: with a leftover
        "tag 1" (and a "tag 3") in the store, the kernel mints "tag 2", then "tag 4" — never a
        duplicate the create would be refused for."""
        self.seed()
        self.assertTrue(self.edit("w0", {"op": "create", "name": "tag 1", "color": "#000000"})["ok"])
        self.assertTrue(self.edit("w0b", {"op": "create", "name": "tag 3", "color": "#000000"})["ok"])
        a = self.edit("w1", {"op": "create", "color": "#1EA1EB"})
        self.assertEqual((a["ok"], a["name"]), (True, "tag 2"))
        b = self.edit("w2", {"op": "create", "color": "#1EA1EB"})
        self.assertEqual((b["ok"], b["name"]), (True, "tag 4"))
        self.assertNotEqual(a["tid"], b["tid"])
        self.assertEqual(next(t["name"] for t in b["views"]["tags"] if t["id"] == b["tid"]), "tag 4",
                         "the ack's blob carries the new row under the returned tid — the dialog opens its rename input there")
        self.assertEqual(store_tag("tag 2")["id"], a["tid"])

    def test_a_rename_to_an_existing_name_is_refused_and_no_gesture_in_the_refusal_window_reaches_the_other_tag(self):
        """Addressed by NAME, a recolor queued behind a refused rename went looking for the
        name the rename would have given the tag — and found the OTHER tag that already had it."""
        self.seed()                                                        # web = gA, #3b82f6
        tid = self.edit("w1", {"op": "create", "name": "api", "color": "#54B204"})["tid"]
        r = self.edit("w2", {"op": "rename", "tid": tid, "newName": "web"})
        self.assertEqual((r["ok"], r["error"], r["tid"]), (False, 'a tag named "web" already exists', tid))
        c = self.edit("w3", {"op": "recolor", "tid": tid, "color": "#DD42FF"})   # the gesture queued behind the rename
        self.assertTrue(c["ok"])
        self.assertEqual(store_tag("api")["color"], "#DD42FF", "lands on the tag the user was editing")
        self.assertEqual(store_tag("web")["color"], "#3b82f6", "the tag that owns the refused name is untouched")
        m = self.edit("w4", {"op": "addMember", "tid": tid, "sids": [SID2]})
        self.assertTrue(m["ok"])
        self.assertEqual([x["sid"] for x in store_tag("web")["members"]], [SID1], "…by every op in the window")

    def test_recolor_remove_member_and_delete(self):
        self.seed()
        self.edit("w1", {"op": "addMember", "tid": "gA", "sid": SID2})
        self.assertEqual(len(store_tag("web")["members"]), 2, "a single `sid` spelling works beside `sids`")
        a = self.edit("w2", {"op": "recolor", "tid": "gA", "color": "#DD42FF"})
        self.assertTrue(a["ok"])
        self.assertEqual(store_tag("web")["color"], "#DD42FF")
        a = self.edit("w3", {"op": "removeMember", "tid": "gA", "sids": [SID1]})
        self.assertTrue(a["ok"])
        self.assertEqual([m["sid"] for m in store_tag("web")["members"]], [SID2])
        a = self.edit("w4", {"op": "delete", "tid": "gA"})
        self.assertEqual((a["ok"], a["views"]["tags"], a["tid"]), (True, [], "gA"))
        self.assertEqual(self.notices, [])

    def test_a_create_with_members_is_one_op_the_join_menus_new_tag_input(self):
        self.seed()
        a = self.edit("w1", {"op": "create", "name": "qa", "color": "#54B204", "sids": [SID2, SID3]})
        self.assertTrue(a["ok"])
        self.assertEqual((a["name"], store_tag("qa")["id"]), ("qa", a["tid"]))
        self.assertEqual(sorted(m["sid"] for m in store_tag("qa")["members"]), sorted([SID2, SID3]))

    def test_a_refused_edit_acks_a_plain_reason_and_changes_nothing(self):
        self.seed()
        api = self.edit("w1", {"op": "create", "name": "api", "color": "#54B204"})["tid"]
        before = json.dumps(km._timeline_views(), sort_keys=True)
        gone = "that tag no longer exists — it may have been deleted from another dashboard"
        cases = [
            ({"op": "rename", "tid": api, "newName": "web"}, 'a tag named "web" already exists'),
            ({"op": "rename", "tid": "gghost", "newName": "x"}, gone),
            ({"op": "create", "name": "web", "color": "#000000"}, 'a tag named "web" already exists'),
            ({"op": "addMember", "tid": "gghost", "sids": [SID2]}, gone),
            ({"op": "addMember", "tid": "gA", "sids": []}, "the edit named no session"),
            ({"op": "addMember", "sids": [SID2]}, "the edit named no tag"),
            ({"op": "recolor", "tid": "gA"}, "no color given"),
            ({"op": "rename", "tid": "gA", "newName": "   "}, "the new name is empty"),
            ({"op": "delete", "tid": "gghost"}, gone),
            ({"op": "explode", "tid": "gA"}, 'unknown tag edit "explode"'),
            (None, "the edit is missing"),
            ("web", "the edit is missing"),
        ]
        for i, (edit, why) in enumerate(cases):
            a = self.edit("r%d" % i, edit)
            self.assertEqual((a["type"], a["writeId"], a["ok"]), ("tagEditAck", "r%d" % i, False), edit)
            self.assertEqual(a["error"], why, edit)
            self.assertIn("views", a, "a refusal still carries the store's blob — the poster's revert base")
            self.assertEqual(json.dumps(km._timeline_views(), sort_keys=True), before, "a refused edit writes nothing")
        # a tag that no longer exists is REFUSED, never resurrected: the dialog's rename of a tag
        # another dashboard deleted must not quietly mint a new one under the old id
        self.assertIsNone(next((t for t in km._timeline_views()["tags"] if t["id"] == "gghost"), None))

    def test_the_message_carries_the_op_nested_under_edit_and_no_top_level_field_is_read(self):
        """The op's fields sit under `edit` so the message has no top-level `name` or `session`
        (federation routes those as a remote lane's address). A stray top-level field is ignored."""
        self.seed()
        a = self.post({"type": "tagEdit", "writeId": "w1", "name": "web", "session": SID2,
                       "edit": {"op": "create", "color": "#000000"}})
        self.assertEqual((a["ok"], a["name"]), (True, "tag 1"), "the create read nothing from the top level")
        self.assertEqual(store_tag("web")["members"], [{"host": "", "sid": SID1}], "…and touched no other tag")

    def test_a_handler_exception_is_acked_as_a_refusal_never_left_unanswered(self):
        """An unanswered write would pin the poster's optimistic copy: the arm acks the failure."""
        self.seed()
        real = km._apply_tag_edit

        def boom(e):
            raise RuntimeError("disk on fire")
        km._apply_tag_edit = boom
        try:
            a = self.edit("w1", {"op": "create", "color": "#000000"})
        finally:
            km._apply_tag_edit = real
        self.assertEqual((a["type"], a["writeId"], a["ok"]), ("tagEditAck", "w1", False))
        self.assertEqual(a["error"], "the edit failed on the kernel: disk on fire")
        self.assertIn("views", a)

    def test_the_ack_answers_the_posting_socket_only(self):
        self.seed()
        other_sent = []
        other = {"app": "timeline", "wid": "w-other", "alive": True, "send": lambda s: other_sent.append(json.loads(s))}
        n = len(self.sent)
        self.edit("w9", {"op": "create", "name": "api", "color": "#54B204"}, client=other)
        self.assertEqual([m["type"] for m in other_sent], ["tagEditAck"])
        self.assertEqual(len(self.sent), n, "no other socket sees the ack — their pushed frames are unchanged")
        # …and a send failure on the poster's socket never escapes the handler (the client is dead, not the kernel)
        def boom(s):
            raise OSError("client gone")
        dead = {"app": "timeline", "wid": "w-dead", "alive": True, "send": boom}
        self.edit("w10", {"op": "create", "name": "qa", "color": "#54B204"}, client=dead)
        self.assertIsNotNone(store_tag("qa"), "the edit landed even though the ack could not be delivered")

    def test_an_edit_without_a_write_id_is_still_acked_with_a_null_id(self):
        self.seed()
        a = self.post({"type": "tagEdit", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        self.assertEqual((a["ok"], a["writeId"]), (True, None))
        self.assertTrue(store_tag("api")["id"].startswith("g"), "the kernel mints the id, /tag's shape")

    def test_a_move_is_one_write_both_halves_or_neither(self):
        """The `move` op (no client gesture posts it yet; a tab strip's "Move to <tag>" row is the intended
        caller): off the source tag, onto the target, as ONE write under the lock — a refused destination
        leaves the source's membership exactly as it was, and the store moves its write sequence once per
        move."""
        self.seed()                                                        # web = gA holding SID1
        api = self.edit("w1", {"op": "create", "name": "api", "color": "#54B204"})["tid"]
        writes, real = [], km._set_timeline_views
        # the RMW doors hand the setter their proved snapshot as `base=` (one read per write), so the
        # counting stub forwards the setter's keywords
        km._set_timeline_views = lambda blob, **kw: (writes.append(1), real(blob, **kw))[1]
        try:
            a = self.edit("w2", {"op": "move", "tid_from": "gA", "tid_to": api, "sid": SID1})
        finally:
            km._set_timeline_views = real
        self.assertEqual((a["ok"], a["tid"], a["name"]), (True, api, "api"))
        self.assertEqual(store_tag("web")["members"], [], "off the source tag…")
        self.assertEqual([m["sid"] for m in store_tag("api")["members"]], [SID1], "…onto the target")
        self.assertEqual(len(writes), 1, "ONE store write for the move, not one per half")
        # a refused DESTINATION: nothing moves — the source keeps the session
        before = json.dumps(km._timeline_views(), sort_keys=True)
        r = self.edit("w3", {"op": "move", "tid_from": api, "tid_to": "gghost", "sid": SID1})
        self.assertFalse(r["ok"])
        self.assertIn("move into", r["error"])
        self.assertEqual([m["sid"] for m in store_tag("api")["members"]], [SID1], "the source membership is intact")
        self.assertEqual(json.dumps(km._timeline_views(), sort_keys=True), before, "nothing was written")
        # a refused SOURCE, and the two malformed shapes
        r = self.edit("w4", {"op": "move", "tid_from": "gghost", "tid_to": "gA", "sid": SID1})
        self.assertEqual((r["ok"], "move out of" in r["error"]), (False, True))
        self.assertEqual(store_tag("web")["members"], [], "the destination is untouched")
        self.assertEqual(self.edit("w5", {"op": "move", "tid_from": api, "tid_to": "gA"})["error"], "the edit named no session")
        self.assertEqual(self.edit("w6", {"op": "move", "tid_to": "gA", "sid": SID1})["error"], "the edit named no tag")
        self.assertEqual(json.dumps(km._timeline_views(), sort_keys=True), before)
        # a move onto a tag already holding the session, off one that does not: still one clean write
        m = self.edit("w7", {"op": "move", "tid_from": "gA", "tid_to": api, "sids": [SID1]})
        self.assertTrue(m["ok"])
        self.assertEqual([x["sid"] for x in store_tag("api")["members"]], [SID1], "no duplicate member")
        self.assertEqual(self.notices, [], "a move is built from the store — never judged stale")

    def test_the_tag_route_still_addresses_by_name_and_creates_on_first_use(self):
        """_edit_tag's name path is unchanged for `romp tag`: a missing name is created on any
        edit, an existing one is edited, a duplicate name refuses rather than guesses."""
        self.seed()
        t, err = km._edit_tag("qa", add=[SID2])
        self.assertIsNone(err)
        self.assertEqual((t["name"], t["members"]), ("qa", [SID2]))
        t, err = km._edit_tag("qa", color="#DD42FF")
        self.assertEqual((err, t["color"]), (None, "#DD42FF"))


class WholeBlobAcks(_Wire):

    def test_a_clean_whole_blob_write_acks_ok_with_no_refusals(self):
        served = self.seed()
        nv = json.loads(json.dumps(served))
        nv["actives"] = {"timeline": {"tags": ["web"]}}                # a lens edit, built from the echo
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": nv})
        self.assertEqual((a["type"], a["writeId"], a["ok"], a["refused"]), ("viewsAck", "w1", True, []))
        self.assertEqual(a["views"]["actives"]["timeline"], {"tags": ["web"]})
        self.assertNotIn("error", a)
        self.assertEqual(self.notices, [])

    def test_a_genuinely_stale_whole_blob_gets_a_refusal_ack_naming_the_tag_and_the_rest_lands(self):
        served = self.seed()
        stale = json.loads(json.dumps(served))                           # this dashboard's copy…
        time.sleep(1.1)                                                  # int-second stamps: land in a later second
        t, err = km._edit_tag("web", add=[SID2])                         # …then another writer adds a member
        self.assertIsNone(err)
        stale["actives"] = {"timeline": {"tags": ["web"]}}               # the stale dashboard changes its lens…
        stale["tags"][0]["members"] = []                                 # …and its copy of web has no members at all
        a = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale})
        self.assertEqual((a["type"], a["writeId"], a["ok"]), ("viewsAck", "w2", False))
        self.assertEqual([r["name"] for r in a["refused"]], ["web"])
        self.assertIn("predates", a["refused"][0]["reason"])
        self.assertIn('"web"', a["error"], "one plain sentence the dialog can show as-is")
        v = a["views"]
        self.assertEqual(sorted(next(x for x in v["tags"] if x["id"] == "gA")["members"]), sorted([SID1, SID2]),
                         "the store's newer members stand — the guard is unchanged")
        self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]}, "the rest of the write (the lens) landed")
        # the guard's other two witnesses still fire — the ack is an addition, not a replacement
        self.assertEqual(len(self.notices), 1)
        self.assertFalse(self.notices[0][1])
        self.assertIn("stale dashboard write", self.notices[0][0])

    def test_a_refusal_on_a_tag_the_client_did_not_edit_is_ok_with_the_refusal_listed(self):
        """A lens change from a dashboard that had slept through another's tag edit was
        acked as a refusal and toasted — for a tag the user never touched. `edited` names the tag
        ids the write changed; a refusal outside them is a stale copy, not a lost edit. A
        lens write (`edited` empty) changes no tag at all, so its stale copy is not even judged —
        the ack is clean and its blob carries the store's web; a write naming ANOTHER tag as edited
        still lists the untouched tag's kept copy."""
        served = self.seed()
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        km._edit_tag("web", add=[SID2])                                   # another writer edits web
        stale["actives"] = {"timeline": {"tags": ["web"]}}                # this dashboard changes only its lens…
        stale["tags"][0]["members"] = []                                  # …carrying its stale copy of web
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []),
                         "ok, nothing listed: a lens write changes no tag, so there is nothing to refuse")
        self.assertNotIn("error", a, "no one-line refusal to show: there is nothing to tell the user")
        self.assertEqual(a["views"]["actives"]["timeline"], {"tags": ["web"]}, "the lens landed")
        self.assertEqual(sorted(next(t for t in a["views"]["tags"] if t["id"] == "gA")["members"]), sorted([SID1, SID2]),
                         "…and the ack's blob carries the newer web the client adopts")
        # the same stale copy of web riding a write that edits a DIFFERENT tag: web is kept, listed, ok
        c0 = self.post({"type": "tagEdit", "writeId": "w1b", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        w = json.loads(json.dumps(c0["views"]))                                  # a fresh copy (its `at` is the store's)…
        next(t for t in w["tags"] if t["id"] == "gA")["members"] = []            # …whose web differs from the store's anyway
        next(t for t in w["tags"] if t["id"] == c0["tid"])["color"] = "#000000"  # this write recolors api
        a2 = self.post({"type": "setTimelineViews", "writeId": "w1c", "views": w, "edited": [c0["tid"]]})
        self.assertEqual((a2["ok"], [r["tid"] for r in a2["refused"]]), (True, ["gA"]),
                         "ok — nothing the user did was refused — with the kept tag listed")
        self.assertIn("did not edit it", a2["refused"][0]["reason"])
        self.assertNotIn("error", a2)
        self.assertEqual(store_tag("api")["color"], "#000000", "the edited tag landed")
        self.assertEqual(sorted(m["sid"] for m in store_tag("web")["members"]), sorted([SID1, SID2]),
                         "the untouched tag was kept, whatever the stamps say (the copy is not stale by them)")
        self.assertIsNone(km._edit_tag(tid=c0["tid"], delete=True)[1])
        # the same write naming web as EDITED is a lost edit: refused, with the reason
        b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale, "edited": ["gA"]})
        self.assertFalse(b["ok"])
        self.assertIn('"web"', b["error"])
        # no `edited` at all (an older client): the strict reading stands
        c = self.post({"type": "setTimelineViews", "writeId": "w3", "views": stale})
        self.assertFalse(c["ok"])

    def test_the_notice_is_filed_only_when_the_poster_edited_the_kept_tag(self):
        """The 2026-09-05 review: the benign case — a kept tag outside `edited`, acked ok —
        still filed a red "reload that dashboard to resync" notice plus stderr for every kept tag.
        Now the notice follows the ack's verdict: a lost edit is loud; a stale copy of an untouched tag
        is one quiet stderr line and nothing on the dashboard. A lens write (`edited` empty)
        changes no tag and logs nothing at all — the quiet line is for a write that names other tags."""
        import contextlib
        import io
        served = self.seed()
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        km._edit_tag("web", add=[SID2])
        stale["actives"] = {"timeline": {"tags": ["web"]}}
        stale["tags"][0]["members"] = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []))
        self.assertEqual(self.notices, [], "nothing the user did was refused, so no notice")
        self.assertEqual([ln for ln in err.getvalue().splitlines() if ln.strip()], [],
                         "a lens write is the normal path: nothing is logged")
        # the same stale web riding a write that edits another tag: one quiet stderr line, no notice
        c0 = self.post({"type": "tagEdit", "writeId": "w1b", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        w = json.loads(json.dumps(c0["views"]))
        next(t for t in w["tags"] if t["id"] == "gA")["members"] = []
        next(t for t in w["tags"] if t["id"] == c0["tid"])["color"] = "#000000"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a2 = self.post({"type": "setTimelineViews", "writeId": "w1c", "views": w, "edited": [c0["tid"]]})
        self.assertEqual((a2["ok"], [r["tid"] for r in a2["refused"]]), (True, ["gA"]))
        self.assertEqual(self.notices, [], "nothing the user did was refused, so no notice")
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, "one quiet stderr line is the whole record")
        self.assertIn('"web" (differing copy)', lines[0], "the quiet label carries its cause like every other")
        self.assertNotIn("reload that dashboard", lines[0])
        self.assertIsNone(km._edit_tag(tid=c0["tid"], delete=True)[1])
        # the same write naming web as edited: the lost-edit notice, as before
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale, "edited": ["gA"]})
        self.assertFalse(b["ok"])
        self.assertEqual(len(self.notices), 1)
        self.assertFalse(self.notices[0][1])
        self.assertIn('"web"', self.notices[0][0])
        self.assertIn("reload that dashboard", self.notices[0][0])
        # a mixed write — one kept tag edited, one not — names only the edited one on the dashboard
        self.notices.clear()
        self.assertTrue(self.post({"type": "tagEdit", "writeId": "w3", "edit": {"op": "create", "name": "api", "color": "#54B204", "sids": [SID3]}})["ok"])
        stale2 = json.loads(json.dumps(km._views_client()))
        time.sleep(1.1)
        km._edit_tag("web", color="#DD42FF")
        km._edit_tag("api", color="#DD42FF")
        for t in stale2["tags"]:
            t["color"] = "#000000"
        api_tid = next(t["id"] for t in stale2["tags"] if t["name"] == "api")
        with contextlib.redirect_stderr(io.StringIO()):
            c = self.post({"type": "setTimelineViews", "writeId": "w4", "views": stale2, "edited": [api_tid]})
        self.assertFalse(c["ok"])
        self.assertEqual(sorted(r["name"] for r in c["refused"]), ["api", "web"], "both kept; the ack lists both")
        self.assertEqual(len(self.notices), 1)
        self.assertIn('"api"', self.notices[0][0])
        self.assertNotIn('"web"', self.notices[0][0], "the untouched tag is not the user's problem")

    def test_every_quiet_label_carries_its_cause(self):
        """The 2026-09-05 review: the quiet stderr line labelled a kept deletion "(deletion)"
        and an unread copy "(unread)", but a differing copy of an unedited tag stood bare beside them —
        the one label without a cause. Now it reads "(differing copy)", so the line is uniform."""
        self.seed()
        api = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        tests = self.post({"type": "tagEdit", "writeId": "w2", "edit": {"op": "create", "name": "tests", "color": "#54B204"}})
        w = json.loads(json.dumps(tests["views"]))
        next(t for t in w["tags"] if t["id"] == "gA")["members"] = []                 # web: a differing copy…
        w["tags"] = [t for t in w["tags"] if t["id"] != api["tid"]]                    # api: absent, a deletion…
        next(t for t in w["tags"] if t["id"] == tests["tid"])["color"] = "#000000"    # …and this write edits tests only
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = self.post({"type": "setTimelineViews", "writeId": "w3", "views": w, "edited": [tests["tid"]]})
        self.assertTrue(a["ok"], "nothing the user did was refused")
        self.assertEqual(sorted(r["tid"] for r in a["refused"]), sorted(["gA", api["tid"]]))
        self.assertEqual(self.notices, [])
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(lines, ["romp-kernel: kept the store's copy of 2 tags over a dashboard's stale copy (tags that "
                                 "dashboard did not edit; the ack carries the newer state): "
                                 '"api" (deletion), "web" (differing copy)'],
                         "every label on the quiet line says why")
        self.assertEqual(store_tag("tests")["color"], "#000000", "the edited tag landed")
        self.assertIsNotNone(store_tag("api"), "the unedited absence was not a deletion")

    def test_a_stale_copy_cannot_resurrect_a_tag_deleted_elsewhere_but_a_named_create_lands(self):
        """The 2026-09-05 review, a pre-existing hole: the guard refused a stale DELETION
        (a tag absent from the copy) but not a stale RESURRECTION (a tag absent from the store) — an
        incoming unknown tag was always kept as new. With `edited`, the two creates a whole-blob write
        can carry are distinguishable: a tag the client did not name as edited is something another
        dashboard deleted after the copy was taken, and is kept out; a tag it did name is a genuine
        create (the legacy path's client-minted id); a write without `edited` keeps the old reading.
        A lens write (`edited` empty) changes no tag, so the deleted tag is not even judged —
        the re-creation refusal is for a write that names OTHER tags as edited."""
        import contextlib
        import io
        self.seed()
        c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204", "sids": [SID2]}})
        api_tid = c["tid"]
        stale = json.loads(json.dumps(c["views"]))                  # this dashboard's copy: web and api
        self.assertIsNone(km._edit_tag(tid=api_tid, delete=True)[1])   # another dashboard deletes api
        self.assertIsNone(store_tag("api"))
        # a lens change from the stale copy changes no tag: api stays deleted, nothing is listed or logged
        stale["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []), "nothing the user did was refused, and no tag was judged")
        self.assertNotIn("error", a)
        self.assertIsNone(store_tag("api"), "the deleted tag stays deleted")
        self.assertEqual([t["name"] for t in a["views"]["tags"]], ["web"], "…and the ack's blob, which the client adopts, has no api")
        self.assertEqual(a["views"]["actives"]["timeline"], {"tags": ["web"]}, "the lens landed")
        self.assertEqual(self.notices, [], "a stale copy of a deleted tag is not the user's problem")
        self.assertEqual([ln for ln in err.getvalue().splitlines() if ln.strip()], [], "nothing logged")
        # the same stale copy riding a write that edits web (a recolor): api is kept OUT with a reason, the write is ok
        stale_w = json.loads(json.dumps(stale))
        next(t for t in stale_w["tags"] if t["id"] == "gA")["color"] = "#000000"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a2 = self.post({"type": "setTimelineViews", "writeId": "w2b", "views": stale_w, "edited": ["gA"]})
        self.assertTrue(a2["ok"], "nothing the user did was refused")
        self.assertEqual([(r["tid"], r["name"]) for r in a2["refused"]], [(api_tid, "api")])
        self.assertEqual(a2["refused"][0]["reason"], "it was deleted after your copy was taken, so it was not re-created")
        self.assertNotIn("error", a2)
        self.assertIsNone(store_tag("api"), "the deleted tag stays deleted")
        self.assertEqual(store_tag("web")["color"], "#000000", "the edited tag landed")
        self.assertEqual(self.notices, [], "a stale copy of a deleted tag is not the user's problem")
        self.assertEqual(len([ln for ln in err.getvalue().splitlines() if ln.strip()]), 1, "one quiet line")
        # the same api, on a copy of the store, named as EDITED: a create (the legacy path's client-minted id), it lands
        api_row = next(t for t in stale["tags"] if t["id"] == api_tid)
        fresh = json.loads(json.dumps(km._views_client()))
        fresh["tags"].append(dict(api_row))
        b = self.post({"type": "setTimelineViews", "writeId": "w3", "views": fresh, "edited": [api_tid]})
        self.assertEqual((b["ok"], b["refused"]), (True, []))
        self.assertEqual(store_tag("api")["id"], api_tid)
        self.assertTrue(store_tag("api").get("mtime"), "a created tag is stamped like any edit")
        # no `edited` at all (an older client): every unknown tag is new, as before
        self.assertIsNone(km._edit_tag(tid=api_tid, delete=True)[1])
        fresh = json.loads(json.dumps(km._views_client()))
        fresh["tags"].append(dict(api_row))
        d = self.post({"type": "setTimelineViews", "writeId": "w4", "views": fresh})
        self.assertEqual((d["ok"], d["refused"]), (True, []))
        self.assertEqual(store_tag("api")["id"], api_tid, "the old reading stands for a client that cannot say")

    def test_the_refusal_names_the_tag_once_and_says_what_was_kept(self):
        """The reason carried the name and the client prefixed it again."""
        served = self.seed()
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        km._edit_tag("web", add=[SID2])
        stale["tags"][0]["members"] = []
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": ["gA"]})
        row = a["refused"][0]
        self.assertNotIn("web", row["reason"], "the reason names no tag — the composer adds the name once")
        self.assertIn("newer state was kept", row["reason"], "…and says what was kept")
        self.assertIn("not applied", row["reason"], "…and what was refused")
        self.assertEqual(a["error"], '"web": %s' % row["reason"])
        self.assertEqual(a["error"].count("web"), 1, "the one-line form names the tag exactly once")
        stale["tags"] = []
        d = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale, "edited": ["gA"]})
        self.assertEqual(d["error"], '"web": it was edited after your copy was taken, so it was not deleted')

    def test_a_setter_exception_is_acked_as_a_refusal_never_left_unanswered(self):
        self.seed()
        real = km._set_timeline_views

        def boom(blob, **kw):
            raise RuntimeError("disk on fire")
        km._set_timeline_views = boom
        try:
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": []}})
        finally:
            km._set_timeline_views = real
        self.assertEqual((a["type"], a["ok"], a["error"]), ("viewsAck", False, "the write failed on the kernel: disk on fire"))

    def test_a_stale_deletion_is_named_as_such(self):
        served = self.seed()
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        km._edit_tag("web", add=[SID2])
        stale["tags"] = []
        a = self.post({"type": "setTimelineViews", "writeId": "w3", "views": stale})
        self.assertFalse(a["ok"])
        self.assertEqual(len(a["refused"]), 1)
        self.assertEqual(a["refused"][0]["name"], "web")
        self.assertIn("not deleted", a["refused"][0]["reason"])
        self.assertEqual([t["name"] for t in a["views"]["tags"]], ["web"])

    def test_the_dialogs_old_burst_is_what_the_whole_blob_door_refuses(self):
        """The mechanism, pinned so the targeted op's reason for existing stays legible: a rename built
        from the client's un-echoed copy of its own create is refused as stale. (The dialog no longer
        posts this shape for tag edits; this is the door's behavior, and the ack now reports it.)"""
        S0 = self.seed()
        time.sleep(1.1)
        P1 = json.loads(json.dumps(S0))                                  # the pending copy: pre-burst `at`
        P1["tags"].append({"id": "gnew1", "name": "tag 2", "color": "#1EA1EB", "members": []})
        a1 = self.post({"type": "setTimelineViews", "writeId": "p1", "views": P1})
        self.assertTrue(a1["ok"], "a new id is never refused")
        P2 = json.loads(json.dumps(P1))                                  # built from the pending, not the echo
        P2["tags"][1]["name"] = "notes-api"
        a2 = self.post({"type": "setTimelineViews", "writeId": "p2", "views": P2})
        self.assertEqual((a2["ok"], [r["name"] for r in a2["refused"]]), (False, ["tag 2"]),
                         "judged stale against the client's OWN previous write — the reported loss")
        self.assertEqual(store_tag("tag 2")["name"], "tag 2")


class EditedBoundsTheWrite(_Wire):
    """The 2026-09-05 review (the HIGH finding): a lens or order write carries `edited: []`
    and is built from the store's blob the client last adopted, and the door still applied its tag
    set as a whole-blob replacement judged by the guard's second-resolution stamps — so a targeted
    edit that landed in the SAME second as that blob's `at` (its mtime equal to the writer's
    evidence, not newer by the guard's clock) was silently reverted by the next lens change: a rename
    undone, a create deleted, a member lost. Now `edited` bounds what a write may change: an empty
    list changes no tag; a list of ids changes those tags only, a differing copy of any other tag
    kept from the store quietly; no `edited` at all keeps the legacy reading."""

    def test_the_reproduction_a_same_second_targeted_edit_survives_a_lens_write(self):
        served = self.seed()                                           # the dashboard's adopted copy: web[SID1]
        lens = json.loads(json.dumps(served))
        # three targeted edits from another surface, landing right after the copy was taken
        self.assertIsNone(km._edit_tag(tid="gA", rename="api")[1])
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID2])[1])
        d, err = km._edit_tag("docs", add=[SID3])
        self.assertIsNone(err)
        store = km._timeline_views()
        # the same-second case made exact rather than left to the test's timing: by the guard's clock
        # the copy's evidence equals the edits' stamps, so nothing about it reads as stale
        lens["at"] = max(t["mtime"] for t in store["tags"])
        lens["actives"] = {"timeline": {"tags": ["api"]}, "chat": {"all": True}, "outline": {"all": True}}
        lens["tagOrder"] = ["docs", "api"]
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": lens, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []))
        self.assertNotIn("error", a)
        web = next(t for t in km._timeline_views()["tags"] if t["id"] == "gA")
        self.assertEqual(web["name"], "api", "the rename survives the lens write")
        self.assertEqual(sorted(m["sid"] for m in web["members"]), sorted([SID1, SID2]), "the member survives")
        self.assertEqual(store_tag("docs")["id"], d["id"], "the create survives")
        self.assertEqual(km._timeline_views()["actives"]["timeline"], {"tags": ["api"]}, "the lens landed")
        self.assertEqual(km._timeline_views()["tagOrder"], ["docs", "api"], "…and the order")
        self.assertEqual(sorted(t["id"] for t in a["views"]["tags"]), sorted(t["id"] for t in store["tags"]),
                         "the ack's blob is the store's tag set (in the write's order)")
        self.assertEqual(self.notices, [], "the normal lens path: nothing said")
        self.assertGreater(a["seq"], store["seq"], "the write moved the store")

    def test_an_order_write_orders_the_stored_array_too_the_pill_drags_contract(self):
        """The 2026-09-05 review: under an empty `edited` the door copied the store's tags
        in STORE order and discarded the posted array order, so a pill drag moved tagOrder and left
        the array as it was — a reader without this kernel's tagOrder (a peer's dashboard) kept
        showing the old order, and the clients' comments promised a re-sort that never landed. The
        array order is an order field, the write's to set: it follows the write's tagOrder; names the
        order lacks trail in store order."""
        self.seed()                                                        # web (gA)
        api, err = km._edit_tag("api", add=[SID2])
        self.assertIsNone(err)
        docs, err = km._edit_tag("docs", add=[SID3])
        self.assertIsNone(err)
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["web", "api", "docs"])
        drag = json.loads(json.dumps(km._views_client()))
        drag["tagOrder"] = ["docs", "web", "remote-only"]                   # the drop: a union order, a remote name included
        drag["tags"] = list(reversed(drag["tags"]))                         # the client's own re-sort: ignored, the order decides
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": drag, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []))
        store = km._timeline_views()
        self.assertEqual([t["name"] for t in store["tags"]], ["docs", "web", "api"],
                         "the array follows the order; the name the order lacks trails in store order")
        self.assertEqual(store["tagOrder"], ["docs", "web", "remote-only"])
        self.assertEqual([t["name"] for t in a["views"]["tags"]], ["docs", "web", "api"], "the ack's blob agrees")
        self.assertEqual([t["mtime"] for t in store["tags"]].count(None), 0)
        self.assertTrue(all(t["id"] in ("gA", api["id"], docs["id"]) for t in store["tags"]), "the same three tags: nothing changed but order")
        # a lens write carrying the same order keeps the array where it is
        lens = json.loads(json.dumps(a["views"]))
        lens["actives"] = {"timeline": {"tags": ["api"]}, "chat": {"all": True}, "outline": {"all": True}}
        b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": lens, "edited": []})
        self.assertEqual((b["ok"], [t["name"] for t in km._timeline_views()["tags"]]), (True, ["docs", "web", "api"]))
        self.assertEqual(self.notices, [])

    def test_a_lens_write_cannot_store_a_dangling_active_it_is_validated_against_the_tags_that_stand(self):
        """The 2026-09-05 review: the normalizer validated `active` against the POSTED
        blob's tags, so a copy whose active named a tag deleted elsewhere wrote that id to disk under
        an empty `edited` (the store's tags stood; the blob's active did not point at any of them).
        Served as "all" by every read's re-normalization, but stored wrong. Validated against what
        stands now — and the lens, order and active fields still land whole, last writer wins."""
        served = self.seed()
        d, err = km._edit_tag("docs", add=[SID2])                           # a second tag…
        self.assertIsNone(err)
        stale = json.loads(json.dumps(km._views_client()))
        self.assertIsNone(km._edit_tag(tid=d["id"], delete=True)[1])       # …deleted elsewhere after the copy was taken
        stale["active"] = d["id"]                                          # the copy still selects it
        stale["actives"] = {"timeline": {"tags": ["docs"]}, "chat": {"all": True}, "outline": {"all": True}}
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []))
        on_disk = json.loads(km._views_path().read_text())
        self.assertEqual(on_disk["active"], "all", "no dangling id on disk: the active named no tag that stands")
        self.assertEqual([t["id"] for t in on_disk["tags"]], ["gA"], "the store's tags stood")
        self.assertEqual(on_disk["actives"]["timeline"], {"tags": ["docs"]},
                         "the lens lands whole, unknown name and all (it may exist on a linked kernel): last writer wins")
        self.assertEqual(self.notices, [])

    def test_a_write_naming_its_edited_tags_changes_those_only(self):
        self.seed()
        c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        b_tid = c["tid"]
        w = json.loads(json.dumps(c["views"]))                          # a fresh copy: not stale by any stamp
        for t in w["tags"]:
            t["color"] = "#000000"                                       # …recoloring BOTH tags…
        a = self.post({"type": "setTimelineViews", "writeId": "w2", "views": w, "edited": [b_tid]})   # …claiming only B
        self.assertEqual((a["ok"], [r["tid"] for r in a["refused"]]), (True, ["gA"]))
        self.assertNotIn("error", a)
        self.assertEqual(store_tag("api")["color"], "#000000", "B, the edited tag, is applied")
        self.assertEqual(store_tag("web")["color"], "#3b82f6", "A is kept from the store, whatever the copy says")
        self.assertEqual(self.notices, [], "a kept tag outside `edited` is nobody's lost edit: quiet")
        # a tag omitted by a write that did not edit it is not deleted either
        w2 = json.loads(json.dumps(a["views"]))
        w2["tags"] = [t for t in w2["tags"] if t["id"] == b_tid]
        w2["tags"][0]["color"] = "#DD42FF"
        a2 = self.post({"type": "setTimelineViews", "writeId": "w3", "views": w2, "edited": [b_tid]})
        self.assertEqual((a2["ok"], [(r["tid"], r["reason"]) for r in a2["refused"]]),
                         (True, [("gA", "this write did not edit it, so it was not deleted")]))
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["api", "web"])
        self.assertEqual(store_tag("api")["color"], "#DD42FF")
        self.assertEqual(self.notices, [])

    def test_a_write_without_edited_keeps_the_legacy_reading(self):
        served = self.seed()
        w = json.loads(json.dumps(served))
        w["tags"][0]["name"] = "api"                                    # a fresh copy renames web…
        w["tags"].append({"id": "gnew", "name": "docs", "color": "#DD42FF", "members": [SID2]})   # …and creates docs
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w})
        self.assertEqual((a["ok"], a["refused"]), (True, []), "a fresh whole-blob write lands whole, as before")
        self.assertEqual(store_tag("api")["id"], "gA")
        self.assertEqual(store_tag("docs")["id"], "gnew")
        # …and a stale one is judged by the stamps alone, loudly
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID3])[1])
        b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": stale})
        self.assertFalse(b["ok"])
        self.assertIn("gA", [r["tid"] for r in b["refused"]])
        self.assertTrue(self.notices and not self.notices[0][1], "loud, as before")


class WriteSequence(_Wire):
    """The store's WRITE SEQUENCE (the 2026-09-05 review): every accepted write
    moves `seq` forward, the blob carries it, so every frame that embeds the blob (the timeline
    skeleton, the feed frame, the tabOrder frames — all built from _views_client) and every ack
    carries the same number. A client adopts a blob only when its seq is at least the one it
    holds, so the order the socket delivered frames and acks in decides nothing: the pusher's
    warmed cache can predate a write whose ack already arrived, and the exact-echo heuristic could
    not tell that frame from the write's own echo."""

    def test_every_accepted_write_moves_the_seq_forward_and_a_refusal_does_not(self):
        served = self.seed()
        s0 = served.get("seq")
        self.assertIsInstance(s0, int, "the served blob carries the stamp")
        _, err = km._edit_tag("web", add=[SID2])                            # a targeted (RMW) write
        self.assertIsNone(err)
        s1 = km._timeline_views()["seq"]
        self.assertGreater(s1, s0, "a targeted write moves it")
        nv = json.loads(json.dumps(km._views_client()))
        nv["actives"] = {"timeline": {"tags": ["web"]}}
        a = self.post({"type": "setTimelineViews", "writeId": "w2", "views": nv})
        self.assertTrue(a["ok"])
        self.assertGreater(a["seq"], s1, "a whole-blob write moves it, and the ack returns the seq the write produced")
        self.assertEqual(a["seq"], a["views"]["seq"], "the ack's seq IS the blob's")
        self.assertEqual(a["seq"], km._timeline_views()["seq"])
        _, err = km._edit_tag("ghost", rename="x", exists=True)              # refused: no write
        self.assertTrue(err)
        self.assertEqual(km._timeline_views()["seq"], a["seq"], "a refused edit writes nothing, so it moves nothing")
        # a PARTIALLY refused whole-blob write still lands the rest of the write — so it moves
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        km._edit_tag("web", color="#DD42FF")
        s2 = km._timeline_views()["seq"]
        stale["tags"][0]["members"] = []
        stale["actives"] = {"chat": {"tags": ["web"]}}
        b = self.post({"type": "setTimelineViews", "writeId": "w3", "views": stale})
        self.assertEqual([r["name"] for r in b["refused"]], ["web"])
        self.assertGreater(b["seq"], s2, "the lens landed, so the store moved")
        self.assertEqual(b["views"]["seq"], b["seq"])

    def test_the_seq_rides_every_frame_that_carries_the_blob_and_survives_the_normalizer(self):
        served = self.seed()
        self.assertEqual(km._views_client()["seq"], served["seq"],
                         "the rendered client blob every frame embeds carries it")
        self.assertEqual(km._norm_timeline_views(json.loads(json.dumps(served)))["seq"], served["seq"],
                         "clients echo the blob wholesale — the stamp survives the round trip")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertGreaterEqual(src.count('**_views_payload()'), 3,
                                "the timeline skeleton, the feed frame and the tabOrder frames all embed the "
                                "rendered blob — one carrier (_views_payload since 2026-09-08: the blob, marked "
                                "under a read fault, or the marker alone), so the seq rides every one of them")

    def test_a_store_recreated_from_nothing_starts_past_what_a_connected_client_holds(self):
        """The seq is seeded from the clock, then +1 per write: a deleted views file (or a new
        state dir) must not restart at 1 — a dashboard holding the old store's seq would then
        ignore every frame until it reloaded."""
        s0 = self.seed()["seq"]
        km._views_path().unlink()
        time.sleep(0.002)
        a = self.post({"type": "setTimelineViews", "writeId": "w9", "views": {"active": "all", "tags": []}})
        self.assertGreater(a["seq"], s0)
        b = self.post({"type": "setTimelineViews", "writeId": "w10", "views": a["views"]})
        self.assertGreater(b["seq"], a["seq"], "…and strictly increasing within a store, same-ms writes included")


class Capability(_Wire):
    """The kernel says what it can do (the 2026-09-05 review): {type: "caps"} in
    reply to every `ready`, after the pushes; the same list on /version. A message no handler took
    is logged once per type and answered {type: "unknownOp", op, writeId?} on the poster's socket,
    so a client waiting on an ack learns the op is unsupported instead of pinning its copy."""

    def setUp(self):
        super().setUp()
        seen = set(km._UNKNOWN_OPS_SEEN)
        self.addCleanup(lambda: (km._UNKNOWN_OPS_SEEN.clear(), km._UNKNOWN_OPS_SEEN.update(seen)))
        km._UNKNOWN_OPS_SEEN.clear()

    def test_ready_is_answered_with_the_caps_frame_after_the_pushes(self):
        self.handler._push_one = lambda c: self.sent.append({"type": "_pushed"})   # the connect push, in order
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        types = [m["type"] for m in self.sent]
        self.assertIn("caps", types)
        self.assertLess(types.index("_pushed"), types.index("caps"),
                        "the caps frame follows the push: the shim's stale banner clears on the first real frame "
                        "after a reconnect, which must stay the resync frame itself")
        self.assertNotIn("tabOrder", types, "no strip from the handler itself: the connect push is the one source")
        caps = next(m for m in self.sent if m["type"] == "caps")
        self.assertEqual(caps, {"type": "caps", "caps": ["tagEdit", "chatProto2"], "viewsSeq": None},
                         "no store exists yet: the stubbed push carried no seq and the store has none; viewsSeq is null, "
                         "the key always present")
        # a RE-SENT ready (the shim, on a reconnected socket) gets the caps again — the event a page
        # with writes in flight across the drop keys on
        n = len(self.sent)
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        self.assertEqual([m["type"] for m in self.sent[n:] if m["type"] == "caps"], ["caps"])

    def test_version_lists_the_same_caps(self):
        self.assertEqual(km._version_info()["caps"], list(km.KERNEL_WS_CAPS))
        self.assertIn("tagEdit", km.KERNEL_WS_CAPS)

    def test_an_unknown_op_is_answered_every_time_and_logged_once_per_type(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = self.post({"type": "noSuchOp", "writeId": "w1"})
            b = self.post({"type": "noSuchOp", "writeId": "w2"})
            c = self.post({"type": "noSuchOp"})
        self.assertEqual(a, {"type": "unknownOp", "op": "noSuchOp", "writeId": "w1"},
                         "the poster learns the op is unsupported — a client waiting on that writeId treats it as a refusal")
        self.assertEqual(b, {"type": "unknownOp", "op": "noSuchOp", "writeId": "w2"})
        self.assertEqual(c, {"type": "unknownOp", "op": "noSuchOp"}, "no writeId → none echoed")
        lines = [ln for ln in err.getvalue().splitlines() if "noSuchOp" in ln]
        self.assertEqual(len(lines), 1, "one line of version skew per type, not one per gesture")
        self.assertIn("unknownOp", lines[0])
        # a KNOWN op missing the field its arm requires falls to the same terminal arm
        with contextlib.redirect_stderr(io.StringIO()):
            d = self.post({"type": "setSessionFlag", "id": "", "flag": "eye"})
        self.assertEqual(d["type"], "unknownOp")
        self.assertEqual(d["op"], "setSessionFlag")

    def test_a_host_directed_op_and_an_undecodable_frame_are_neither_logged_nor_answered(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.post({"type": "openLink", "href": "https://example.invalid/x"}),
                              "the browser shim has no host to consume it; it is not the kernel's to answer")
            self.assertIsNone(self.post({"type": "readClipboard"}))
            self.assertIsNone(self.post(None), "an undecodable frame was already reported by the recv loop")
            self.assertIsNone(self.post({"type": ""}))
        self.assertEqual(err.getvalue(), "")

    def _ready(self):
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        return next(m for m in reversed(self.sent) if m["type"] == "caps")

    def test_the_caps_frame_carries_the_seq_of_the_connect_pushs_own_views_blob_not_a_fresh_read(self):
        """The 2026-09-05 review: a client that kept the blob its seq gate last turned away
        adopts it on the caps frame; it must do so only when that blob IS the connect push's (the
        restore case), so the frame names the connect push's seq — read from the frame the push
        enqueued, not from the store, which a write between the push and the caps frame moves on."""
        s0 = self.seed()["seq"]

        def push(c):                                     # the connect push's tabOrder frame, then a write lands before caps
            km._send_client(c, ("taborder",), {"type": "tabOrder", "order": [], "tabs": [], "views": km._views_client()})
            self.assertIsNone(km._edit_tag(tid="gA", add=[SID2])[1])
        self.handler._push_one = push
        caps = self._ready()
        s1 = km._views_client()["seq"]
        self.assertGreater(s1, s0, "the write moved the store on after the frame the push served")
        seqs = [m["views"]["seq"] for m in self.sent if m["type"] == "tabOrder"]
        self.assertEqual(seqs, [s0], "one strip, the connect push's, under the seq it was built with")
        self.assertEqual(caps["viewsSeq"], s0, "the seq the push served, not the store's current seq")
        self.assertEqual([m["type"] for m in self.sent if m["type"] in ("tabOrder", "caps")], ["tabOrder", "caps"])

    def test_a_pusher_thread_frame_enqueued_between_the_push_and_the_caps_is_not_what_the_caps_names(self):
        """The residual race the client cannot see: a pusher-thread frame built before a concurrent write
        is enqueued after the handler's connect push and before its caps frame. The client rejects it
        and keeps it; caps must not name its seq, or the client would adopt a stale blob."""
        served = self.seed()
        stale = json.loads(json.dumps(served))           # the pusher's frame, built before the write below
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID2])[1])
        s_new = km._views_client()["seq"]

        def push(c):
            km._send_client(c, ("taborder",), {"type": "tabOrder", "order": [], "tabs": [], "views": km._views_client()})
            t = threading.Thread(target=lambda: km._send_client(
                c, ("taborder",), {"type": "tabOrder", "order": ["x"], "tabs": [], "views": stale}))
            t.start(); t.join()
        self.handler._push_one = push
        caps = self._ready()
        seqs = [m["views"]["seq"] for m in self.sent if m["type"] == "tabOrder"]
        self.assertEqual(seqs, [s_new, served["seq"]], "the stale frame went out after the connect push's, before the caps")
        self.assertEqual(caps["viewsSeq"], s_new, "the caps names the connect push's seq, never the pusher thread's")
        self.assertNotEqual(caps["viewsSeq"], served["seq"])

    def test_the_timeline_skeletons_nested_views_blob_is_read_and_a_push_without_one_names_the_stores_seq(self):
        s0 = self.seed()["seq"]
        self.handler._push_one = lambda c: km._send_client(
            c, ("timeline",), {"type": "data", "data": {"sessions": [], "views": km._views_client()}})
        self.assertEqual(self._ready()["viewsSeq"], s0, "the skeleton carries views under `data`")
        self.handler._push_one = lambda c: km._send_client(c, ("working",), {"type": "working", "names": []})
        self.assertEqual(self._ready()["viewsSeq"], s0,
                         "a push that served no views blob: the store's current seq, the one the next push serves "
                         "(null here left a page's gate armed at a pre-restore seq)")
        km._views_path().unlink()
        km._flags_cache.clear()
        self.assertIsNone(self._ready()["viewsSeq"], "no store at all: nothing has a seq, null")
        self.assertIsNone(km._views_seq_of({"type": "tabOrder", "views": {"tags": []}}), "a seq-less blob: null")
        self.assertIsNone(km._views_seq_of({"type": "tabOrder", "views": {"seq": "x"}}))
        self.assertEqual(km._views_seq_of({"type": "tabOrder", "views": {"seq": 7}}), 7)

    def test_the_real_connect_push_and_the_caps_frame_agree_on_the_seq(self):
        """End to end through the real _push (the test_tab_meta_push.py stubs): the tabOrder frame the
        connect push sends a chat page carries the same seq the caps frame that follows it names."""
        import tempfile as _tf
        from pathlib import Path as _P
        s0 = self.seed()["seq"]
        tmp = _tf.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        names = _P(tmp) / "names"
        names.mkdir()
        (names / SID1).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        saved = (km.NAMES, km._live_map, km._mark_views_dirty, km._chat_tab_sessions, km._cached_feed)
        try:
            km.NAMES = names
            km._live_map = lambda: {}
            km._mark_views_dirty = lambda: None
            km._chat_tab_sessions = lambda now, live: [{"sid": SID1, "name": "web", "path": os.path.join(tmp, "none.jsonl"),
                                                         "anchor": SID1}]
            km._cached_feed = lambda *a, **k: None
            self.client["app"] = "chat"
            self.client["sent"] = {}
            caps = self._ready()
            tab = [m for m in self.sent if m["type"] == "tabOrder"]
            self.assertEqual(len(tab), 1, "the real connect push's tabOrder frame, the one strip a ready produces")
            self.assertEqual([t["views"]["seq"] for t in tab], [s0])
            self.assertEqual(caps["viewsSeq"], s0)
            types = [m["type"] for m in self.sent]
            self.assertLess(types.index("tabOrder"), types.index("caps"))
        finally:
            (km.NAMES, km._live_map, km._mark_views_dirty, km._chat_tab_sessions, km._cached_feed) = saved

    def test_the_inline_boot_routes_caps_and_unknown_op_to_the_panel(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('else if(m.type==="caps"&&panel.setCaps)panel.setCaps(m);', src)
        self.assertIn('else if(m.type==="unknownOp"&&panel.unknownOp)panel.unknownOp(m);', src)


class ReadyStripSource(_Wire):
    """The tab strip a chat page receives at `ready` comes from the connect push alone: _push lists
    _chat_tab_sessions (every living session plus the dead ones the user reopened read-only) through the
    ("taborder",) dedup slot. The ready arm used to send a second strip of its own, built from a second
    liveness read (living sessions only). The client tears down every tab a later frame omits unless that
    frame affirms it live (render.ts applyTabOrder, tab-order.ts), so the second strip
    closed every kept-open tab the first had just listed, at every ready. And once that second strip went
    through the same slot, a renderer whose socket was served a strip before its listener existed (the
    pusher fires from accept) received NO strip at `ready` while the strip was unchanged: the connect push's
    frame deduped against the pre-listener one, and so did the ready arm's own. `ready` now clears the slot
    with the chat slots (_client_reset_chat_base), so the connect push's frame goes out. Synthetic sids."""

    LIVE = "11111111-2222-3333-4444-555555555511"
    DEAD = "11111111-2222-3333-4444-555555555512"

    def setUp(self):
        super().setUp()
        self.client["app"] = "chat"                  # _push sends the strip to chat clients only
        self.seed()                                  # a stamped store: every frame's views blob carries a seq
        # the liveness reads a strip built at ready would make: pinned, so should such a strip return, these
        # tests fail the same way whatever is live on this machine
        saved = (km._live_map, km._alive_sessions)
        km._live_map = lambda: {self.LIVE: {}}
        km._alive_sessions = lambda now, live: [{"sid": self.LIVE, "name": "web", "path": "/nonexistent/live.jsonl"}]
        self.addCleanup(lambda: setattr(km, "_live_map", saved[0]))
        self.addCleanup(lambda: setattr(km, "_alive_sessions", saved[1]))

    def _connect_push(self, order):
        """The connect push's strip for `order`, the shape _push builds: the listed tabs' meta, LIVE affirmed."""
        meta = [{"id": sid, "name": "web" if sid == self.LIVE else "api", "color": None} for sid in order]
        return km._tab_order_frame(order, meta, {self.LIVE})

    def _strips(self):
        return [m for m in self.sent if m["type"] == "tabOrder"]

    @staticmethod
    def _torn_down(frames):
        """The ids the client dismisses across `frames`, by applyTabOrder's rule: an id an earlier frame
        listed that a later frame's order omits without affirming it live."""
        listed, torn = set(), []
        for fr in frames:
            torn += sorted(listed - set(fr["order"]) - set(fr.get("live") or []))
            listed |= set(fr["order"])
        return torn

    def test_a_kept_open_tab_the_connect_push_lists_is_never_omitted_by_a_frame_at_ready(self):
        frame = self._connect_push([self.LIVE, self.DEAD])
        self.handler._push_one = lambda c: km._send_client(c, ("taborder",), frame)
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        strips = self._strips()
        self.assertTrue(strips, "the connect push's strip went out")
        for fr in strips:
            self.assertIn(self.DEAD, fr["order"], "every strip at ready lists the kept-open tab: %r" % (fr["order"],))
        self.assertEqual(self._torn_down(strips), [], "no frame at ready closes a tab the connect push listed")

    def test_ready_clears_the_tab_order_slot_so_the_connect_pushs_frame_goes_out(self):
        frame = self._connect_push([self.LIVE, self.DEAD])
        # the pusher fires from accept: this socket was served the strip before the bundle's listener existed
        self.client["sent"] = {("taborder",): (km._dedup_sig(frame, json.dumps(frame)), time.time())}
        self.handler._push_one = lambda c: km._send_client(c, ("taborder",), frame)
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        self.assertEqual(self._strips(), [frame],
                         "exactly one strip, the connect push's, kept-open tab included: the slot was cleared at ready")

    def test_an_unchanged_strip_still_reaches_a_renderer_served_before_its_listener_existed(self):
        # no kept-open tab: a strip built from the kernel's own liveness read at ready would be identical to
        # the connect push's, so with the slot armed neither frame went out and the page had no strip at all
        frame = self._connect_push([self.LIVE])
        self.client["sent"] = {("taborder",): (km._dedup_sig(frame, json.dumps(frame)), time.time())}
        self.handler._push_one = lambda c: km._send_client(c, ("taborder",), frame)
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        self.assertEqual(self._strips(), [frame],
                         "the renderer holds nothing: the strip goes out although this socket was sent the same bytes")


class SetterReturnsRefusals(unittest.TestCase):
    """_set_timeline_views now RETURNS the refused rows (tid + name + plain reason); callers that
    ignored None keep ignoring a list."""

    def setUp(self):
        _own_state(self)
        self._sync = km._sync_notice
        km._sync_notice = lambda text, ok=True: None

    def tearDown(self):
        km._sync_notice = self._sync

    def test_clean_writes_return_an_empty_list_and_stale_ones_the_rows(self):
        self.assertEqual(km._set_timeline_views({"active": "all", "tags": [dict(WEB)]}), [])
        stale = json.loads(json.dumps(km._timeline_views()))
        time.sleep(1.1)
        km._edit_tag("web", add=[SID2])
        stale["tags"][0]["members"] = []
        rows = km._set_timeline_views(stale)
        self.assertEqual([sorted(r.keys()) for r in rows], [["name", "reason", "tid"]])
        self.assertEqual((rows[0]["tid"], rows[0]["name"]), ("gA", "web"))


class ReadCacheAfterWrite(_Wire):
    """The views read cache is keyed on the file's (mtime_ns, size) and was never touched by a write
    (the 2026-09-05 review). The kernel's file mtime clock is coarse (a few ms), so two
    same-size writes in one tick shared a key, and the second write's ack — built by reading the store
    — could serve the FIRST write's blob. Now the write itself refreshes the cache entry with the blob
    it wrote. This harness runs with the production cache (no clears): every ack in this file is
    served through it."""

    def test_the_cache_holds_the_blob_just_written_and_a_shared_stat_key_cannot_serve_the_older_one(self):
        self.seed()                                                          # web = #3b82f6
        p = km._views_path()
        st1 = p.stat()
        first = km._timeline_views()
        self.assertIs(km._flags_cache[str(p)][1], first, "the read warmed the cache")
        # a same-size write (a 7-char colour for a 7-char colour): the file's size does not change
        _, err = km._edit_tag("web", color="#54B204")
        self.assertIsNone(err)
        self.assertEqual(km._flags_cache[str(p)][1]["tags"][0]["color"], "#54B204",
                         "the write replaced the cache entry with what it wrote — no read needed")
        self.assertEqual(p.stat().st_size, st1.st_size, "(the write is the same size: the key's other half cannot tell them apart)")
        # the coarse-clock case, forced: give the second write the first's mtime, so the stat key is
        # exactly the one the first read was cached under
        os.utime(p, ns=(st1.st_mtime_ns, st1.st_mtime_ns))
        self.assertEqual(km._timeline_views()["tags"][0]["color"], "#54B204",
                         "the next read serves the newer write — never the older blob under a shared key")

    def test_a_targeted_edits_ack_is_fresh_through_the_production_cache(self):
        self.seed()
        a = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "recolor", "tid": "gA", "color": "#54B204"}})
        self.assertTrue(a["ok"])
        self.assertEqual(next(t for t in a["views"]["tags"] if t["id"] == "gA")["color"], "#54B204",
                         "the ack's blob is the post-write store, read through the cache the write refreshed")
        b = self.post({"type": "tagEdit", "writeId": "w2", "edit": {"op": "recolor", "tid": "gA", "color": "#DD42FF"}})
        self.assertEqual(next(t for t in b["views"]["tags"] if t["id"] == "gA")["color"], "#DD42FF")
        self.assertGreater(b["seq"], a["seq"])


class LegacyStoreStampedOnce(_Wire):
    """A store from before the write sequence (every install upgrading, the 2026-09-05
    review): served seq-less, it left every client on the null rule — adopt anything — until the
    first write, so the seq gate could not protect that first write against a frame the pusher built
    before it. The first READ stamps it once, through the setter (one write); after that the file is
    left alone. A store that does not exist is NOT created: the null rule is right for a deleted or
    recreated store, whose first write starts past what any client holds."""

    def _writes(self):
        n = [0]
        real = km._atomic_write

        def counting(path, text, mode=None):
            n[0] += 1
            return real(path, text, mode)
        km._atomic_write = counting
        return n, lambda: setattr(km, "_atomic_write", real)

    def test_a_seq_less_file_is_stamped_on_its_first_read_and_only_then(self):
        p = km._views_path()
        legacy = {"active": "all", "tags": [{"id": "gL", "name": "legacy", "color": "#3b82f6",
                                              "members": [{"host": "", "sid": SID1}]}]}
        p.write_text(json.dumps(legacy))
        n, restore = self._writes()
        try:
            v = km._timeline_views()
            self.assertEqual(n[0], 1, "exactly one write: the stamp")
            self.assertIsInstance(v.get("seq"), int, "the served blob carries a seq from its first read")
            self.assertTrue(v["seq"] > 0)
            on_disk = json.loads(p.read_text())
            self.assertEqual(on_disk["seq"], v["seq"], "…and the file does too")
            self.assertEqual(on_disk["tags"][0]["members"], [{"host": "", "sid": SID1}], "nothing else changed")
            self.assertTrue(on_disk["tags"][0].get("mtime"), "every tag is given an mtime by the stamp: "
                            "the mark that tells a tag a store once held from a client's own create")
            self.assertLessEqual(on_disk["tags"][0]["mtime"], on_disk["at"], "a file without `at`: the stamp's own time")
            self.assertEqual(self.notices, [], "a stamp is not a refusal: nothing is said")
            before = p.read_bytes()
            km._flags_cache.clear()                      # a cold cache (a restart): the stamped file is fine as-is
            v2 = km._timeline_views()
            self.assertEqual(n[0], 1, "the second read writes nothing")
            self.assertEqual(p.read_bytes(), before, "byte-identical")
            self.assertEqual(v2["seq"], v["seq"])
            self.assertEqual(km._views_client()["seq"], v["seq"], "every frame now carries it — the gate protects the first write")
            # …and that first write orders after it
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": km._views_client()})
            self.assertGreater(a["seq"], v["seq"])
        finally:
            restore()

    def test_a_missing_store_is_served_seq_less_and_not_created(self):
        n, restore = self._writes()
        try:
            v = km._timeline_views()
            self.assertNotIn("seq", v, "no file → the null rule: a client adopts whatever the first write brings")
            self.assertEqual(n[0], 0)
            self.assertFalse(km._views_path().exists(), "reading does not create the store")
        finally:
            restore()

    def test_a_torn_file_is_moved_aside_never_overwritten_and_the_store_reads_empty(self):
        # MOVED PIN (the views store's proved read): this test asserted the torn file was left IN PLACE ("a corrupt
        # file is left for a human"). The reader goes through _read_state_json now, which QUARANTINES torn bytes
        # aside (<name>.corrupt-<stamp>: a move, never a delete -- the flags, order and bell stores' convention)
        # so the evidence survives AND the store reads as empty only after it is preserved; left in place, the
        # next writer's atomic replace overwrote it. The stamp still never replaces it with an empty store.
        p = km._views_path()
        p.write_text("{not json")
        n, restore = self._writes()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(km._timeline_views()["tags"], [])
            self.assertEqual(n[0], 0, "a corrupt file is never replaced with an empty store by the stamp")
            self.assertFalse(p.exists(), "the torn bytes were moved aside, not left for the next writer to overwrite")
            aside = sorted(p.parent.glob("timeline-views.json.corrupt-*"))
            self.assertEqual(len(aside), 1, "one quarantine file")
            self.assertEqual(aside[0].read_text(), "{not json", "…holding the ORIGINAL bytes for forensics")
        finally:
            restore()
            for q in p.parent.glob("timeline-views.json.corrupt-*"):
                q.unlink()


class LegacyTagsStampedOnFirstRead(_Wire):
    """The 2026-09-05 review: the write door stamps an mtime on a tag only when it changes,
    and the first-read stamp changes nothing, so a tag from before the per-tag stamp never got one —
    and the foreign-file rule (an unknown tag WITH an mtime existed in a store once and is not
    re-created; one without is the writer's own create) let a deleted legacy tag come back through
    a file written outside the kernel. The first-read stamp (and the migration) now give every tag
    an mtime: the file's `at`, else the time of the stamp."""

    def test_every_legacy_tag_gets_an_mtime_so_a_deleted_one_cannot_come_back_through_a_foreign_file(self):
        p = km._views_path()
        legacy = {"active": "all", "at": 1700000000, "tags": [
            {"id": "gL1", "name": "web", "color": "#3b82f6", "members": [{"host": "", "sid": SID1}]},
            {"id": "gL2", "name": "api", "color": "#54B204", "members": [{"host": "", "sid": SID2}]}]}
        p.write_text(json.dumps(legacy))
        v = km._timeline_views()                                       # the first read: the stamp
        self.assertEqual([t.get("mtime") for t in v["tags"]], [1700000000, 1700000000], "every tag carries the file's `at`")
        self.assertEqual([t["mtime"] for t in json.loads(p.read_text())["tags"]], [1700000000, 1700000000], "…on disk too")
        self.assertEqual(self.notices, [], "a stamp is not a refusal")
        panel = json.loads(p.read_text())                              # a panel's copy, taken after the stamp
        time.sleep(1.1)
        self.assertIsNone(km._edit_tag(tid="gL2", delete=True)[1])    # api deleted since
        s_served = km._timeline_views()["seq"]
        foreign = json.loads(json.dumps(panel))                        # the panel writes its copy: seq behind, judged
        foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        time.sleep(0.01)
        km._atomic_write(p, json.dumps(foreign))
        v2 = km._timeline_views()
        self.assertGreater(v2["seq"], s_served)
        self.assertEqual([t["id"] for t in v2["tags"]], ["gL1"],
                         "the deleted legacy tag does not come back: its mtime says a store once held it")
        self.assertEqual(v2["actives"]["timeline"], {"tags": ["web"]}, "the panel's lens change lands")
        self.assertTrue(self.notices and not self.notices[0][1])
        self.assertIn('"api" (re-creation)', self.notices[0][0])
        # a legacy file without `at`: the stamp's own time
        p.unlink()
        self.assertEqual(km._timeline_views()["tags"], [])
        p.write_text(json.dumps({"active": "all", "tags": [{"id": "gL3", "name": "docs", "color": "", "members": []}]}))
        before = int(time.time())
        v3 = km._timeline_views()
        self.assertGreaterEqual(v3["tags"][0]["mtime"], before)
        self.assertLessEqual(v3["tags"][0]["mtime"], v3["at"])


class SeqFloorOutlivesTheCacheEntry(_Wire):
    """The 2026-09-05 review: a store read as missing forgets its cache entry (rightly: a
    file that then appears must not be judged against a store that no longer exists), and with it
    forgot the seq floor — so a file restored from an older copy was served under its old seq, and
    every dashboard holding a higher one ignored it until the next kernel write. The floor is kept
    apart from the entry now (_VIEWS_SEQ_FLOOR): the restored file is re-stamped past it, as written,
    and every write orders past it."""

    def test_a_store_deleted_and_recreated_with_an_older_seq_is_restamped_past_the_floor(self):
        served = self.seed()
        s1 = served["seq"]
        p = km._views_path()
        content = json.loads(p.read_text())
        p.unlink()
        self.assertEqual(km._timeline_views()["tags"], [], "read as missing: the cache entry is forgotten")
        self.assertNotIn(str(p), km._flags_cache)
        self.assertGreaterEqual(km._views_seq_floor(), s1, "…the floor is not")
        restored = json.loads(json.dumps(content))
        restored["seq"] = s1 - 5                                       # a restore from an older copy
        km._atomic_write(p, json.dumps(restored))
        v = km._timeline_views()
        self.assertGreater(v["seq"], s1, "re-stamped past the last served seq: a dashboard holding s1 adopts it")
        self.assertEqual([t["name"] for t in v["tags"]], ["web"], "kept as written: nothing served to judge it against")
        self.assertEqual(json.loads(p.read_text())["seq"], v["seq"], "…and the file carries the new seq")
        self.assertEqual(self.notices, [], "ordering a file is not a refusal")
        # a restored file at or past the floor is left alone
        p.unlink()
        self.assertEqual(km._timeline_views()["tags"], [])
        ahead = json.loads(json.dumps(content))
        ahead["seq"] = v["seq"] + 100
        km._atomic_write(p, json.dumps(ahead))
        before = p.read_bytes()
        self.assertEqual(km._timeline_views()["seq"], v["seq"] + 100)
        self.assertEqual(p.read_bytes(), before, "byte-identical")
        # a write after a delete orders past the floor too, not just past the (empty) previous blob
        p.unlink()
        self.assertEqual(km._timeline_views()["tags"], [])
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": []}})
        self.assertGreater(a["seq"], v["seq"] + 100)

    def test_the_floor_is_per_store_so_two_state_dirs_in_one_process_do_not_share_one(self):
        """The 2026-09-05 review: the floor was one module-wide number keyed on nothing,
        while the read cache beside it is keyed by path — so a process serving two state dirs (a test
        suite; a kernel rebound to another root) re-stamped a cold read of a small-seq file in the
        fresh dir past the OTHER store's seq: a write on read, ordered against a store it never held."""
        from pathlib import Path
        s1 = self.seed()["seq"]
        home = km.jd.STATE
        self.assertGreaterEqual(km._views_seq_floor(), s1, "the first store's floor is raised by its own writes")
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        km.jd.STATE = Path(other)
        p2 = km._views_path()
        try:
            km._atomic_write(p2, json.dumps({"active": "all", "tags": [dict(WEB)], "seq": 5, "at": 100}))
            before = p2.read_bytes()
            v = km._timeline_views()
            self.assertEqual(v["seq"], 5, "served as written: this store has no floor of its own")
            self.assertEqual(p2.read_bytes(), before, "…and the file is not rewritten")
            self.assertEqual(km._views_seq_floor(), 5, "the read raised THIS store's floor…")
            self.assertGreaterEqual(km._views_seq_floor(home / "timeline-views.json"), s1, "…and left the other's alone")
            self.assertEqual(self.notices, [])
        finally:
            km.jd.STATE = home
            km._flags_cache.pop(str(p2), None)
            km._VIEWS_SEQ_FLOOR.pop(str(p2), None)


class ForeignWriteReStamped(_Wire):
    """A write to timeline-views.json OUTSIDE the kernel (the 2026-09-05 review): the
    timeline's Electron branch writes the file itself with the seq it holds, so a panel holding an
    older frame publishes a seq lower than what every dashboard holds, and they all ignore the file
    until the next kernel write. The reader treats a changed file whose seq fell behind the last one
    served as such a write and re-stamps it through the setter: seq = max(last + 1, now), the content
    kept as written. A changed file whose seq is not behind is served as-is."""

    def test_a_file_whose_seq_fell_behind_is_re_stamped_past_the_last_served_seq(self):
        served = self.seed()
        s1 = served["seq"]
        self.assertEqual(km._timeline_views()["seq"], s1, "served: the cache holds it")
        p = km._views_path()
        # the panel's write: its held (older) blob with a lens change, seq behind the store's
        foreign = json.loads(p.read_text())
        foreign["seq"] = s1 - 5
        foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        time.sleep(0.01)                                  # a new mtime, so the file changes under the cache
        km._atomic_write(p, json.dumps(foreign))
        v = km._timeline_views()
        self.assertGreater(v["seq"], s1, "re-stamped past the last served seq — every dashboard adopts it")
        self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]}, "the content is kept as written")
        on_disk = json.loads(p.read_text())
        self.assertEqual(on_disk["seq"], v["seq"], "…and the file carries the new seq: the next kernel read is a plain hit")
        self.assertEqual(self.notices, [], "ordering a file is not a refusal")
        self.assertEqual(km._views_client()["seq"], v["seq"])
        # a kernel write after it orders after the re-stamp
        a = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "recolor", "tid": "gA", "color": "#54B204"}})
        self.assertGreater(a["seq"], v["seq"])

    def test_a_changed_file_whose_seq_is_not_behind_is_served_as_is(self):
        served = self.seed()
        s1 = served["seq"]
        km._timeline_views()
        p = km._views_path()
        foreign = json.loads(p.read_text())                # a panel holding the NEWEST frame writes it back
        foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        time.sleep(0.01)
        km._atomic_write(p, json.dumps(foreign))
        before = p.read_bytes()
        v = km._timeline_views()
        self.assertEqual(v["seq"], s1, "an equal seq is not behind: no write")
        self.assertEqual(p.read_bytes(), before, "byte-identical")
        self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]})
        ahead = json.loads(p.read_text()); ahead["seq"] = s1 + 100
        time.sleep(0.01)
        km._atomic_write(p, json.dumps(ahead))
        self.assertEqual(km._timeline_views()["seq"], s1 + 100, "a seq ahead of the last served is adopted as the new floor")


class MigrationStampsTheArchivedTag(_Wire):
    """The 2026-09-05 review: the hidden-to-archived migration built its diff base from the
    same tag dicts it mutated (the archived tag's dict was aliased, and the dict copy was shallow), so
    the base already carried the migration and an EXISTING archived tag gained its members with no
    fresh mtime. A dashboard holding the pre-migration copy could then post the whole blob and strip
    the migrated members with no refusal. The migration now works on a deep copy: the moved members
    stamp the tag, and the guard refuses the stale copy."""

    def test_an_existing_archived_tag_is_stamped_by_the_migration_and_a_stale_copy_cannot_strip_it(self):
        p = km._views_path()
        pre = {"active": "all", "tags": [dict(WEB), {"id": "archived", "name": "archived", "color": "#6b7280",
                                                     "members": [SID2]}], "hidden": [SID3]}
        p.write_text(json.dumps(pre))
        v = km._timeline_views()
        arch = next(t for t in v["tags"] if t["name"] == "archived")
        self.assertEqual([m["sid"] for m in arch["members"]], sorted([SID2, SID3]), "the hidden entry joined the existing tag")
        self.assertTrue(arch.get("mtime"), "the migrated tag carries a FRESH edit stamp: its members changed")
        self.assertTrue(next(t for t in v["tags"] if t["id"] == "gA").get("mtime"),
                        "an untouched tag is given one too: every tag a migrated store holds carries an mtime")
        self.assertNotIn("hidden", json.loads(p.read_text()))
        # the pre-migration dashboard posts its whole copy (no `at`, no mtimes): the guard refuses
        # the archived hunk — the migrated members stand — and the ack says so
        stale = {"active": "all", "tags": [dict(WEB), {"id": "archived", "name": "archived", "color": "#6b7280", "members": [SID2]}]}
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": ["archived"]})
        self.assertFalse(a["ok"])
        self.assertEqual([r["tid"] for r in a["refused"]], ["archived"])
        self.assertEqual(sorted(m["sid"] for m in store_tag("archived")["members"]), sorted([SID2, SID3]),
                         "the migrated members survive the stale copy")
        self.assertTrue(self.notices and not self.notices[0][1], "and the refusal is loud")


class ReaderRestampUnwritable(_Wire):
    """The 2026-09-05 review: the reader's re-stamp write had no error handling, so an
    unwritable or full state dir made every READ raise, and the feed's build aborted with it. Now an
    OSError on that write is logged once per distinct error, the file is served as read (normalized)
    and cached under its own key so reads stop retrying the write, and the next successful write
    clears the note so a recurrence is logged again."""

    def setUp(self):
        super().setUp()
        self.addCleanup(km._VIEWS_RESTAMP_ERR.__setitem__, 0, km._VIEWS_RESTAMP_ERR[0])
        km._VIEWS_RESTAMP_ERR[0] = None

    def test_a_read_only_state_dir_is_served_not_raised_and_logged_once(self):
        import contextlib
        import io
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        p = km._views_path()
        d = p.parent
        legacy = {"active": "all", "tags": [dict(WEB)]}                # seq-less: the first read wants to stamp it
        p.write_text(json.dumps(legacy))
        n = [0]
        real = km._atomic_write

        def counting(path, text, mode=None):
            n[0] += 1
            return real(path, text, mode)
        km._atomic_write = counting
        os.chmod(d, 0o555)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                v = km._timeline_views()                                # does not raise
                self.assertEqual([t["name"] for t in v["tags"]], ["web"], "the file is served as read")
                self.assertNotIn("seq", v, "…unstamped: the write did not land")
                self.assertEqual(n[0], 1, "one write attempted")
                v2 = km._timeline_views()
                self.assertIs(v2, v, "the second read is a cache hit under the file's key")
                self.assertEqual(n[0], 1, "…and does not retry the write")
                km._flags_cache.clear()                                # a cold cache retries…
                km._timeline_views()
                self.assertEqual(n[0], 2)
            lines = [ln for ln in err.getvalue().splitlines() if "could not be re-stamped" in ln]
            self.assertEqual(len(lines), 1, "one stderr line for one distinct error, however many reads")
            self.assertIn("PermissionError", lines[0])
            self.assertEqual(p.read_text(), json.dumps(legacy), "the file is untouched")
            self.assertEqual(self.notices, [], "an unwritable store is a log line, not a refusal notice")
            # writable again: a kernel write succeeds and clears the note, so the next failure logs afresh
            os.chmod(d, 0o755)
            km._flags_cache.clear()
            a = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "recolor", "tid": "gA", "color": "#54B204"}})
            self.assertTrue(a["ok"])
            self.assertIsNone(km._VIEWS_RESTAMP_ERR[0], "a successful write clears the note")
            p.write_text(json.dumps(legacy))                           # seq-less again, then unwritable again
            km._flags_cache.clear()
            os.chmod(d, 0o555)
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                km._timeline_views()
            self.assertEqual(len([ln for ln in err2.getvalue().splitlines() if "could not be re-stamped" in ln]), 1,
                             "the same error logs again after a write cleared the note")
        finally:
            os.chmod(d, 0o755)
            km._atomic_write = real
            km._VIEWS_RESTAMP_ERR[0] = None

    def test_a_judged_foreign_file_on_an_unwritable_store_is_served_judged_and_the_next_write_persists_it(self):
        """The 2026-09-05 review: the OSError branch served and cached the file AS READ in
        every case — in the judged case that is the foreign copy the judgment had just refused, so an
        unwritable store served the deleted tag back and the newer member gone, and the next RMW write,
        built from that cache, persisted them. The judgment is now a step apart from the write, and
        the judged blob is what an unwritable store serves and caches."""
        import contextlib
        import io
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        self.seed()
        c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204", "sids": [SID2]}})
        api_tid = c["tid"]
        p = km._views_path()
        d = p.parent
        panel = json.loads(p.read_text())                              # the panel's copy: web[SID1], api
        time.sleep(1.1)
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID3])[1])       # a member added since
        self.assertIsNone(km._edit_tag(tid=api_tid, delete=True)[1])   # a tag deleted since
        s_served = km._timeline_views()["seq"]
        foreign = json.loads(json.dumps(panel))                        # seq behind: judged on read
        foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        time.sleep(0.01)
        km._atomic_write(p, json.dumps(foreign))
        os.chmod(d, 0o555)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                v = km._timeline_views()
                web = next(t for t in v["tags"] if t["id"] == "gA")
                self.assertEqual(sorted(m["sid"] for m in web["members"]), sorted([SID1, SID3]), "the newer member is served")
                self.assertNotIn("api", [t["name"] for t in v["tags"]], "the deleted tag stays absent")
                self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]}, "the panel's lens change is served")
                self.assertGreater(v["seq"], s_served, "…under a seq past the last served, so dashboards adopt it")
                self.assertIs(km._timeline_views(), v, "cached: the judged blob, under the file's key")
            lines = [ln for ln in err.getvalue().splitlines() if "could not be re-stamped" in ln]
            self.assertEqual(len(lines), 1)
            self.assertIn("the judged blob", lines[0])
            self.assertEqual(json.loads(p.read_text())["tags"], foreign["tags"], "the file is still the foreign copy: nothing landed")
            self.assertEqual(len(self.notices), 1, "the judgment's refusals are reported once")
            self.assertFalse(self.notices[0][1])
            # writable again: the next write is built from the judged cache and persists the judged state
            os.chmod(d, 0o755)
            a = self.post({"type": "tagEdit", "writeId": "w2", "edit": {"op": "recolor", "tid": "gA", "color": "#DD42FF"}})
            self.assertTrue(a["ok"])
            on_disk = json.loads(p.read_text())
            web_disk = next(t for t in on_disk["tags"] if t["id"] == "gA")
            self.assertEqual(sorted(m["sid"] for m in web_disk["members"]), sorted([SID1, SID3]), "the newer member persists")
            self.assertEqual([t["name"] for t in on_disk["tags"]], ["web"], "the deleted tag does not come back")
            self.assertEqual(web_disk["color"], "#DD42FF")
            self.assertEqual(on_disk["actives"]["timeline"], {"tags": ["web"]})
            self.assertGreater(on_disk["seq"], v["seq"])
        finally:
            os.chmod(d, 0o755)
            km._VIEWS_RESTAMP_ERR[0] = None


class ForeignWriteJudged(_Wire):
    """The 2026-09-05 review: the re-stamp of a file written outside the kernel whose seq
    fell behind used the file's own content as the guard's base, so a stale Electron blob was blessed
    wholesale — a tag deleted since came back, a member added since was lost, silently. The writer's
    own seq says it held an older copy, so the file is now judged against the LAST SERVED blob: the
    refused hunks are kept from it, the writer's own creates land, and the refusals are reported
    through the sync notice naming each tag. With no served blob (a fresh kernel; a store read as
    missing) there is nothing to judge against and the file is ordered as written, as before."""

    def test_a_seq_behind_file_is_judged_against_the_last_served_blob(self):
        self.seed()
        c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204", "sids": [SID2]}})
        api_tid = c["tid"]
        p = km._views_path()
        panel = json.loads(p.read_text())                              # the panel's copy: web[SID1], api, this seq and `at`
        time.sleep(1.1)
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID3])[1])       # a member added since
        self.assertIsNone(km._edit_tag(tid=api_tid, delete=True)[1])   # a tag deleted since
        served = km._timeline_views()
        s_served = served["seq"]
        # the panel writes the file itself: its stale copy plus a lens change, plus a tag it created
        # (a client-minted id, no mtime — the one mark a kernel puts on a tag)
        foreign = json.loads(json.dumps(panel))
        foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        foreign["tags"].append({"id": "gdocs", "name": "docs", "color": "#DD42FF", "members": [SID2]})
        time.sleep(0.01)
        km._atomic_write(p, json.dumps(foreign))
        v = km._timeline_views()
        self.assertGreater(v["seq"], s_served, "re-stamped past the last served seq")
        web = next(t for t in v["tags"] if t["id"] == "gA")
        self.assertEqual(sorted(m["sid"] for m in web["members"]), sorted([SID1, SID3]), "the member added since is KEPT")
        self.assertIsNone(store_tag("api"), "the tag deleted since is not brought back")
        self.assertEqual(store_tag("docs")["id"], "gdocs", "the panel's own create lands")
        self.assertEqual(v["actives"]["timeline"], {"tags": ["web"]}, "the lens change lands")
        self.assertEqual(json.loads(p.read_text())["tags"], v["tags"], "the file holds the judged result")
        self.assertEqual(len(self.notices), 1)
        text, ok = self.notices[0]
        self.assertFalse(ok, "the refusals are loud")
        self.assertIn("outside the kernel", text)
        self.assertIn('"web"', text)
        self.assertIn('"api" (re-creation)', text, "…naming each tag")
        # no served blob: a store read as missing forgets what was served, and a file that then
        # appears is kept as written — nothing to judge it against — but ORDERED past the last
        # served seq, which outlives the entry (SeqFloorOutlivesTheCacheEntry)
        p.unlink()
        self.assertEqual(km._timeline_views()["tags"], [])
        recreated = json.loads(json.dumps(foreign))
        recreated["seq"] = 5
        km._atomic_write(p, json.dumps(recreated))
        v2 = km._timeline_views()
        self.assertEqual(sorted(t["name"] for t in v2["tags"]), ["api", "docs", "web"], "kept as written")
        self.assertGreater(v2["seq"], v["seq"], "re-stamped past the last served seq")
        self.assertEqual(len(self.notices), 1, "no second notice")


class WholeBlobNameCollisions(_Wire):
    """The 2026-09-05 review (the verifier's reproduction): a rename the targeted op refused
    as a duplicate landed anyway through a whole-blob lens write the dialog built from its PENDING
    copy, and neither the whole-blob door nor the normalizer deduped names — two tags with one name,
    which every name-keyed surface shows as one. The door now refuses a renamed or new tag whose name
    another tag in the resulting set holds, with a reason naming the collision, and the normalizer
    drops a second entry under one id."""

    def test_the_reproduction_a_refused_rename_cannot_land_through_a_lens_write(self):
        self.seed()
        c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204", "sids": [SID2]}})
        api_tid = c["tid"]
        r = self.post({"type": "tagEdit", "writeId": "w2", "edit": {"op": "rename", "tid": api_tid, "newName": "web"}})
        self.assertEqual((r["ok"], r["error"]), (False, 'a tag named "web" already exists'))
        pending = json.loads(json.dumps(c["views"]))                    # the dialog's pending copy still carries the rename
        next(t for t in pending["tags"] if t["id"] == api_tid)["name"] = "web"
        pending["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
        a = self.post({"type": "setTimelineViews", "writeId": "w3", "views": pending, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []),
                         "a lens write changes no tag, so the pending rename is not even judged")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["api", "web"], "ONE tag per name")
        self.assertEqual(km._timeline_views()["actives"]["timeline"], {"tags": ["web"]}, "the lens landed")
        self.assertEqual(self.notices, [], "nothing the user did in THIS write was refused")
        # the same pending copy riding a write that edits web (a recolor): the rename is a tag this
        # write did not claim to edit — kept as the store has it, listed with the collision's reason
        pending_w = json.loads(json.dumps(pending))
        next(t for t in pending_w["tags"] if t["id"] == "gA")["color"] = "#000000"
        a2 = self.post({"type": "setTimelineViews", "writeId": "w3b", "views": pending_w, "edited": ["gA"]})
        self.assertTrue(a2["ok"], "the refused hunk is a tag this write did not claim to edit")
        self.assertEqual([(x["tid"], x["name"]) for x in a2["refused"]], [(api_tid, "api")])
        self.assertIn("did not edit it", a2["refused"][0]["reason"])
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["api", "web"], "ONE tag per name")
        self.assertEqual(store_tag("web")["color"], "#000000", "the recolor landed")
        self.assertEqual(self.notices, [], "nothing the user did in THIS write was refused")
        # the rename on a copy of the store, naming the tag as edited (an older client's whole-blob rename): refused, loud
        pending2 = json.loads(json.dumps(km._views_client()))
        next(t for t in pending2["tags"] if t["id"] == api_tid)["name"] = "web"
        b = self.post({"type": "setTimelineViews", "writeId": "w4", "views": pending2, "edited": [api_tid]})
        self.assertFalse(b["ok"])
        self.assertEqual(b["error"], '"api": a tag named "web" already exists, so it was not renamed to it')
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["api", "web"])
        self.assertTrue(self.notices and not self.notices[0][1])
        self.assertIn('"api" (name collision)', self.notices[0][0])

    def test_a_new_tag_under_a_taken_name_is_kept_out_and_a_swap_of_two_names_lands(self):
        served = self.seed()
        blob = json.loads(json.dumps(served))
        blob["tags"].append({"id": "gnew", "name": "web", "color": "#000000", "members": []})
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": blob, "edited": ["gnew"]})
        self.assertEqual((a["ok"], a["refused"][0]["tid"]), (False, "gnew"))
        self.assertEqual(a["refused"][0]["reason"], 'a tag named "web" already exists, so it was not created')
        self.assertEqual([t["id"] for t in km._timeline_views()["tags"]], ["gA"])
        # two tags trading names in one write collide with nothing: both changed, neither name is held
        c = self.post({"type": "tagEdit", "writeId": "w2", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
        swap = json.loads(json.dumps(c["views"]))
        for t in swap["tags"]:
            t["name"] = "api" if t["id"] == "gA" else "web"
        b = self.post({"type": "setTimelineViews", "writeId": "w3", "views": swap, "edited": ["gA", c["tid"]]})
        self.assertEqual((b["ok"], b["refused"]), (True, []))
        self.assertEqual(store_tag("api")["id"], "gA")
        self.assertEqual(store_tag("web")["id"], c["tid"])

    def test_a_refused_rename_settles_its_stored_name_so_a_new_tag_under_it_is_refused_too(self):
        """The 2026-09-05 review: B (api → web, refused against A = web) stood as "api" but
        never claimed it, so a new C named "api" in the same write landed beside it — twins under the
        kept name. The pass now runs to a fixpoint: a refused rename settles its stored name and the
        takers are re-checked, in either array order, with or without `edited`."""
        for order, named in (("ABC", True), ("ACB", True), ("ABC", False), ("ACB", False)):
            with self.subTest(order=order, edited=named):
                try:
                    km._views_path().unlink()                              # a fresh store per case
                except OSError:
                    pass
                self.notices.clear()
                self.seed()
                c = self.post({"type": "tagEdit", "writeId": "w1", "edit": {"op": "create", "name": "api", "color": "#54B204"}})
                b_tid = c["tid"]
                blob = json.loads(json.dumps(c["views"]))
                a_row = next(t for t in blob["tags"] if t["id"] == "gA")
                b_row = next(t for t in blob["tags"] if t["id"] == b_tid)
                b_row["name"] = "web"                                       # B renamed to A's name
                c_row = {"id": "gC", "name": "api", "color": "#DD42FF", "members": []}   # C created under B's stored name
                blob["tags"] = [a_row, b_row, c_row] if order == "ABC" else [a_row, c_row, b_row]
                msg = {"type": "setTimelineViews", "writeId": "w2", "views": blob}
                if named:
                    msg["edited"] = [b_tid, "gC"]
                a = self.post(msg)
                self.assertFalse(a["ok"])
                self.assertEqual(sorted((r["tid"], r["name"]) for r in a["refused"]), sorted([(b_tid, "api"), ("gC", "api")]))
                reasons = {r["tid"]: r["reason"] for r in a["refused"]}
                self.assertEqual(reasons[b_tid], 'a tag named "web" already exists, so it was not renamed to it')
                self.assertEqual(reasons["gC"], 'a tag named "api" already exists, so it was not created')
                self.assertEqual(sorted((t["id"], t["name"]) for t in km._timeline_views()["tags"]),
                                 sorted([("gA", "web"), (b_tid, "api")]), "one tag per name: B kept under api, C kept out")
                self.assertTrue(self.notices and not self.notices[0][1], "both refusals are the poster's: loud")
                self.assertIn('"api" (name collision)', self.notices[0][0])

    def test_names_are_stripped_at_the_door_so_a_padded_spelling_is_the_same_name(self):
        """The 2026-09-05 review: the targeted door stripped a rename, the whole-blob door
        stripped nothing, so "web " beside "web" passed the collision pass as two names. Names are
        now clamped and stripped in the normalizer — the first step of every write and every read —
        and the targeted door's lookup uses the same basis."""
        served = self.seed()
        blob = json.loads(json.dumps(served))
        blob["tags"].append({"id": "gpad", "name": "web ", "color": "#000000", "members": []})
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": blob, "edited": ["gpad"]})
        self.assertEqual((a["ok"], [r["tid"] for r in a["refused"]]), (False, ["gpad"]))
        self.assertEqual(a["refused"][0]["reason"], 'a tag named "web" already exists, so it was not created')
        self.assertEqual([t["name"] for t in km._timeline_views()["tags"]], ["web"], "no padded twin")
        blob["tags"][-1]["name"] = "  api  "
        b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": blob, "edited": ["gpad"]})
        self.assertEqual((b["ok"], b["refused"]), (True, []))
        self.assertEqual(store_tag("api")["id"], "gpad", "stored stripped")
        self.assertEqual(next(t for t in b["views"]["tags"] if t["id"] == "gpad")["name"], "api", "…and served stripped")
        # the targeted door addresses the stored spelling from a padded one, rather than minting a twin
        t, err = km._edit_tag(" web ", add=[SID2])
        self.assertIsNone(err)
        self.assertEqual(t["id"], "gA")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["api", "web"])
        # a name that is only padding is no name: the whole-blob door stores the default, the targeted door mints one
        blob2 = json.loads(json.dumps(km._views_client()))
        blob2["tags"].append({"id": "gblank", "name": "   ", "color": "", "members": []})
        c = self.post({"type": "setTimelineViews", "writeId": "w3", "views": blob2, "edited": ["gblank"]})
        self.assertTrue(c["ok"])
        self.assertEqual(store_tag("tag")["id"], "gblank")
        t2, err = km._edit_tag("   ", add=[SID3])
        self.assertIsNone(err)
        self.assertTrue(t2["name"].strip() and t2["id"] not in ("gA", "gpad", "gblank"), "a create with no name takes the default")

    def test_the_normalizer_drops_a_second_entry_under_one_id(self):
        v = km._norm_timeline_views({"active": "all", "tags": [
            {"id": "g1", "name": "web", "members": [SID1]}, {"id": "g1", "name": "web", "members": [SID2]},
            {"id": "g2", "name": "api", "members": []}]})
        self.assertEqual([t["id"] for t in v["tags"]], ["g1", "g2"])
        self.assertEqual([m["sid"] for m in v["tags"][0]["members"]], [SID1], "the first entry is the one kept")


class SetterJudgesUnderTheFileLock(_Wire):
    """The 2026-09-05 review: the reader's re-stamp of a file written outside the kernel
    holds _views_file_lock across its judge and write, but the write door judged first and took the
    lock for the write alone. A re-stamp landing in between was written over by a blob judged
    against the pre-re-stamp store: the foreign write's lens change and its creates gone, under a
    seq no higher than the re-stamp's (both computed from the same base), no refusal anywhere, and
    every dashboard that had adopted the re-stamp's seq ignoring the store until the next write.
    The door holds the lock across both steps now, so a re-stamp waits for the write to finish."""

    def test_a_re_stamp_cannot_land_between_a_writers_judge_and_its_write(self):
        s0 = self.seed()["seq"]
        real = km._judge_timeline_views
        in_judge, release, paused = threading.Event(), threading.Event(), [False]

        def slow(blob, base=None, seq_floor=0, edited=None, foreign=None):
            out = real(blob, base=base, seq_floor=seq_floor, edited=edited, foreign=foreign)
            if base is None and foreign is None and not paused[0]:       # the writer's own judgment, once
                paused[0] = True
                in_judge.set()
                release.wait(5)
            return out
        km._judge_timeline_views = slow
        try:
            w_blob = json.loads(json.dumps(km._timeline_views()))
            w_blob["tags"][0]["color"] = "#DD42FF"                          # a recolor, as _edit_tag writes it

            def writer():
                with km._views_lock:                                      # the RMW writers hold this one
                    km._set_timeline_views(w_blob)
            w = threading.Thread(target=writer)
            w.start()
            self.assertTrue(in_judge.wait(5), "the writer is inside its judgment")
            # a panel writes the file itself meanwhile, from an older copy: its lens change and a create
            p = km._views_path()
            foreign = json.loads(p.read_text())
            foreign["seq"] = s0 - 1
            foreign["actives"] = {"timeline": {"tags": ["web"]}, "chat": {"all": True}, "outline": {"all": True}}
            foreign["tags"].append({"id": "gP", "name": "panel", "color": "", "members": []})
            km._atomic_write(p, json.dumps(foreign))
            served = []
            r = threading.Thread(target=lambda: served.append(km._timeline_views()))   # any frame build
            r.start()
            r.join(0.5)
            self.assertTrue(r.is_alive(), "the reader's re-stamp waits on the lock the writer holds across judge and write")
            release.set()
            w.join(5)
            r.join(5)
            self.assertFalse(w.is_alive() or r.is_alive())
        finally:
            km._judge_timeline_views = real
            release.set()
        final = km._timeline_views()
        self.assertEqual(final["tags"][0]["color"], "#DD42FF", "the writer's edit stands")
        self.assertGreater(final["seq"], s0)
        self.assertEqual(served[0], final, "what the reader served IS the store: nothing it handed out carries a seq the store fell behind")
        self.assertEqual(json.loads(p.read_text())["seq"], final["seq"])
        # (the panel's own file write was replaced by the writer's atomic write before it was judged, as any
        # file write racing a kernel write is; the point here is the order — the reader can no longer hand
        # out a blob the very next kernel write lands behind)


class CapNeverDropsAKeptStoreTag(_Wire):
    """The 2026-09-05 review (the MEDIUM finding): the door assembled the set that stands
    as the blob's tags first and the store's kept copies after, then sliced it to _VIEWS_MAX_TAGS —
    so when a stale client's tags plus the store's keeps exceeded the cap, the tag dropped was
    exactly the one the ack had just said was kept ("this write did not edit it, so it was not
    deleted"), and the client's own create took its place under an ok ack. Now a kept store tag
    always survives: the write's creates are refused instead, with a reason naming the cap, the same
    way _edit_tag refuses a 33rd."""

    def _seed_many(self, n):
        tags = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(n)]
        ack = self.post({"type": "setTimelineViews", "writeId": "w0", "views": {"active": "all", "tags": tags}})
        self.assertEqual((ack["ok"], ack["refused"], len(ack["views"]["tags"])), (True, [], n))
        return ack["views"]

    def test_edited_path_a_create_over_the_cap_is_refused_and_the_quietly_kept_tag_survives(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS)                       # the store is full
        stale = json.loads(json.dumps(served))
        stale["tags"] = [t for t in stale["tags"] if t["id"] != "g31"]      # a copy from before the 32nd…
        stale["tags"].append({"id": "gnew", "name": "mine", "color": "#DD42FF", "members": [SID1]})   # …plus a create
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": ["gnew"]})
        self.assertFalse(a["ok"], "the client's own create was refused, so the ack is not ok")
        rows = {r["tid"]: r["reason"] for r in a["refused"]}
        self.assertEqual(rows, {"gnew": "the views blob caps at 32 tags, so it was not created",
                                "g31": "this write did not edit it, so it was not deleted"})
        ids = [t["id"] for t in km._timeline_views()["tags"]]
        self.assertEqual(len(ids), km._VIEWS_MAX_TAGS)
        self.assertIn("g31", ids, "the kept tag the ack names is in the store")
        self.assertNotIn("gnew", ids, "the create is not")
        self.assertEqual([t["id"] for t in a["views"]["tags"]], ids, "the ack's blob is the store")
        self.assertEqual(len(self.notices), 1, "a refused create is the poster's lost edit: loud")
        self.assertIn('"mine" (over the cap)', self.notices[0][0])
        self.assertIn("its changes to 1 tag were not applied", self.notices[0][0])

    def test_edited_less_path_the_loudly_kept_tag_survives_and_the_create_is_refused(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS - 1)                   # 31 in the store
        t31, err = km._edit_tag("t31", add=[SID2])                         # another surface's create: 32
        self.assertIsNone(err)
        stale = json.loads(json.dumps(served))                             # an older client's copy of the 31…
        stale["at"] -= 10                                                  # …with evidence older than the create
        for t in stale["tags"]:
            t["mtime"] -= 10
        stale["tags"].append({"id": "gnew", "name": "mine", "color": "", "members": []})
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale})   # no `edited`: the legacy reading
        self.assertFalse(a["ok"])
        rows = {r["tid"]: r["reason"] for r in a["refused"]}
        self.assertEqual(rows, {"gnew": "the views blob caps at 32 tags, so it was not created",
                                t31["id"]: "it was edited after your copy was taken, so it was not deleted"})
        ids = [t["id"] for t in km._timeline_views()["tags"]]
        self.assertEqual(len(ids), km._VIEWS_MAX_TAGS)
        self.assertIn(t31["id"], ids, "the store's newer tag stands, as the notice says it does")
        self.assertNotIn("gnew", ids)
        self.assertEqual(len(self.notices), 1)
        self.assertIn('"t31" (deletion)', self.notices[0][0])
        self.assertIn('"mine" (over the cap)', self.notices[0][0])

    def test_creates_that_fit_land_and_only_the_excess_is_refused_last_first(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS - 2)                   # room for two
        w = json.loads(json.dumps(served))
        w["tags"] += [{"id": "gx", "name": "x", "color": "", "members": []},
                      {"id": "gy", "name": "y", "color": "", "members": []}]
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w, "edited": ["gx", "gy"]})
        self.assertEqual((a["ok"], a["refused"]), (True, []), "both creates fit")
        self.assertEqual(len(km._timeline_views()["tags"]), km._VIEWS_MAX_TAGS)
        w2 = json.loads(json.dumps(a["views"]))
        w2["tags"].append({"id": "gz", "name": "z", "color": "", "members": []})
        b = self.post({"type": "setTimelineViews", "writeId": "w2", "views": w2, "edited": ["gz"]})
        self.assertEqual((b["ok"], [(r["tid"], r["reason"]) for r in b["refused"]]),
                         (False, [("gz", "the views blob caps at 32 tags, so it was not created")]))
        self.assertEqual(len(km._timeline_views()["tags"]), km._VIEWS_MAX_TAGS)
        self.assertIsNone(store_tag("z"))
        self.assertEqual(self.notices[-1][1], False)
        # two creates where only one fits: the later one in array order is the one refused
        w3 = json.loads(json.dumps(served))
        w3["tags"] += [{"id": "ga", "name": "a", "color": "", "members": []},
                       {"id": "gb", "name": "b", "color": "", "members": []},
                       {"id": "gc", "name": "c", "color": "", "members": []}]
        try:
            km._views_path().unlink()
        except OSError:
            pass
        self._seed_many(km._VIEWS_MAX_TAGS - 2)
        c = self.post({"type": "setTimelineViews", "writeId": "w3", "views": w3, "edited": ["ga", "gb", "gc"]})
        self.assertEqual([r["tid"] for r in c["refused"]], ["gc"])
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"] if t["id"] in ("ga", "gb", "gc")), ["a", "b"])

    def test_a_posted_33rd_tag_reaches_the_door_and_is_refused_not_sliced_off_by_the_normalizer(self):
        """The normalizer bounds a blob to the cap on every read, and the door read the posted blob
        through it — so a client's 33rd tag was gone before the judge saw it, no row, no notice. The
        door reads under a wider bound and its cap pass refuses the excess; disk reads keep the cap."""
        many = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(km._VIEWS_MAX_TAGS + 1)]
        self.assertEqual(len(km._norm_timeline_views({"tags": many})["tags"]), km._VIEWS_MAX_TAGS, "a read is bounded, as before")
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many}})
        self.assertEqual((a["ok"], [(r["tid"], r["reason"]) for r in a["refused"]]),
                         (False, [("g32", "the views blob caps at 32 tags, so it was not created")]))
        self.assertEqual(len(km._timeline_views()["tags"]), km._VIEWS_MAX_TAGS)


class PastTheDoorBoundNothingVanishesSilently(_Wire):
    """The 2026-09-05 review: the door reads a posted blob under twice the cap so a 33rd
    tag reaches the cap pass — but the normalizer's slice still dropped everything past
    the 64th BEFORE the judge saw it: no row, absent from the ack's blob, invisible to the ack's
    `ok` (computed from rows), so a client whose `edited` named only ids past the bound was acked
    ok and adopted a blob missing its own creates. Now every posted entry the slice leaves unread
    gets a row — "not created" for a new id, "the store's copy was kept" for a store tag whose copy
    fell past the bound (it is not a deletion either) — and nothing is stored past the bound."""

    def _seed_many(self, n):
        tags = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(n)]
        ack = self.post({"type": "setTimelineViews", "writeId": "w0", "views": {"active": "all", "tags": tags}})
        self.assertEqual((ack["ok"], ack["refused"], len(ack["views"]["tags"])), (True, [], n))
        return ack["views"]

    def test_seventy_creates_all_edited_every_posted_id_is_in_the_ack_blob_or_in_a_row(self):
        many = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(70)]
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many},
                       "edited": [t["id"] for t in many]})
        self.assertFalse(a["ok"])
        in_blob = {t["id"] for t in a["views"]["tags"]}
        rows = {r["tid"]: r["reason"] for r in a["refused"]}
        self.assertEqual(in_blob, {"g%d" % i for i in range(32)})
        self.assertEqual(set(rows), {"g%d" % i for i in range(32, 70)}, "every posted id is in the blob or in a row")
        self.assertEqual(rows["g32"], "the views blob caps at 32 tags, so it was not created")
        self.assertEqual(rows["g64"], "a write is read to 64 tags and it was past that bound, so it was not created")
        self.assertEqual(rows["g69"], rows["g64"])
        self.assertEqual(len(km._timeline_views()["tags"]), 32, "nothing past the bound is stored")
        self.assertEqual(len(self.notices), 1)
        text = self.notices[0][0]
        self.assertIn("its changes to 38 tags were not applied", text)
        self.assertIn("reload that dashboard to resync", text)
        self.assertIn('Refused: "t32" (over the cap), "t33" (over the cap), "t34" (over the cap) and 35 more', text,
                      "the labels after the point, as many as fit; the rows carry every reason")
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT)

    def test_creates_past_the_bound_that_edited_names_are_refused_never_acked_ok(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS)
        w = json.loads(json.dumps(served))
        w["tags"] += [{"id": "h%d" % i, "name": "h%d" % i, "color": "", "members": []} for i in range(38)]
        tail = ["h%d" % i for i in range(32, 38)]
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w, "edited": tail})
        self.assertFalse(a["ok"], "the client's own creates were refused, so the ack is not ok")
        rows = {r["tid"]: r["reason"] for r in a["refused"]}
        for h in tail:
            self.assertEqual(rows[h], "a write is read to 64 tags and it was past that bound, so it was not created")
        self.assertEqual(rows["h0"], "it was deleted after your copy was taken, so it was not re-created",
                         "h0..h31 sat within the bound and were not in `edited`: re-creations, quietly")
        self.assertEqual([t["id"] for t in a["views"]["tags"]], [t["id"] for t in served["tags"]])
        self.assertEqual(len(self.notices), 1, "the refused creates were the poster's: loud")
        self.assertIn("(unread)", self.notices[0][0])

    def test_a_duplicate_entry_consumes_a_slot_and_the_create_it_pushes_past_the_bound_gets_a_row(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS)
        w = json.loads(json.dumps(served))
        w["tags"] = w["tags"] + json.loads(json.dumps(w["tags"])) \
            + [{"id": "h0", "name": "mine", "color": "", "members": []}]        # the 65th entry
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w, "edited": ["h0"]})
        self.assertEqual((a["ok"], [(r["tid"], r["name"]) for r in a["refused"]]), (False, [("h0", "mine")]))
        self.assertIsNone(store_tag("mine"))

    def test_a_store_tags_copy_past_the_bound_is_kept_not_deleted(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS)
        creates = [{"id": "n%d" % i, "name": "n%d" % i, "color": "", "members": []} for i in range(33)]
        w = json.loads(json.dumps(served))
        w["tags"] = creates + w["tags"]                    # 65 entries: the store's g31 is the 65th
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w})   # no `edited`: an older client
        self.assertFalse(a["ok"])
        rows = {r["tid"]: r["reason"] for r in a["refused"]}
        self.assertEqual(rows["g31"],
                         "a write is read to 64 tags and its copy was past that bound, so the store's copy was kept")
        self.assertEqual(sorted(k for k in rows if k.startswith("n")), sorted("n%d" % i for i in range(33)),
                         "every create is refused by the cap: the store is full")
        ids = [t["id"] for t in km._timeline_views()["tags"]]
        self.assertEqual(sorted(ids), sorted(t["id"] for t in served["tags"]), "the store is intact, g31 included")
        self.assertIn("its changes to 34 tags were not applied", self.notices[0][0])
        self.assertLessEqual(len(self.notices[0][0]), km.SYNC_NOTICE_FIT)

    def test_a_lens_only_write_touches_no_tag_whatever_the_blob_carries_by_design(self):
        served = self._seed_many(3)
        w = json.loads(json.dumps(served))
        w["tags"] += [{"id": "h%d" % i, "name": "h%d" % i, "color": "", "members": []} for i in range(70)]
        w["actives"] = {"timeline": {"tags": ["t1"]}, "chat": {"all": True}, "outline": {"all": True}}
        a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w, "edited": []})
        self.assertEqual((a["ok"], a["refused"]), (True, []),
                         "edited=[] is the client's word that it changed no tag (docs/read-side.md): no rows")
        self.assertEqual(len(km._timeline_views()["tags"]), 3)
        self.assertEqual(km._timeline_views()["actives"]["timeline"], {"tags": ["t1"]})
        self.assertEqual(self.notices, [])


class ReaderRestampKeepsTheDiskCap(_Wire):
    """The 2026-09-05 review: the reader's first-read stamp and the hidden migration judged
    the file's own content through the door, which since the 2026-09-05 review reads its blob under twice the cap
    while the base is normalized under the cap — so a file holding more than 32 tags had its excess
    (the migration's appended "archived" tag first) judged as creates and refused by the cap pass,
    under a red notice worded as a stale DASHBOARD write ("reload that dashboard to resync"), on a
    read, blaming a dashboard that wrote nothing. The re-stamp now reads the file under the disk cap,
    as every read does, and what the cap leaves out is named in a notice as a fact about the file."""

    def _file(self, n, **extra):
        tags = [{"id": "t%d" % i, "name": "t%d" % i, "color": "", "members": [{"host": "", "sid": "s%d" % i}]}
                for i in range(n)]
        d = dict({"active": "all", "tags": tags}, **extra)
        km._views_path().write_text(json.dumps(d))
        return d

    def test_a_seq_less_40_tag_file_with_hidden_entries_migrates_stamps_and_names_the_drop_as_a_file_fact(self):
        p = km._views_path()
        self._file(40, hidden=[SID3])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            v = km._timeline_views()
        self.assertEqual(len(v["tags"]), km._VIEWS_MAX_TAGS, "served under the cap, as every read")
        self.assertIsInstance(v.get("seq"), int, "stamped")
        on_disk = json.loads(p.read_text())
        self.assertNotIn("hidden", on_disk, "the migration landed: the hidden key is consumed")
        self.assertEqual(len(on_disk["tags"]), km._VIEWS_MAX_TAGS)
        self.assertTrue(all(t.get("mtime") for t in on_disk["tags"]))
        self.assertEqual(len(self.notices), 1, "one notice: the file fact")
        text, ok = self.notices[0]
        self.assertFalse(ok)
        self.assertEqual(text, "the views file held 41 tags, over the store's 32-tag cap; no dashboard wrote this. "
                               "9 tags were dropped when it was re-stamped on read (hidden entries migrated into the "
                               "archived tag): \"t32\" (1 member), \"t33\" (1 member) and 7 more",
                         "cause and count first, the dropped tags after, as many as fit")
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT)
        self.assertNotIn("dashboard write", text)
        self.assertNotIn("reload", text)
        self.assertNotIn("refused", text)
        line = next(ln for ln in err.getvalue().splitlines() if "no dashboard wrote this" in ln)
        for nm in ("t32", "t39"):
            self.assertIn('"%s": s%s' % (nm, nm[1:]), line, "stderr names every dropped tag with its members")
        self.assertIn('"archived": %s' % SID3, line, "the migrated hidden entry is named as what was dropped")
        # idempotent: a cold second read writes nothing and says nothing more
        before = p.read_bytes()
        km._flags_cache.clear()
        km._timeline_views()
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(len(self.notices), 1)

    def test_a_full_store_with_hidden_entries_the_kernel_once_wrote_loses_only_the_archived_tag_and_says_so(self):
        # kernel-producible: the cap and the hidden field were born together (2026-08-18), hidden retired 08-24
        p = km._views_path()
        self._file(km._VIEWS_MAX_TAGS, hidden=[SID2, SID3], seq=7)
        v = km._timeline_views()
        self.assertEqual([t["id"] for t in v["tags"]], ["t%d" % i for i in range(32)], "every real tag stands")
        self.assertIsNone(store_tag("archived"))
        self.assertNotIn("hidden", json.loads(p.read_text()))
        self.assertEqual(len(self.notices), 1)
        text, ok = self.notices[0]
        self.assertFalse(ok)
        self.assertIn('1 tag was dropped when it was re-stamped on read (hidden entries migrated into the archived '
                      'tag): "archived" (2 members)', text)
        self.assertNotIn("dashboard write", text)

    def test_a_31_tag_file_with_hidden_entries_fits_and_nothing_is_said(self):
        self._file(km._VIEWS_MAX_TAGS - 1, hidden=[SID3])
        v = km._timeline_views()
        self.assertEqual([m["sid"] for m in store_tag("archived")["members"]], [SID3])
        self.assertEqual(len(v["tags"]), km._VIEWS_MAX_TAGS)
        self.assertEqual(self.notices, [], "the migration fits: a stamp is not a refusal")

    def test_a_seq_less_file_over_the_cap_is_stamped_under_the_cap_and_the_drop_is_a_file_fact(self):
        p = km._views_path()
        self._file(40, at=1000)
        v = km._timeline_views()
        self.assertEqual(len(v["tags"]), 32)
        self.assertEqual(len(json.loads(p.read_text())["tags"]), 32)
        self.assertEqual(len(self.notices), 1)
        text, ok = self.notices[0]
        self.assertFalse(ok)
        self.assertIn("the views file held 40 tags", text)
        self.assertIn("a store from before the write sequence, stamped once", text)
        self.assertNotIn("partially refused", text)
        self.assertNotIn("dashboard", text.replace("no dashboard wrote this", ""))
        self.assertIn("8 tags were dropped", text)

    def test_the_served_row_carries_the_cause_whole_however_many_tags_were_dropped(self):
        """The 2026-09-05 review: the notice put its point — no dashboard wrote this — LAST,
        after one entry per dropped tag; the kernel serves a sync notice cut at 300 (_sync_notice_rows)
        and the dashboard's bell cuts it again at 240 (SYNC_NOTICE_FIT), so with two or more tags
        dropped the user saw a list of names and never the cause. The served row is the whole notice."""
        self._file(40, hidden=[SID3])
        with self.real_notices():
            with contextlib.redirect_stderr(io.StringIO()):
                km._timeline_views()
            rows = km._sync_notice_rows()
            with km._SYNC_LOCK:
                full = km._SYNC_NOTICES[-1]["text"]
        self.assertEqual(len(rows), 1)
        text = rows[0]["text"]
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(text, full, "nothing the kernel's cap could cut")
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT, "…and nothing the bell's cap will")
        self.assertTrue(text.startswith("the views file held 41 tags, over the store's 32-tag cap; no dashboard wrote this. "
                                        "9 tags were dropped when it was re-stamped on read"))
        self.assertTrue(text.endswith(' and 7 more'))

    def test_the_served_loud_notice_carries_the_count_and_the_remedy_whole(self):
        many = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(70)]
        with self.real_notices():
            with contextlib.redirect_stderr(io.StringIO()):
                a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many},
                               "edited": [t["id"] for t in many]})
            rows = km._sync_notice_rows()
        self.assertFalse(a["ok"])
        self.assertEqual(len(rows), 1)
        text = rows[0]["text"]
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT)
        self.assertTrue(text.startswith("a stale dashboard write was partially refused: its changes to 38 tags were not "
                                        "applied and the store's state was kept; reload that dashboard to resync. Refused: "))
        self.assertTrue(text.endswith(" and 35 more"))
        self.assertEqual(len(a["refused"]), 38, "every reason is in the ack's rows, whole")

    def test_notice_list_names_what_fits_and_counts_the_rest(self):
        nl = km._notice_list
        self.assertEqual(nl("head", ": ", ["a", "b", "c"], cap=100), "head: a, b, c")
        self.assertEqual(nl("head", ": ", [], cap=100), "head")
        self.assertEqual(nl("head", ": ", ["aaaa", "bbbb", "cccc"], cap=len("head: aaaa and 2 more")), "head: aaaa and 2 more")
        self.assertEqual(nl("head", ": ", ["aaaa", "bbbb", "cccc"], cap=len("head: aaaa, bbbb and 1 more") - 1),
                         "head: aaaa and 2 more", "an item is named only with room left for the tail that follows it")
        self.assertEqual(nl("head", ": ", ["aaaa", "bbbb", "c" * 16], cap=len("head: aaaa, bbbb and 1 more")),
                         "head: aaaa, bbbb and 1 more")
        self.assertEqual(nl("head", ": ", ["aaaa", "bbbb"], cap=8), "head", "not even the first fits: the head alone")
        self.assertEqual(nl("head", ": ", ["a"], extra=5, cap=100), "head: a and 5 more", "`extra` counts things never listed")
        self.assertEqual(nl("head", ": ", [], extra=5, cap=100), "head", "…but with nothing named the head carries the count")
        self.assertEqual(nl("", "", ["x: 1", "y: 2"], cap=100, joiner="; "), "x: 1; y: 2")
        self.assertLessEqual(km.SYNC_NOTICE_FIT, 300, "the bell's cap is under the kernel's served cap")

    def test_the_judges_loud_notice_names_the_writer_and_offers_a_reload_only_to_a_dashboard(self):
        self.seed()
        self.assertIsNone(km._edit_tag(tid="gA", add=[SID2])[1])            # newer state in the store
        stale = {"active": "all", "at": 1, "tags": [dict(WEB, mtime=1)]}    # a copy predating it
        km._judge_timeline_views(json.loads(json.dumps(stale)), writer="the views file's re-stamp on read")
        self.assertEqual(len(self.notices), 1)
        self.assertTrue(self.notices[0][0].startswith("the views file's re-stamp on read was partially refused"))
        self.assertNotIn("reload", self.notices[0][0], "no dashboard wrote it: nothing to reload")
        km._judge_timeline_views(json.loads(json.dumps(stale)))
        self.assertTrue(self.notices[1][0].startswith("a stale dashboard write was partially refused"))
        self.assertIn("reload that dashboard to resync", self.notices[1][0])


class ReaderRestampUnwritableNamesTheDrop(_Wire):
    """The 2026-09-05 review: the file-fact notice fired only after the stamp WRITE succeeded.
    On an unwritable or full state dir the reader served and cached the capped blob and said nothing
    about what the cap left out, and the next RMW writer (a tag edit through _edit_tag) wrote
    that cached blob: the file's excess tags were dropped for good with no notice ever filed. The
    notice now fires whether or not the write lands, once per file state, and on the failed write it
    says the drop becomes permanent at the next write unless the file is brought under the cap."""

    def setUp(self):
        super().setUp()
        self.addCleanup(km._VIEWS_RESTAMP_ERR.__setitem__, 0, km._VIEWS_RESTAMP_ERR[0])
        km._VIEWS_RESTAMP_ERR[0] = None
        self._real_write = km._atomic_write

    def tearDown(self):
        km._atomic_write = self._real_write
        super().tearDown()

    def _file(self, n, **extra):
        tags = [{"id": "t%d" % i, "name": "t%d" % i, "color": "", "members": [{"host": "", "sid": "s%d" % i}]}
                for i in range(n)]
        km._views_path().write_text(json.dumps(dict({"active": "all", "tags": tags}, **extra)))

    def _full_disk(self):
        def failing(path, text, mode=None):
            raise OSError(errno.ENOSPC, "No space left on device")
        km._atomic_write = failing

    def test_the_drop_is_named_when_the_stamp_cannot_be_written_and_the_notice_says_it_becomes_permanent(self):
        p = km._views_path()
        self._file(40, at=1000)
        self._full_disk()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            v = km._timeline_views()
            self.assertEqual(len(v["tags"]), 32, "served under the cap, as every read")
            self.assertNotIn("seq", v, "unstamped: the write did not land")
            self.assertEqual(len(json.loads(p.read_text())["tags"]), 40, "the file still holds the excess")
            self.assertEqual(len(self.notices), 1, "the file fact is said although the stamp did not land")
            text, ok = self.notices[0]
            self.assertFalse(ok)
            self.assertEqual(text, "the views file held 40 tags, over the store's 32-tag cap; no dashboard wrote this. "
                                   "Its re-stamp could not be written — 8 tags are not served and are lost at the next "
                                   "write unless the file is brought under the cap first",
                             "cause and count first; with no room left for a name the head stands alone")
            self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT)
            self.assertIs(km._timeline_views(), v, "the next read is a cache hit under the file's key…")
            self.assertEqual(len(self.notices), 1, "…so the fact is said once per file state")
        lines = err.getvalue().splitlines()
        self.assertEqual(len([ln for ln in lines if "could not be re-stamped" in ln]), 1)
        self.assertIn("the file as read, under the cap, unstamped", next(ln for ln in lines if "could not be re-stamped" in ln))
        fact = next(ln for ln in lines if "no dashboard wrote this" in ln)
        for i in range(32, 40):
            self.assertIn('"t%d": s%d' % (i, i), fact, "stderr names every dropped tag with its members")
        # the writer is back: the next RMW write persists the served blob — the drop the notice announced
        km._atomic_write = self._real_write
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._edit_tag(tid="t0", add=[SID2])[1])
        on_disk = json.loads(p.read_text())
        self.assertEqual(len(on_disk["tags"]), 32)
        self.assertNotIn("t39", [t["id"] for t in on_disk["tags"]], "permanent now, as the notice said it would be")
        self.assertEqual(len(self.notices), 1, "said when it could still be prevented; the write that makes it so adds nothing")
        km._flags_cache.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()
        self.assertEqual(len(self.notices), 1, "a clean 32-tag stamped store: nothing left to name")

    def test_one_dropped_tag_is_named_in_the_unwritable_notice(self):
        self._file(33, at=1000)
        self._full_disk()
        with contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()
        self.assertEqual(len(self.notices), 1)
        text, ok = self.notices[0]
        self.assertFalse(ok)
        self.assertTrue(text.endswith("Its re-stamp could not be written — 1 tag is not served and is lost at the next "
                                      'write unless the file is brought under the cap first: "t32" (1 member)'), text)
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT)

    def test_a_file_under_the_cap_on_a_full_disk_files_no_file_fact(self):
        self._file(5, at=1000)
        self._full_disk()
        with contextlib.redirect_stderr(io.StringIO()):
            v = km._timeline_views()
        self.assertEqual(len(v["tags"]), 5)
        self.assertEqual(self.notices, [], "an unwritable store alone is a log line, not a notice")

    def test_a_cold_read_while_the_disk_stays_full_says_it_again_the_file_state_is_unchanged_and_unfixed(self):
        self._file(40, at=1000)
        self._full_disk()
        with contextlib.redirect_stderr(io.StringIO()):
            km._timeline_views()
            km._flags_cache.clear()                            # a restart: the file is as it was, still over the cap
            km._timeline_views()
        self.assertEqual(len(self.notices), 2, "once per file state: a cold read of the same unfixed file names it again")
        self.assertEqual(self.notices[0], self.notices[1])

    def test_two_cold_readers_racing_to_the_lock_on_a_full_disk_name_the_drop_once(self):
        """The 2026-09-05 review: the cache hit check runs before the file lock, and the
        in-lock re-check compared the stat key alone — which a FAILED write leaves unchanged. Two readers
        that both missed a cold cache (the pusher and a handler, on a kernel starting over a full disk)
        serialized on the lock, and the loser re-stated the same key, judged again, failed again and
        filed the file-fact notice again: two identical red notices for one file state. The loser now
        finds the winner's cache entry under the lock and serves it, as the next read would."""
        self._file(40, at=1000)
        self._full_disk()
        real = km._views_restamp
        gate = threading.Barrier(2)

        def restamp(d, hit):
            # runs after the hit check and before the lock: both readers pass the check before either
            # takes the lock, the window the race needs
            try:
                gate.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            return real(d, hit)
        km._views_restamp = restamp
        served, failures = [], []

        def read():
            try:
                served.append(len(km._timeline_views()["tags"]))
            except Exception as e:                            # pragma: no cover - the assertion below reports it
                failures.append(repr(e))
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                readers = [threading.Thread(target=read) for _ in range(2)]
                for t in readers:
                    t.start()
                for t in readers:
                    t.join(timeout=30)
        finally:
            km._views_restamp = real
        self.assertEqual(failures, [])
        self.assertEqual(served, [32, 32], "both readers are answered, under the cap")
        self.assertEqual(len(self.notices), 1, "one file state, one notice: the loser serves the winner's cache entry")
        lines = err.getvalue().splitlines()
        self.assertEqual(len([ln for ln in lines if "no dashboard wrote this" in ln]), 1, "…and one stderr file fact")
        self.assertEqual(len([ln for ln in lines if "could not be re-stamped" in ln]), 1)
        self.assertEqual(len(json.loads(km._views_path().read_text())["tags"]), 40, "the file is as it was")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(len(km._timeline_views()["tags"]), 32)
        self.assertEqual(len(self.notices), 1, "a warm read adds nothing")


class RefusalRowsAreBounded(_Wire):
    """The 2026-09-05 review: the review gave every posted entry past the door's read bound a
    refusal row of its own, and with it the rows, the loud notice and the ack's `error` became O(N) in
    the posted array. A 100k-entry post (a client bug, or any local page holding the socket — the WS
    reader takes frames of any length) drew a ~22 MB ack that overran WS_QUEUE_BYTES: the poster's own
    ack marked its socket dead before the ack was queued, so it never learned the write's outcome. Now
    the first 64 unread entries get rows, the rest are one summary row carrying their count and how
    many of them `edited` named, `ok` still reads the summary, and the ack stays small."""

    N = 10000

    def _many(self, n, start=0):
        return [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(start, start + n)]

    def _real_client(self):
        """A client built the way the accept path builds one — the byte budget and its drop rule live in
        its `send` — with no sender thread and a socket that only records a shutdown."""
        class Sock:
            shut = 0

            def shutdown(self, how):
                self.shut += 1
        sock = Sock()
        client, q, _lock = km._new_ws_client("timeline", "w-dash", sock, start_sender=False)
        return client, q, sock

    def test_a_10000_entry_post_is_acked_in_bounded_rows_on_a_socket_that_stays_alive(self):
        client, q, sock = self._real_client()
        many = self._many(self.N)
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(self.handler, {"type": "setTimelineViews", "writeId": "w1",
                                                   "views": {"active": "all", "tags": many},
                                                   "edited": [t["id"] for t in many]}, client)
        self.assertTrue(client["alive"], "the poster's own ack no longer drops it")
        self.assertEqual(sock.shut, 0)
        self.assertEqual(q.qsize(), 1, "the ack was queued")
        frame = q.get_nowait()
        ack = json.loads(frame)
        self.assertEqual((ack["type"], ack["writeId"], ack["ok"]), ("viewsAck", "w1", False))
        bound = km._VIEWS_MAX_TAGS * 2
        self.assertLess(len(frame), 100_000, "bounded — a 10k post drew a ~2 MB ack before, a 100k post ~22 MB")
        self.assertLessEqual(len(ack["refused"]), 2 * bound + km._VIEWS_MAX_TAGS + 1,
                             "the cap pass's rows, 64 named unread rows, one summary — never one per posted entry")
        named = [r for r in ack["refused"] if r.get("tid")]
        summary = [r for r in ack["refused"] if not r.get("tid")]
        self.assertEqual([r["tid"] for r in named if "read to" in r["reason"]], ["g%d" % i for i in range(bound, 2 * bound)],
                         "the first 64 unread entries, in array order, get rows of their own")
        self.assertEqual(summary, [{"reason": "9872 more entries past the %d-tag read bound were not read "
                                              "(9872 of them this write edited)" % bound,
                                    "more": 9872, "moreEdited": 9872}])
        self.assertLessEqual(len(ack["error"]), km._ACK_ERROR_CAP)
        self.assertRegex(ack["error"], r" and \d+ more$", "the one-line form is bounded the same way")
        self.assertEqual(len(km._timeline_views()["tags"]), 32, "nothing past the bound is stored")
        self.assertEqual(len(self.notices), 1)
        text = self.notices[0][0]
        self.assertLessEqual(len(text), km.SYNC_NOTICE_FIT, "the loud notice is bounded too")
        self.assertIn("its changes to 9968 tags were not applied", text)
        self.assertIn("reload that dashboard to resync", text)

    def test_ok_is_false_when_the_clients_only_create_sits_past_the_row_bound(self):
        many = self._many(self.N)
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many},
                           "edited": ["g9999"]})
        self.assertFalse(a["ok"], "the client's own create was not read: never acked ok, row or no row")
        self.assertNotIn("g9999", [r.get("tid") for r in a["refused"]], "no row of its own: it is counted in the summary")
        summary = next(r for r in a["refused"] if not r.get("tid"))
        self.assertEqual((summary["more"], summary["moreEdited"]), (9872, 1))
        self.assertIn("(1 of them this write edited)", summary["reason"])
        self.assertEqual(len(self.notices), 1, "a lost edit is loud…")
        self.assertEqual(self.notices[0][0], "a stale dashboard write was partially refused: its changes to 1 tag were not "
                                             "applied and the store's state was kept; reload that dashboard to resync.",
                         "…counted, with nothing to name: the summary's entries carry no label")

    def test_ok_is_true_when_nothing_past_the_bound_is_the_clients_own(self):
        many = self._many(self.N)
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many},
                           "edited": ["g0"]})
        self.assertTrue(a["ok"], "its one create landed; every other entry is a stale copy it did not edit")
        self.assertEqual([t["id"] for t in a["views"]["tags"]], ["g0"])
        summary = next(r for r in a["refused"] if not r.get("tid"))
        self.assertEqual(summary["moreEdited"], 0)
        self.assertEqual(self.notices, [], "quiet: nothing the user did was refused")

    def test_store_tags_whose_copies_sit_past_the_row_bound_are_all_kept(self):
        tags = self._many(km._VIEWS_MAX_TAGS, start=50000)
        served = self.post({"type": "setTimelineViews", "writeId": "w0", "views": {"active": "all", "tags": tags}})["views"]
        w = {"active": "all", "at": served["at"], "tags": self._many(self.N) + json.loads(json.dumps(served["tags"]))}
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": w})     # no `edited`: an older client
        self.assertFalse(a["ok"])
        ids = sorted(t["id"] for t in km._timeline_views()["tags"])
        self.assertEqual(ids, sorted(t["id"] for t in served["tags"]),
                         "every store tag sat past the row bound and every one was kept: the bound is on rows, not keeps")
        self.assertLessEqual(len(a["refused"]), 2 * km._VIEWS_MAX_TAGS * 2 + km._VIEWS_MAX_TAGS + 1)
        self.assertEqual(next(r for r in a["refused"] if not r.get("tid"))["moreEdited"], 0, "no `edited`: nothing to count")


class AckErrorNamesThePostersRefusalFirst(_Wire):
    """The 2026-09-05 review: the review bounded the ack's one-line `error` at 1000 characters
    but kept the judge's order, in which the quiet rows (differing copies of tags the poster did not
    edit, acked ok on their own) precede the cap pass. With eight or more quiet rows the toast named
    only refusals the user never made and cut the one row that made `ok` false. Now the rows `edited`
    names — or the summary row counting one — lead, and the quiet rows follow as far as the cap allows;
    the rows themselves keep the judge's order, and so does the line when the write carries no
    `edited`."""

    QUIET = "your copy of it differs from the store's and this write did not edit it, so the store's copy was kept"
    CAP = "the views blob caps at 32 tags, so it was not created"

    def _seed_many(self, n):
        tags = [{"id": "g%d" % i, "name": "tag-name-%02d" % i, "color": "", "members": []} for i in range(n)]
        ack = self.post({"type": "setTimelineViews", "writeId": "w0", "views": {"active": "all", "tags": tags}})
        self.assertEqual((ack["ok"], ack["refused"], len(ack["views"]["tags"])), (True, [], n))
        return ack["views"]

    def test_the_finders_scenario_a_create_over_the_cap_behind_ten_differing_copies(self):
        """The store is full; a dashboard that slept through a batch of recolors elsewhere posts its whole
        blob with its one create as `edited`: ten of its copies differ, and the create is refused by the
        cap. The rows list the ten quiet refusals then the create's, and the line leads with the create."""
        served = self._seed_many(km._VIEWS_MAX_TAGS)
        stale = json.loads(json.dumps(served))
        for t in stale["tags"][:10]:
            t["color"] = "#000000"
        stale["tags"].append({"id": "mine", "name": "mine", "color": "#DD42FF", "members": []})
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale, "edited": ["mine"]})
        self.assertFalse(a["ok"], "the poster's own create was refused")
        self.assertEqual([r["tid"] for r in a["refused"]], ["g%d" % i for i in range(10)] + ["mine"],
                         "the rows keep the judge's order: the quiet ten, then the cap pass")
        self.assertEqual(a["refused"][-1]["reason"], self.CAP)
        self.assertTrue(a["error"].startswith('"mine": %s; "tag-name-00": %s' % (self.CAP, self.QUIET)),
                        "the line leads with the refusal the user made, then the quiet rows in judge order: " + a["error"])
        self.assertLessEqual(len(a["error"]), km._ACK_ERROR_CAP)
        self.assertRegex(a["error"], r" and \d+ more$", "the quiet rows overflow the cap, not the poster's")
        self.assertEqual(len(self.notices), 1)
        self.assertIn('"mine" (over the cap)', self.notices[0][0], "the notice agrees with the line")
        self.assertNotIn("mine", [t["id"] for t in km._timeline_views()["tags"]])

    def test_the_summary_row_counting_the_posters_create_leads_the_line(self):
        many = [{"id": "g%d" % i, "name": "t%d" % i, "color": "", "members": []} for i in range(10000)]
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": {"active": "all", "tags": many},
                           "edited": ["g9999"]})
        self.assertFalse(a["ok"])
        summary = next(r for r in a["refused"] if not r.get("tid"))
        self.assertTrue(a["error"].startswith(summary["reason"] + "; "),
                        "the row that made ok false leads, nameless as it is: " + a["error"][:200])
        self.assertIn("(1 of them this write edited)", a["error"])
        self.assertLessEqual(len(a["error"]), km._ACK_ERROR_CAP)

    def test_without_edited_every_row_is_loud_and_the_line_keeps_the_judges_order(self):
        served = self._seed_many(km._VIEWS_MAX_TAGS)
        stale = json.loads(json.dumps(served))
        time.sleep(1.1)
        with contextlib.redirect_stderr(io.StringIO()):
            for i in range(10):
                self.assertIsNone(km._edit_tag(tid="g%d" % i, color="#DD42FF")[1])     # newer edits elsewhere
        stale["tags"].append({"id": "mine", "name": "mine", "color": "", "members": []})
        with contextlib.redirect_stderr(io.StringIO()):
            a = self.post({"type": "setTimelineViews", "writeId": "w1", "views": stale})      # an older client
        self.assertFalse(a["ok"])
        self.assertEqual([r["tid"] for r in a["refused"]], ["g%d" % i for i in range(10)] + ["mine"])
        self.assertTrue(a["error"].startswith('"tag-name-00": your copy predates a newer edit'),
                        "no `edited`: every refusal is the poster's, so the judge's order stands: " + a["error"][:120])

    def test_the_rule_at_the_ack_itself(self):
        """The ordering rule without the door: the poster's rows (a tid `edited` names, or the summary row
        with `moreEdited`) first in judge order, then the rest; judge order when `edited` is absent or
        names none of them; the rows in the ack are never reordered."""
        quiet = [{"tid": "g%d" % i, "name": "tag-name-%02d" % i, "reason": self.QUIET} for i in range(10)]
        mine = {"tid": "mine", "name": "mine", "reason": self.CAP}
        summary = {"reason": "5 more entries past the 64-tag read bound were not read (1 of them this write edited)",
                   "more": 5, "moreEdited": 1}

        def ack(refused, edited):
            sent = []
            km._ack_views_write({"send": lambda s: sent.append(json.loads(s))}, "viewsAck", "w", False,
                                refused=refused, edited=edited)
            self.assertEqual(sent[0]["refused"], refused, "the rows keep the judge's order")
            return sent[0]["error"]
        line = ack(quiet + [mine], ["mine"])
        self.assertTrue(line.startswith('"mine": %s; "tag-name-00": %s; "tag-name-01"' % (self.CAP, self.QUIET)), line)
        self.assertRegex(line, r" and \d+ more$")
        self.assertLessEqual(len(line), km._ACK_ERROR_CAP)
        line = ack(quiet[:3] + [mine, summary], ["mine"])
        self.assertTrue(line.startswith('"mine": %s; %s; "tag-name-00"' % (self.CAP, summary["reason"])), line)
        line = ack(quiet + [summary], ["g9999"])
        self.assertTrue(line.startswith(summary["reason"] + '; "tag-name-00"'), line)
        self.assertTrue(ack(quiet + [mine], None).startswith('"tag-name-00"'), "no `edited`: judge order")
        self.assertTrue(ack(quiet + [mine], ["absent"]).startswith('"tag-name-00"'), "none of the poster's: judge order")
        self.assertTrue(ack(quiet + [mine], []).startswith('"tag-name-00"'))


class BlobLessConnectPushCaps(_Wire):
    """A connect push that serves NO views blob (a pane whose push carries none) leaves the caps frame
    nothing to name from the served frames. It then carries the STORE's current seq, the seq the
    next push serves, and is null only with no store at all: with null, after a restart over a store
    restored from an older copy, nothing re-armed the page's gate — the next pusher tabOrder (the
    store's older seq) was rejected and kept, and no second caps frame ever came."""

    def _ready(self):
        km.Handler._dispatch_ws(self.handler, {"type": "ready"}, self.client)
        return next(m for m in reversed(self.sent) if m["type"] == "caps")

    def test_a_blob_less_connect_push_names_the_stores_seq_and_the_pushers_next_frame_carries_it(self):
        import tempfile as _tf
        from pathlib import Path as _P
        s0 = self.seed()["seq"]
        self.handler._push_one = lambda c: km._send_client(c, ("working",), {"type": "working", "names": []})
        n = len(self.sent)                                             # the seed's own ack sits before this
        caps = self._ready()
        self.assertEqual([m for m in self.sent[n:] if km._views_seq_of(m) is not None], [],
                         "no frame of the connect push carried a views blob")
        self.assertEqual(caps["viewsSeq"], s0, "the store's current seq: the one the next push serves")
        # the pusher's next tabOrder carries the store's blob under that very seq, which the client's gate
        # adopts because the caps frame named it (the real pusher, through the test_tab_meta_push.py stubs)
        tmp = _tf.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        names = _P(tmp) / "names"
        names.mkdir()
        (names / SID1).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        saved = (km.NAMES, km._live_map, km._mark_views_dirty, km._chat_tab_sessions, km._cached_feed)
        try:
            km.NAMES = names
            km._live_map = lambda: {}
            km._mark_views_dirty = lambda: None
            km._chat_tab_sessions = lambda now, live: [{"sid": SID1, "name": "web", "path": os.path.join(tmp, "none.jsonl"),
                                                         "anchor": SID1}]
            km._cached_feed = lambda *a, **k: None
            self.client["app"] = "chat"
            self.client["sent"] = {}
            km._push([self.client])
        finally:
            (km.NAMES, km._live_map, km._mark_views_dirty, km._chat_tab_sessions, km._cached_feed) = saved
        tab = [m for m in self.sent if m["type"] == "tabOrder"]
        self.assertEqual(len(tab), 1)
        self.assertEqual(tab[0]["views"]["seq"], s0)
        self.assertEqual([m["type"] for m in self.sent if m["type"] == "caps"], ["caps"], "no second caps frame: none is needed")

    def test_a_write_between_a_blob_less_push_and_the_caps_frame_moves_what_the_caps_names(self):
        """With no blob served there is no served blob to name: the caps frame names the store as of the
        caps frame — the post-write seq, which is what the next push carries (Capability pins the
        served-blob case, where a write after the last served frame is NOT what the caps names)."""
        s0 = self.seed()["seq"]

        def push(c):
            km._send_client(c, ("working",), {"type": "working", "names": []})
            self.assertIsNone(km._edit_tag(tid="gA", add=[SID2])[1])
        self.handler._push_one = push
        caps = self._ready()
        s1 = km._views_client()["seq"]
        self.assertGreater(s1, s0, "the write moved the store on")
        self.assertEqual(caps["viewsSeq"], s1)

    def test_a_restored_older_store_after_a_restart_is_what_the_caps_names(self):
        """The failure's shape end to end on the kernel side: the page holds a seq from before the restore,
        the restarted kernel (a cold cache, no floor) serves the restored file under its old seq, the
        connect push carries no blob — and the caps frame names that old seq, which the next pusher frame
        carries, so the client's rule (adopt a later frame whose seq equals viewsSeq even below the held
        one) has the event it needs."""
        s_page = self.seed()["seq"]
        p = km._views_path()
        older = json.loads(p.read_text())
        older["seq"] = s_page - 1000
        p.write_text(json.dumps(older))
        km._flags_cache.clear()
        km._VIEWS_SEQ_FLOOR.clear()                                    # a fresh process: nothing served, no floor
        self.assertEqual(km._timeline_views()["seq"], s_page - 1000, "served under its old seq, no re-stamp")
        self.handler._push_one = lambda c: km._send_client(c, ("working",), {"type": "working", "names": []})
        caps = self._ready()
        self.assertEqual(caps["viewsSeq"], s_page - 1000)
        self.assertEqual(self.notices, [])


class WebBootWiring(_Wire):
    """The kernel-served timeline page: the inline _TIMELINE_BOOT twin of timeline-boot.ts exposes
    the targeted-edit bridge and routes both acks to the panel (timeline-boot.test.ts pins the two
    bridge sets equal). Reads the kernel's source only; a _Wire so it runs under a state root of its
    own like every other class in the module."""

    def test_the_bridge_and_the_ack_dispatch(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('window.__rompTimelineTagEdit=function(writeId,edit){post({type:"tagEdit",writeId:writeId,edit:edit});};', src)
        self.assertIn('window.__rompTimelineSetViews=function(views,writeId,edited){post({type:"setTimelineViews",views:views,writeId:writeId,edited:edited||[]});};', src)
        self.assertIn('else if((m.type==="tagEditAck"||m.type==="viewsAck")&&panel.viewsAck)panel.viewsAck(m);', src)


if __name__ == "__main__":
    raise SystemExit("run under pytest: ~/.venvs/romptest/bin/python -m pytest %s -q -p no:cacheprovider" % __file__)
