#!/usr/bin/env python3
"""Tag federation v2 (the user 2026-08-29): a tag DELETE / RENAME / member-REMOVE that failed on an
attached-but-unreachable host PERSISTS — a kernel-side journal (pending-tag-edits.json) applies it
once when that host answers again — so a delete propagates to every federated kernel instead of the
down host's surviving copy resurrecting the name in the union. The 2026-08-24 loud-refusal design
stands (tagEditFailed still fires, now saying "queued"); what changed is that the intent outlives it.

The late apply moves state only on evidence (the cards-move rule): the host's views are fetched
FRESH at apply time, a same-named tag CREATED there after the ruling survives (tag ids encode their
creation ms), and the same tag EDITED there after the ruling makes the queued edit yield (the v2
per-tag mtime, stamped at the store's one write door). ADD never queues. Synthetic hosts/sids only.
"""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
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
km = load_source("romp_kernel_tf2", os.path.join(BIN, "romp-kernel"))

HOST = "TESTHOST"


def _fresh_journal():
    """Reset the journal (file + cache) between tests — the cache is module state."""
    try:
        km._pending_tag_path().unlink()
    except OSError:
        pass
    with km._PENDING_TAG_LOCK:
        km._PENDING_TAG_CACHE["rows"] = None
    getattr(km, "_pending_tag_faults", {}).clear()   # the once-per-episode fault lines are module state too


def _attach(views=None, status="up"):
    """A synthetic attached-host row in the live registry; returns the row."""
    r = {"host": HOST, "status": status, "local_port": 1, "token": ""}
    if views is not None:
        r["views"] = views
    km._remotes.clear()
    km._remotes[HOST] = r
    return r


def _remote_tag(tid, name, members=(), mtime=0):
    t = {"id": tid, "name": name, "color": "", "members": list(members)}
    if mtime:
        t["mtime"] = mtime
    return t


class Queueing(unittest.TestCase):
    def setUp(self):
        _fresh_journal()
        _attach(views={"tags": [_remote_tag("g" + km._b36(int(time.time() * 1000) - 10_000_000), "web")]})

    def tearDown(self):
        _fresh_journal()
        km._remotes.clear()

    def test_delete_journals_with_the_cached_identity(self):
        self.assertTrue(km._queue_pending_tag_edit(HOST, {"name": "web", "delete": True}))
        rows = km._pending_tag_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["host"], rows[0]["name"], rows[0]["delete"]), (HOST, "web", True))
        self.assertTrue(rows[0]["tagId"].startswith("g"),
                        "the ruling captures WHICH tag it ruled on, from the host's cached views")
        self.assertAlmostEqual(rows[0]["ruledAt"], time.time(), delta=30)

    def test_add_and_color_never_queue(self):
        self.assertFalse(km._queue_pending_tag_edit(HOST, {"name": "web", "add": ["s1"]}))
        self.assertFalse(km._queue_pending_tag_edit(HOST, {"name": "web", "color": "#123456"}))
        self.assertEqual(km._pending_tag_rows(), [])

    def test_an_unattached_host_never_queues(self):
        km._remotes.clear()
        self.assertFalse(km._queue_pending_tag_edit("gone-host", {"name": "web", "delete": True}),
                         "no reattach event is coming for a detached host — detach was its own intent")

    def test_a_delete_supersedes_and_then_rules(self):
        km._queue_pending_tag_edit(HOST, {"name": "web", "remove": ["s1"]})
        km._queue_pending_tag_edit(HOST, {"name": "web", "rename": "site"})
        km._queue_pending_tag_edit(HOST, {"name": "web", "delete": True})
        rows = km._pending_tag_rows()
        self.assertEqual([r.get("delete") for r in rows], [True],
                         "the delete supersedes every earlier row for its (host, name)")
        km._queue_pending_tag_edit(HOST, {"name": "web", "remove": ["s2"]})
        self.assertEqual(len(km._pending_tag_rows()), 1, "…and refuses later rows: the delete already rules")

    def test_removes_merge(self):
        km._queue_pending_tag_edit(HOST, {"name": "web", "remove": ["s1"]})
        km._queue_pending_tag_edit(HOST, {"name": "web", "remove": ["s2", "s1"]})
        rows = [r for r in km._pending_tag_rows() if r.get("remove")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remove"], ["s1", "s2"])

    def test_the_journal_survives_a_kernel_restart(self):
        km._queue_pending_tag_edit(HOST, {"name": "web", "delete": True})
        with km._PENDING_TAG_LOCK:
            km._PENDING_TAG_CACHE["rows"] = None      # a fresh process knows nothing — the file is the truth
        rows = km._pending_tag_rows()
        self.assertEqual([(r["host"], r["name"]) for r in rows], [(HOST, "web")])


class LateApply(unittest.TestCase):
    """_apply_pending_tag_edits against a scripted host: _poll_remote_views and _remote_forward are
    seams (monkeypatched per test), so every leg drives the REAL decision code."""

    def setUp(self):
        _fresh_journal()
        self.r = _attach(views={"tags": [_remote_tag("g100", "web")]})
        self.forwarded = []
        self._orig_poll, self._orig_fwd = km._poll_remote_views, km._remote_forward
        km._remote_forward = lambda r, path, body: (self.forwarded.append((path, body))
                                                    or {"ok": True, "deleted": True})

    def tearDown(self):
        km._poll_remote_views, km._remote_forward = self._orig_poll, self._orig_fwd
        _fresh_journal()
        km._remotes.clear()

    def _host_answers(self, tags):
        km._poll_remote_views = lambda r: {"tags": tags}

    def _rule(self, body, name="web"):
        self.assertTrue(km._queue_pending_tag_edit(HOST, dict(body, name=name)))
        return km._pending_tag_rows()[-1]

    def test_reattach_applies_the_delete_once_and_the_union_is_clean(self):
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web")])       # same identity, untouched since
        self.assertEqual(km._apply_pending_tag_edits(self.r), 1)
        self.assertEqual(self.forwarded, [("/tag", {"name": "web", "delete": True})])
        self.assertEqual(km._pending_tag_rows(), [], "a terminal outcome retires the row — once, ever")
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0, "nothing pending → nothing re-applies")

    def test_a_same_named_tag_created_after_the_ruling_survives(self):
        self._rule({"delete": True})
        newer = "g" + km._b36(int((time.time() + 60) * 1000))  # created AFTER ruledAt
        self._host_answers([_remote_tag(newer, "web")])
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
        self.assertEqual(self.forwarded, [], "new information lives — nothing is forwarded")
        self.assertEqual(km._pending_tag_rows(), [], "…and the stale ruling retires")

    def test_a_post_ruling_edit_on_the_host_makes_the_ruling_yield(self):
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web", mtime=int(time.time()) + 60)])
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
        self.assertEqual(self.forwarded, [],
                         "a writer whose evidence predates newer information stands down")
        self.assertEqual(km._pending_tag_rows(), [])

    def test_already_gone_retires_without_a_forward(self):
        self._rule({"delete": True})
        self._host_answers([])
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
        self.assertEqual(self.forwarded, [])
        self.assertEqual(km._pending_tag_rows(), [])

    def test_rename_and_member_remove_legs(self):
        self._rule({"rename": "site"})
        self._rule({"remove": ["s1"]}, name="api")
        self.r["views"] = {"tags": [_remote_tag("g100", "web"), _remote_tag("g200", "api")]}
        self._host_answers([_remote_tag("g100", "web"), _remote_tag("g200", "api", members=["s1"])])
        self.assertEqual(km._apply_pending_tag_edits(self.r), 2)
        self.assertIn(("/tag", {"name": "web", "rename": "site"}), self.forwarded)
        self.assertIn(("/tag", {"name": "api", "remove": ["s1"]}), self.forwarded)
        self.assertEqual(km._pending_tag_rows(), [])

    def test_transport_failure_keeps_the_row_for_the_next_pass(self):
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web")])
        km._remote_forward = lambda r, path, body: None
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
        self.assertEqual(len(km._pending_tag_rows()), 1, "a link that drops mid-apply loses nothing")

    def test_unreadable_views_waits_ask_the_host_never_guess(self):
        self._rule({"delete": True})
        km._poll_remote_views = lambda r: None
        self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
        self.assertEqual(self.forwarded, [])
        self.assertEqual(len(km._pending_tag_rows()), 1)

    # the /tag route's STORE-FAULT refusal: 200 {ok:false, retryable:true, error} -- the disk's answer,
    # not the host's ruling (the route's own shape; the PR-watch route wears the same key on a save fault)
    _STORE_FAULT = {"ok": False, "retryable": True,
                    "error": "the tag store could not be read (read failed: [Errno 5] Input/output error) \u2014 retry"}

    def _pass(self, answer):
        """One apply pass against a host whose /tag answers `answer`; returns (applied, stderr text)."""
        km._remote_forward = lambda r, path, body: (self.forwarded.append((path, body)) or dict(answer))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = km._apply_pending_tag_edits(self.r)
        return n, err.getvalue()

    def _dial_outcomes(self):
        try:
            recs = [json.loads(ln) for ln in km.TUNNEL_LOG.read_text().splitlines() if ln.strip()]
        except OSError:
            recs = []
        return [x.get("outcome") for x in recs if x.get("event") == "pending-tag-edit"]

    def test_a_retryable_store_fault_on_the_host_keeps_the_row_and_the_next_pass_lands_it(self):
        """On main the host's store fault retired the row as "refused by the host": a pending delete was lost
        to a transient disk fault on the host -- the case the journal exists to survive."""
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web")])
        n, err = self._pass(self._STORE_FAULT)
        self.assertEqual(n, 0)
        self.assertEqual(len(km._pending_tag_rows()), 1, "the disk's answer is not the host's ruling: the row stays")
        lines = [ln for ln in err.splitlines() if "pending-tag-edits" in ln]
        self.assertEqual(len(lines), 1, err)
        for word in ('delete of "web"', HOST, "could not be read", "retrying next pass"):
            self.assertIn(word, lines[0])
        v = km._views_client()
        self.assertEqual(v.get("pendingTagEdits"), [{"host": HOST, "name": "web", "op": "delete"}])
        self.assertEqual(next(t for t in v["remoteTags"] if t["name"] == "web").get("pending"), "delete",
                         "the badge stays while the edit is still owed")
        self.assertIn("the host's tag store faulted: the tag store could not be read (read failed: [Errno 5] "
                      "Input/output error) \u2014 retry \u2014 kept; retried every pass, recorded once", self._dial_outcomes())
        # the next pass: the host's store is back, and the SAME row lands
        n, err = self._pass({"ok": True, "deleted": True})
        self.assertEqual(n, 1)
        self.assertEqual([b for _, b in self.forwarded], [{"name": "web", "delete": True}] * 2)
        self.assertEqual(km._pending_tag_rows(), [])
        self.assertEqual(err, "", "landing is not a fault")
        self.assertEqual(self._dial_outcomes()[-1], "applied after 1 faulting pass", "the landing record carries the count")
        v = km._views_client()
        self.assertNotIn("pendingTagEdits", v)
        self.assertIsNone(next(t for t in v["remoteTags"] if t["name"] == "web").get("pending"), "the badge clears")
        self.assertEqual(km._pending_tag_faults, {}, "the episode ended with its row")

    def test_a_refusal_without_retryable_is_the_hosts_ruling_and_retires(self):
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web")])
        n, err = self._pass({"ok": False, "error": 'no tag named "web"'})
        self.assertEqual(n, 0)
        self.assertEqual(km._pending_tag_rows(), [], "the host's own words are terminal, as before")
        self.assertEqual(err, "")
        self.assertIn('refused by the host: no tag named "web"', self._dial_outcomes())

    def test_a_host_that_keeps_faulting_is_said_once_and_a_fresh_ruling_afresh(self):
        self._rule({"delete": True})
        self._host_answers([_remote_tag("g100", "web")])
        seen = len(self._dial_outcomes())          # the dial log is the module's shared state: count from here
        said = []
        for _ in range(3):
            n, err = self._pass(self._STORE_FAULT)
            self.assertEqual(n, 0)
            self.assertEqual(len(km._pending_tag_rows()), 1, "still pending after every faulting pass")
            said += [ln for ln in err.splitlines() if "pending-tag-edits" in ln]
        self.assertEqual(len(said), 1, "one stderr line per row per episode, not one per pass")
        self.assertEqual(len(self.forwarded), 3, "…while every pass still asks the host")
        self.assertEqual(len(self._dial_outcomes()[seen:]), 1,
                         "one dial record per episode too: a host whose disk stays bad must not rotate the dial log away")
        self.assertTrue(self._dial_outcomes()[-1].startswith("the host's tag store faulted: "))
        # the episode ends when the row retires -- here on the host's own ruling, which says how long it took
        n, err = self._pass({"ok": False, "error": 'no tag named "web"'})
        self.assertEqual((n, km._pending_tag_rows(), err), (0, [], ""))
        self.assertEqual(self._dial_outcomes()[-1], 'refused by the host: no tag named "web" after 3 faulting passes')
        self.assertEqual(km._pending_tag_faults, {})
        # a NEW ruling on the same tag is a new row, and a fault on it is said afresh
        self.r["views"] = {"tags": [_remote_tag("g100", "web")]}
        self._rule({"rename": "site"})
        n, err = self._pass(self._STORE_FAULT)
        self.assertEqual(n, 0)
        lines = [ln for ln in err.splitlines() if "pending-tag-edits" in ln]
        self.assertEqual(len(lines), 1, err)
        self.assertIn('rename of "web"', lines[0])
        self.assertEqual(len(km._pending_tag_rows()), 1)

    def test_a_supersede_mid_episode_is_a_new_row_and_is_said_afresh(self):
        """_queue_pending_tag_edit coalesces: a delete ruled while a rename is still queued REPLACES the
        rename's row (op and ruledAt change), so the prune drops the old key and the new row's fault is a
        new episode -- said afresh. (A same-op re-rule within one wall-clock second shares its key: a
        degenerate case that only folds two lines into one.)"""
        self._rule({"rename": "site"})
        self._host_answers([_remote_tag("g100", "web")])
        n, err = self._pass(self._STORE_FAULT)
        lines = [ln for ln in err.splitlines() if "pending-tag-edits" in ln]
        self.assertEqual((n, len(lines)), (0, 1), err)
        self.assertIn('rename of "web"', lines[0])
        self._rule({"delete": True})                         # the supersede: one row, the delete's
        rows = km._pending_tag_rows()
        self.assertEqual([km._row_op(x) for x in rows], ["delete"])
        n, err = self._pass(self._STORE_FAULT)
        lines = [ln for ln in err.splitlines() if "pending-tag-edits" in ln]
        self.assertEqual((n, len(lines)), (0, 1), "a second line: the delete is a new row, hence a new episode")
        self.assertIn('delete of "web"', lines[0])
        self.assertEqual(set(km._pending_tag_faults), {km._pending_tag_row_key(rows[0])},
                         "after the prune the map holds only the live row's key")
        self.assertEqual(len(self.forwarded), 2)

    def test_a_changed_reading_marks_the_views_dirty_with_nothing_retired(self):
        """The apply's fresh read stored the reading BARE, and _mark_views_dirty fired only when a row
        retired. With rows pending and every forward failing, the apply's re-read is the pass's only real
        read (it stamps the poll gate, so the supervisor's own poll serves the cache), so a change seen
        there reached the feed and timeline a cache bucket late and woke no one. The reading goes through
        _cache_remote_views now: a change marks and wakes, an equal re-read does neither."""
        self._rule({"delete": True})
        dirty = km._views_dirty[0]
        km._views_dirty[0] = 0.0
        km._pusher_wake.clear()
        try:
            km._remote_forward = lambda r, path, body: None          # the transport fails, pass after pass
            changed = [_remote_tag("g100", "web"), _remote_tag("g200", "api")]   # another tag appeared there
            self._host_answers(changed)
            self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
            self.assertEqual(len(km._pending_tag_rows()), 1, "nothing retired: the only path that used to mark")
            self.assertEqual(self.r["views"], {"tags": changed}, "the fresh reading is cached")
            self.assertGreater(km._views_dirty[0], 0.0, "…and marked dirty: the cached feed and timeline builds rebuild past it")
            self.assertTrue(km._pusher_wake.is_set(), "…and the pusher is woken")
            km._views_dirty[0] = 0.0
            km._pusher_wake.clear()
            self._host_answers(list(changed))                         # the next pass: equal content, a new object
            self.assertEqual(km._apply_pending_tag_edits(self.r), 0)
            self.assertEqual(km._views_dirty[0], 0.0, "an unchanged re-read is not news")
            self.assertFalse(km._pusher_wake.is_set())
        finally:
            km._views_dirty[0] = dirty
            km._pusher_wake.clear()


class MtimeStamp(unittest.TestCase):
    """The v2 per-tag mtime: stamped at the store's ONE write door, only when the tag changed."""

    def test_an_edit_stamps_and_an_untouched_tag_keeps_its_stamp(self):
        km._edit_tag("alpha", add=[])
        v = km._timeline_views()
        t0 = next(t for t in v["tags"] if t["name"] == "alpha")
        self.assertTrue(t0.get("mtime"), "creation is an edit")
        old = t0["mtime"] - 1000
        # pin an OLD stamp by writing the FILE directly — the write door itself refuses to move a
        # stamp on an unchanged tag (a client blob cannot forge mtimes), which is the point
        vv = json.loads(json.dumps(v))
        next(t for t in vv["tags"] if t["name"] == "alpha")["mtime"] = old
        km._atomic_write(km._views_path(), json.dumps(vv, sort_keys=True))
        km._edit_tag("beta", add=[])
        v2 = km._timeline_views()
        self.assertEqual(next(t for t in v2["tags"] if t["name"] == "alpha").get("mtime"), old)
        km._edit_tag("alpha", color="#9cd2ff")
        v3 = km._timeline_views()
        self.assertGreater(next(t for t in v3["tags"] if t["name"] == "alpha")["mtime"], old,
                           "an actual change moves the stamp")


class Visibility(unittest.TestCase):
    """The queued intent is loud: pending stamps on the rendered blob, never gone-but-not-gone."""

    def setUp(self):
        _fresh_journal()
        _attach(views={"tags": [_remote_tag("g100", "web")]})

    def tearDown(self):
        _fresh_journal()
        km._remotes.clear()

    def test_views_client_stamps_the_pending_intent(self):
        km._queue_pending_tag_edit(HOST, {"name": "web", "delete": True})
        v = km._views_client()
        rt = next(t for t in v["remoteTags"] if t["host"] == HOST and t["name"] == "web")
        self.assertEqual(rt.get("pending"), "delete")
        self.assertEqual(v["pendingTagEdits"], [{"host": HOST, "name": "web", "op": "delete"}])

    def test_the_dialog_renders_the_compact_idiom(self):
        src = open(os.path.join(os.path.dirname(HERE), "ui", "romp-timeline-view.js")).read()
        self.assertIn("'pending ' + rt.pending + ' on ' + (rt.host || '?')", src,
                      "the tag dialog says pending-<op>-on-<host> beside the pill (compact idiom)")

    def test_the_loud_refusal_says_queued(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn("queued: it applies when %s reattaches", src,
                      "the immediate refusal stays AND says the intent persists (both WS and /tag)")
        self.assertEqual(src.count("queued: it applies when %s reattaches"), 2,
                         "both failure doors carry the wording (WS editTag + POST /tag --host)")


class HostSeqRidesTheRows(unittest.TestCase):
    """Every remoteTags row carries its host's OWN views store's write seq, read off the cached /views
    reading (a kernel stamps `seq` on its blob since 2026-09-05). A remote rename rides this kernel's
    blob with no change to the local `seq`, so a client ordering what a blob says about a remote tag —
    the tab strip's rename follow, which stands down on a blob older than its memory's evidence — needs
    the host's. A host that stamps none puts none on the row, and the local blob's own `seq` stays the
    local store's."""

    def tearDown(self):
        km._remotes.clear()

    def _rows(self):
        return [t for t in km._views_client().get("remoteTags") or [] if t["host"] == HOST]

    def test_a_stamped_reading_stamps_every_row_of_the_host(self):
        _attach(views={"seq": 1757000000123, "tags": [_remote_tag("g100", "web"), _remote_tag("g200", "api")]})
        rows = self._rows()
        self.assertEqual([t["name"] for t in rows], ["web", "api"])
        self.assertEqual([t["seq"] for t in rows], [1757000000123, 1757000000123], "the host's seq, not a per-tag one")
        local_seq = km._views_client().get("seq")
        self.assertNotEqual(local_seq, 1757000000123, "the local blob's seq is the local store's, untouched")

    def test_an_unstamped_or_junk_seq_puts_none_on_the_row(self):
        for bad in (None, 0, -4, True, "1757000000123", 1.5, {"n": 1}):
            with self.subTest(seq=bad):
                views = {"tags": [_remote_tag("g100", "web")]}
                if bad is not None:
                    views["seq"] = bad
                _attach(views=views)
                rows = self._rows()
                self.assertEqual(len(rows), 1)
                self.assertNotIn("seq", rows[0], "an older host stamps none; junk is not a seq")


class SupervisorViewsCache(unittest.TestCase):
    """_cache_remote_views: the supervisor's store of a host's /views reading marks the views dirty and
    wakes the pusher when the reading CHANGED — a pane receives a remote host's tags only on the views
    blob the pusher ships, and a silent store left a reattached host's tags trailing its tabs by a
    pusher cycle. An unchanged reading is not news."""

    def setUp(self):
        self.r = _attach(views={"tags": [_remote_tag("g100", "web")]})
        self._dirty = km._views_dirty[0]
        km._views_dirty[0] = 0.0
        km._pusher_wake.clear()

    def tearDown(self):
        km._views_dirty[0] = self._dirty
        km._pusher_wake.clear()
        km._remotes.clear()

    def test_a_changed_reading_is_stored_and_marks_the_views_dirty(self):
        new = {"tags": [_remote_tag("g100", "api")]}
        self.assertTrue(km._cache_remote_views(self.r, new))
        self.assertEqual(self.r["views"], new)
        self.assertGreater(km._views_dirty[0], 0.0, "the dirty mark moved: the cached feed and timeline rebuild past it")
        self.assertTrue(km._pusher_wake.is_set(),
                        "...and the pusher is woken, so the tabOrder frame carrying the tags ships on its next cycle")

    def test_the_first_reading_of_a_fresh_row_counts_as_a_change(self):
        r = _attach()                                        # no cached views yet: the reattach's first pass
        self.assertTrue(km._cache_remote_views(r, {"tags": []}))
        self.assertEqual(r["views"], {"tags": []})
        self.assertTrue(km._pusher_wake.is_set())

    def test_an_unchanged_reading_stores_nothing_and_wakes_no_one(self):
        same_object = self.r["views"]                        # what the poll's rate gate hands back
        self.assertFalse(km._cache_remote_views(self.r, same_object))
        self.assertFalse(km._cache_remote_views(self.r, {"tags": [_remote_tag("g100", "web")]}),
                         "a re-read that parses equal is not news either")
        self.assertFalse(km._cache_remote_views(self.r, None),
                         "no reading this pass (the host down, or its first read failed): the cache stands")
        self.assertEqual(self.r["views"], {"tags": [_remote_tag("g100", "web")]})
        self.assertEqual(km._views_dirty[0], 0.0)
        self.assertFalse(km._pusher_wake.is_set())

    def test_every_store_of_a_reading_goes_through_the_cache(self):
        """A first cut routed the supervisor's own store through the helper and this test read the
        supervisor's source alone, while _apply_pending_tag_edits (called from the same pass) and
        _forward_tag_edit still stored bare. The WHOLE module is scanned: the one `["views"] =` is the
        helper's own, and every reader of a host's /views calls it."""
        import inspect
        import re
        store = re.compile(r"""\[["']views["']\]\s*=(?!=)""")
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        helper = inspect.getsource(km._cache_remote_views)
        self.assertEqual(len(store.findall(helper)), 1, "the helper stores once")
        self.assertEqual(len(store.findall(src)), 1,
                         "a bare r[\"views\"] = … outside _cache_remote_views: route it through the helper")
        self.assertNotRegex(src, r"""(setdefault|update)\(\s*\{?\s*["']views["']""", "no store by another spelling")
        for fn in (km._tunnel_supervisor, km._apply_pending_tag_edits, km._forward_tag_edit):
            self.assertIn("_cache_remote_views(r, ", inspect.getsource(fn), fn.__name__)


if __name__ == "__main__":
    unittest.main()
