#!/usr/bin/env python3
"""The notification bells (the user 2026-07-28; master default 2026-08-09): the master bell
(notify-cards.json "*" — the bottom bar's bell), a session-level bell (timeline lane / tab menu →
session-flags "notify") and a per-card bell (feed card right-click → notify-cards.json) arm OS-level
notifications, resolved most-specific-wins (card > session > master) — so the master on means every
task notifies and the per-item bells read as mutes. Fired when an armed card ENTERS needs_input
(blocked on you) or completed. Detection diffs each fresh feed build against the previous one — the
exact event the columns move on — against a snapshot that persists across kernel lives
(tests/test_notify_prev_persist.py): an install's very first build is a silent baseline (existing
state is status, not news), and a restart re-announces nothing. Synthetic ids/names only."""
import json
import os
import sys
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_nb", os.path.join(BIN, "romp-kernel"))
jd = km.jd


def _card(iid, sid, col, text="Fix the login flow", **kw):
    d = {"itemId": iid, "sid": sid, "name": "web", "column": col, "text": text}
    d.update(kw)
    return d


def _feed(*cards):
    return {"type": "feed", "asks": [dict(c) for c in cards]}


class NotifyCardStore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_default_is_empty(self):
        self.assertEqual(km._notify_cards(), {})
        self.assertFalse(km._notify_all_on())

    def test_set_get_then_unset_drops_the_entry(self):
        km._set_notify_card("TESTSID:g1", True)
        self.assertEqual(km._notify_cards(), {"TESTSID:g1": True})
        km._set_notify_card("TESTSID:g1", False)
        self.assertEqual(km._notify_cards(), {}, "off matches the (off) default → the override is deleted")

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._notify_cards(), {})            # primes the (empty) read path
        km._set_notify_card("TESTSID:g1", True)
        self.assertTrue(km._notify_cards().get("TESTSID:g1"), "the (mtime_ns,size) cache key sees the write")

    def test_master_set_and_unset(self):
        km._set_notify_all(True)
        self.assertTrue(km._notify_all_on())
        self.assertEqual(km._notify_cards(), {"*": True})
        km._set_notify_all(False)
        self.assertEqual(km._notify_cards(), {})

    def test_a_click_matching_the_default_deletes_the_override(self):
        # master on: arming a card merely restates the default → no entry (a pinned True would
        # keep it armed against a later master-off, which is not what the click said) …
        km._set_notify_all(True)
        km._set_notify_card("TESTSID:g1", True, "TESTSID")
        self.assertEqual(km._notify_cards(), {"*": True})
        # … while muting it deviates → an explicit False
        km._set_notify_card("TESTSID:g1", False, "TESTSID")
        self.assertEqual(km._notify_cards(), {"*": True, "TESTSID:g1": False})

    def test_the_cards_default_is_its_sessions_override_first(self):
        # session muted under a master-on: the card's default is OFF, so arming it is a deviation
        km._set_notify_all(True)
        km._set_notify_session("TESTSID", False)
        km._set_notify_card("TESTSID:g1", True, "TESTSID")
        self.assertEqual(km._notify_cards().get("TESTSID:g1"), True)
        km._set_notify_card("TESTSID:g1", False, "TESTSID")     # back to the session's own default
        self.assertNotIn("TESTSID:g1", km._notify_cards())

    def test_session_override_delete_if_default(self):
        km._set_notify_session("TESTSID", True)                  # master off → a deviation, stored
        self.assertEqual(km._session_flag_raw("TESTSID", "notify"), True)
        km._set_notify_session("TESTSID", False)                 # matches master-off → removed
        self.assertIsNone(km._session_flag_raw("TESTSID", "notify"))
        km._set_notify_all(True)
        km._set_notify_session("TESTSID", False)                 # a mute under master-on is a real value
        self.assertEqual(km._session_flag_raw("TESTSID", "notify"), False)

    def test_prune_drops_only_ids_that_left_the_feed(self):
        km._set_notify_card("TESTSID:g1", True)
        km._set_notify_card("TESTSID:g2", True)
        km._prune_notify_cards({"TESTSID:g2"})
        self.assertEqual(km._notify_cards(), {"TESTSID:g2": True})
        # nothing gone → no write (the file's mtime is the feed-cache sig; a no-op must not churn it)
        p = jd.STATE / "notify-cards.json"
        before = p.stat().st_mtime_ns
        km._prune_notify_cards({"TESTSID:g2"})
        self.assertEqual(p.stat().st_mtime_ns, before)

    def test_prune_keeps_the_master_and_the_mutes(self):
        km._set_notify_all(True)
        km._set_notify_card("TESTSID:g1", False, "TESTSID")      # a live card's mute
        km._set_notify_card("TESTSID:g2", False, "TESTSID")      # a mute whose card then leaves
        km._prune_notify_cards({"TESTSID:g1"})
        self.assertEqual(km._notify_cards(), {"*": True, "TESTSID:g1": False},
                         "the master is not a card and never prunes; kept values stay as stored")


class FeedNotifications(unittest.TestCase):
    """The diff detector: [(title, body, sid, itemId)] per armed card newly in needs_input/completed."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._NOTIFY_PREV[0] = None

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_the_first_build_is_a_silent_baseline(self):
        km._set_session_flag("TESTSID", "notify", True)
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "an install's first build, no snapshot yet: status, not news (freshNeedsYou policy)")

    def test_a_session_armed_card_entering_needs_input_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1)
        # ONE title rule (the user 2026-09-09, whose phone found the notifications too busy): a card
        # entering needs_input wears "Romp needs you: <session>"; everything else "Romp: <session>".
        # Decided from the card's COLUMN, the authoritative state — never from the body text. Was
        # "romp: web" for every kind, which read as a subtitle and left the kind to the body.
        self.assertEqual(out[0][0], "Romp needs you: web")
        self.assertTrue(out[0][1].startswith("Needs you: "), out[0][1])
        self.assertIn("Fix the login flow", out[0][1])
        self.assertNotIn(":", out[0][0].split(": ", 1)[1], "the session name alone — no host, no sid")
        # the sid rides every notification so a push tap can land ON the session that fired
        # (the user 2026-08-08 — their first real push opened the app on a different session)
        self.assertEqual(out[0][2], "TESTSID")

    def test_an_unarmed_transition_is_silent(self):
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "no bell armed → no notification, however the card moves")

    def test_a_card_armed_card_completing_notifies(self):
        km._set_notify_card("TESTSID:g1", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "completed")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "Romp: web", "a completion is not a needs-you: the plain title")
        self.assertTrue(out[0][1].startswith("Completed: "), out[0][1])

    def test_the_body_is_the_events_own_and_nothing_more(self):
        # the user 2026-09-09: the body stays what the event already says — no extra lines, no
        # host/sid prefixes, no decorations; the title alone carries the kind
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out[0][1], "Needs you: Fix the login flow")
        self.assertEqual(out[0][1].count("\n"), 0)
        self.assertNotIn("TESTSID", out[0][0] + out[0][1])

    def test_holding_a_column_does_not_refire(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "still blocked is not news — only the ENTRY event notifies")

    def test_reblocking_without_a_gesture_on_the_card_is_one_announcement(self):
        # 2026-09-10: a card that leaves needs_input and comes back is not news unless something of
        # the user's happened on the card in between (a journaled gesture — tests/test_notify_prev_persist.py
        # has the exception) or the other column was announced since; the judges re-filing a block at
        # every turn end of a busy session pushed one card twelve times
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        first = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))     # the judges lifted it
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(first), 1)
        self.assertEqual(out, [], "the same card in the same column, told already: silent")

    def test_a_card_appearing_already_blocked_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed())                     # baseline consumed on an empty feed
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1, "work can SURFACE blocked — appearing there is entering there")

    def test_a_provisional_placeholder_never_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed())
        out = km._feed_notifications(
            _feed(_card("TESTSID:g1", "TESTSID", "needs_input", provisional=True)))
        self.assertEqual(out, [], "placeholder churn is not a stable card")

    def test_an_armed_card_leaving_the_feed_is_pruned(self):
        km._set_notify_card("TESTSID:g1", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        km._feed_notifications(_feed())                     # cleared/archived → id never comes back
        self.assertEqual(km._notify_cards(), {}, "the store tracks the live feed, not history")

    def test_the_master_arms_everything_by_default(self):
        # the user 2026-08-09: the bottom-right bell alone must mean "notify me about all the tasks"
        km._set_notify_all(True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1, "no per-item bell touched — the master alone arms the card")

    def test_a_card_mute_silences_it_under_the_master(self):
        km._set_notify_all(True)
        km._set_notify_card("TESTSID:g1", False, "TESTSID")
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working"),
                                     _card("TESTSID:g2", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input"),
                                           _card("TESTSID:g2", "TESTSID", "completed")))
        self.assertEqual(len(out), 1, "the muted card is silent; its unmuted sibling still speaks")
        self.assertTrue(out[0][1].startswith("Completed: "), out[0][1])

    def test_a_session_mute_silences_its_cards_under_the_master(self):
        km._set_notify_all(True)
        km._set_notify_session("TESTSID", False)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working"),
                                     _card("OTHERSID:g1", "OTHERSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input"),
                                           _card("OTHERSID:g1", "OTHERSID", "needs_input")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][2], "OTHERSID", "only the unmuted session's card notifies")
        self.assertEqual(out[0][3], "OTHERSID:g1", "the card's own id rides along for the tap to land on")

    def test_a_card_arm_overrides_its_sessions_mute(self):
        # most-specific-wins: card > session > master
        km._set_notify_all(True)
        km._set_notify_session("TESTSID", False)
        km._set_notify_card("TESTSID:g1", True, "TESTSID")
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1)


class NotifyTitle(unittest.TestCase):
    """The ONE title builder (the user 2026-09-09, whose phone found the notifications too busy —
    "romp: web" over a body that had to carry the kind read like a subtitle). Two shapes and no
    third: a needs-you event is "Romp needs you: <session>", everything else "Romp: <session>".
    The body is untouched; the session name stands alone (never host:sid, never a "romp: host:"
    decoration — the tap carries the routing in `data`, the host is not the user's concern)."""

    def test_needs_you_and_everything_else(self):
        self.assertEqual(km._notify_title("web", needs_you=True), "Romp needs you: web")
        self.assertEqual(km._notify_title("web", needs_you=False), "Romp: web")
        self.assertEqual(km._notify_title("web"), "Romp: web", "needs-you is the exception, not the default")

    def test_a_missing_name_still_reads_as_romp(self):
        self.assertEqual(km._notify_title(""), "Romp")
        self.assertEqual(km._notify_title(None), "Romp")
        self.assertEqual(km._notify_title("", needs_you=True), "Romp needs you")

    def test_the_name_is_worn_as_given(self):
        # the builder never decorates: what the caller hands it is what the lock screen shows
        self.assertEqual(km._notify_title("  api "), "Romp: api")
        self.assertEqual(km._notify_title(42), "Romp: 42")

    def test_every_producer_composes_its_title_through_the_builder(self):
        # the rule holds by construction: the card leg, the turn leg and the test push all call
        # _notify_title; no producer composes a "romp: …" title of its own, and the relay no longer
        # rewrites the title it is handed (it used to graft "romp: <origin>:" onto it)
        import inspect
        # the card leg's body lives in _feed_notifications_diff (the wrapper only takes the snapshot's lock)
        for fn in (km._feed_notifications_diff, km._turn_notify_tick, km._push_test):
            self.assertIn("_notify_title(", inspect.getsource(fn), fn.__name__)
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertNotIn('"romp: %s"', src, "no producer composes a title of its own")
        self.assertNotIn('t.startswith("romp: ")', src, "the relay passes the title through as composed")
        self.assertNotIn('"romp: %s:%s" % (origin', src)
        self.assertNotIn('_push_payload("romp"', src, "the test push wears the same builder")


class SystemNotify(unittest.TestCase):
    def test_darwin_shells_out_to_osascript_with_escaped_strings(self):
        calls = []
        saved_popen, saved_platform = km.subprocess.Popen, sys.platform
        km.subprocess.Popen = lambda cmd, **kw: calls.append(cmd)
        sys.platform = "darwin"
        try:
            km._system_notify('Romp needs you: web', 'Needs you: fix the "login" flow')
        finally:
            km.subprocess.Popen = saved_popen
            sys.platform = saved_platform
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[0][1], "-e")
        self.assertIn('with title "Romp needs you: web"', calls[0][2])
        self.assertIn('\\"login\\"', calls[0][2], "quotes are escaped into the AppleScript string")

    def test_a_missing_binary_never_raises(self):
        saved = km.subprocess.Popen

        def boom(cmd, **kw):
            raise OSError("no such binary")
        km.subprocess.Popen = boom
        try:
            km._system_notify("t", "b")                    # must not raise — best-effort by contract
        finally:
            km.subprocess.Popen = saved


class NotifyWiring(unittest.TestCase):
    """Source pins: the flag reaches every payload + the detector rides fresh feed builds."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()

    def test_the_session_flag_rides_both_session_payloads(self):
        # the timeline lane row AND the chat session payload both echo the EFFECTIVE bell state
        # (override, else master) — with the master on, an untouched session's bell paints on
        self.assertEqual(self.src.count('"notify": _notify_session_effective(sid)'), 2)

    def test_every_ask_carries_its_card_arming(self):
        self.assertIn('_a["notify"] = True if _notify_card_effective(_ncards, _a["itemId"], '
                      'str(_a.get("sid") or "")) else None', self.src)

    def test_the_ws_handler_persists_the_card_toggle(self):
        self.assertIn('msg.get("type") == "cardNotify"', self.src)
        # sid rides so delete-if-default resolves against the card's own default
        # the flag is a checked boolean, never bool()-coerced (a string "false" used to arm the bell)
        self.assertIn('value, ferr = _as_bool(msg.get("value"), "value")', self.src)
        # ...and refused on the frame the FEED page renders (settingRefused, addressed to the card), never a warn
        self.assertIn('_refuse_setting(client, ferr, "that bell", "bell", sid=msg.get("sid") or "", item_id=msg["itemId"],',
                      self.src)
        self.assertIn('_set_notify_card(str(msg["itemId"]), value, str(msg.get("sid") or ""))', self.src)

    def test_the_session_bell_routes_to_its_tristate_setter(self):
        # setSessionFlag's pop-on-false is right for the view flags but would eat a mute
        self.assertIn('_set_notify_session(str(msg["id"]), value)', self.src)

    def test_the_master_has_both_routes_and_broadcasts(self):
        # GET paints the bell at boot; POST flips it, rebuilds the feed (per-card bells repaint
        # their new effective state) and tells every open shell at once
        self.assertIn('if p == "/notify-all":', self.src)
        self.assertIn('if u.path == "/notify-all":', self.src)
        self.assertIn('_send_to_app("shell", {"type": "notifyAll", "on": _on})', self.src)

    def test_the_store_mtime_busts_the_feed_cache(self):
        self.assertIn('(jd.STATE / "notify-cards.json", "__ncards__")', self.src,
                      "arming a card must reach the next build, not wait out the sig")

    def test_fresh_feed_builds_drive_the_notifier(self):
        # the detector runs where the fresh build lands — the one choke point every push shares
        # (the sid joined the tuple 2026-08-08 so the push sink can aim its tap-to-open; the list
        # got a name the same day so the federated forward rides the SAME events, never a re-diff)
        self.assertIn("_fired = _feed_notifications(feed)", self.src)
        self.assertIn("for _t, _b, _sid, _iid in _fired:", self.src)   # itemId: the tap scrolls the feed to the card
        self.assertIn("_system_notify(_t, _b)", self.src)
        # trusted peers hear the same transition — since 2026-09-05 the events that BUZZED here
        # (`_buzzed`: the fired list minus those that yielded to a turn-finished push for the same
        # turn end, tests/test_kernel_notify_popover.py), never a list built from a second diff
        self.assertIn("_push_forward(_buzzed)", self.src)


class NotifyStoreUnreadableRefuses(unittest.TestCase):
    """The state-readers audit (rank 9): _notify_cards used to fold ANY read fault to {} and CACHE
    it, so _set_notify_all / _set_notify_turns / _set_notify_card copied that empty, applied one
    edit, and atomically wrote it back — erasing every per-card, session and master bell override
    under a silent success. The setters now read PROVED: a read fault refuses the write loudly and
    the file is left exactly as it was. Synthetic ids only."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        self.td.cleanup()

    def _path(self):
        return jd.STATE / "notify-cards.json"

    def _fault_reads_of(self, target):
        """Fail BOTH read_bytes (the fix's proved reader) and read_text (origin/main's reader) for one
        path — green on the fix, RED on main where the fold-to-{} erases the overrides."""
        import errno
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
        return (real_rb, real_rt)

    def test_a_master_bell_toggle_is_refused_when_the_store_cannot_be_read(self):
        # a populated store: one per-card override the user set deliberately
        km._set_notify_card(self.SID + ":g1", True)
        km._notify_cards_cache.clear()
        before = self._path().read_bytes()
        saved = self._fault_reads_of(self._path())
        raised = None
        try:
            km._set_notify_all(True)               # flip the master — on main this erases g1's override
        except Exception as e:                     # noqa: BLE001 — on main it never raises
            raised = e
        finally:
            Path.read_bytes, Path.read_text = saved
        km._notify_cards_cache.clear()
        # THE erasure the audit is about: on origin/main the read folds to {}, the setter writes {"*":true}
        # and the per-card override is GONE — this assertion turns RED there. The fix refuses the write.
        self.assertEqual(self._path().read_bytes(), before,
                         "the notify file must be unchanged when its file can't be read (main erases it here)")
        self.assertEqual(km._notify_cards().get(self.SID + ":g1"), True,
                         "the pre-existing bell override survives the refused toggle")
        self.assertEqual(type(raised).__name__, "_StateUnreadable",
                         "the write is refused LOUDLY, not folded to a fabricated empty (got %r)" % raised)

    def test_a_torn_notify_file_is_quarantined_aside_not_overwritten(self):
        torn = b'{"*": true, "sid:g1":'
        self._path().write_bytes(torn)
        km._notify_cards_cache.clear()
        self.assertEqual(km._notify_cards_proved(), {}, "the store starts empty only after the bytes are saved")
        q = list(jd.STATE.glob("notify-cards.json.corrupt-*"))
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0].read_bytes(), torn, "the quarantine holds the ORIGINAL bytes")
        self.assertFalse(self._path().exists())

    def test_enoent_notify_still_reads_empty_with_no_quarantine(self):
        self.assertEqual(km._notify_cards(), {}, "a missing store is legitimately empty")
        self.assertEqual(km._notify_cards_proved(), {})
        self.assertEqual(list(jd.STATE.glob("notify-cards.json.corrupt-*")), [])


import contextlib
import errno
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock


@contextlib.contextmanager
def _stat_fault(target):
    """Fail every stat of ONE path with an EACCES for the duration of the block -- the arm the read-fault
    injector never reaches (review find, 2026-09-08): every reader stats BEFORE it reads (the display
    readers key their cache on it), and a state dir that cannot be searched faults exactly there.
    Everything else stats normally."""
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
def _reads_fault(target):
    """Fail every byte read of ONE path with an EIO for the duration of the block."""
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


class NotifyDisplayReaderServesUnproved(unittest.TestCase):
    """The DISPLAY reader of the bells under a fault: the last value this kernel read if it holds
    one, else {} -- neither cached under the file's stat key, so the recovered disk is read again."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._state_fault_seen.clear()
        self.notices = []
        self._notice = km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok, kind))

    def tearDown(self):
        km._sync_notice = self._notice
        jd.STATE = self.saved
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._state_fault_seen.clear()
        self.td.cleanup()

    def _path(self):
        return jd.STATE / "notify-cards.json"

    def test_a_stat_fault_serves_the_last_read_value_and_refuses_the_writers(self):
        km._set_notify_card(self.SID + ":g1", True)
        km._notify_cards_cache.clear()
        self.assertEqual(km._notify_cards(), {self.SID + ":g1": True})   # primes the cache
        key1 = km._notify_cards_cache[str(self._path())][0]
        before = self._path().read_bytes()
        with _stat_fault(self._path()):
            self.assertEqual(km._notify_cards(), {self.SID + ":g1": True}, "the stat arm serves the LAST value read")
            self.assertEqual(km._notify_cards_cache[str(self._path())], (key1, {self.SID + ":g1": True}), "and caches nothing new")
            with self.assertRaises(km._StateUnreadable) as cm:
                km._notify_cards_proved()
            self.assertIn("stat failed: [Errno 13]", str(cm.exception))
            with self.assertRaises(km._StateUnreadable):
                km._set_notify_all(True)                                 # a writer refuses on the stat fault too
        self.assertEqual(self._path().read_bytes(), before)
        bad = [t for t, ok, _k in self.notices if not ok]
        self.assertEqual(len(bad), 1, "one notice for the episode, from the display reader's stat arm")
        self.assertIn("notify-cards.json could not be read (stat failed: [Errno 13]", bad[0])
        km._notify_cards_cache.clear()
        with _stat_fault(self._path()):
            self.assertEqual(km._notify_cards(), {}, "a cold cache serves the empty default under a stat fault")
            self.assertNotIn(str(self._path()), km._notify_cards_cache, "…uncached")

    def test_the_prune_on_a_card_leaving_skips_the_pass_on_a_fault_loud_once(self):
        # the body's claim, unpinned until now (review find, 2026-09-08: deleting the prune's try/except
        # passed the suite): the prune that runs when a card leaves the feed skips its pass on a fault
        # instead of pruning against a fabricated {} and writing the truncation over every override
        km._set_notify_card(self.SID + ":g1", True)
        km._set_notify_card(self.SID + ":g2", True)
        km._notify_cards_cache.clear()
        before = self._path().read_bytes()
        with _reads_fault(self._path()):
            km._prune_notify_cards({self.SID + ":g1"})                  # g2 left the feed: a clean pass drops it
            km._prune_notify_cards({self.SID + ":g1"})
        self.assertEqual(self._path().read_bytes(), before, "a fault must not prune against a fabricated {} and write it")
        bad = [t for t, ok, _k in self.notices if not ok]
        self.assertEqual(len(bad), 1, "loud once per episode, not once per pass")
        self.assertIn("notify-cards.json could not be read", bad[0])
        km._prune_notify_cards({self.SID + ":g1"})                      # the disk recovers: the pass prunes as before
        self.assertEqual(km._notify_cards_proved(), {self.SID + ":g1": True})

    def test_a_quarantine_tells_the_dashboard_once_under_the_refused_kind(self):
        # review find, 2026-09-08: a quarantine reset every bell override with only a stderr line
        torn = b'{"*": true, "' + self.SID.encode() + b':g1":'
        self._path().write_bytes(torn)
        self.assertEqual(km._notify_cards_proved(), {})
        self.assertEqual(len(self.notices), 1)
        text, ok, kind = self.notices[0]
        self.assertEqual((ok, kind), (False, "refused"))
        self.assertIn("notify-cards.json could not be parsed and was moved aside to notify-cards.json.corrupt-", text)
        self.assertIn("start over empty", text)
        self.assertEqual(km._notify_cards_proved(), {})                  # the next read is an ENOENT …
        self.assertEqual(len(self.notices), 1, "… and files nothing more")

    def test_valid_json_of_the_wrong_shape_is_quarantined_not_read_as_empty(self):
        # review find, 2026-09-08: a LIST where the bells dict belongs read as a proved empty store and
        # was overwritten by the next bell click -- a file none of our writers produce, gone
        wrong = b'["' + self.SID.encode() + b':g1"]'
        self._path().write_bytes(wrong)
        self.assertEqual(km._notify_cards_proved(), {}, "empty only AFTER the bytes are moved aside")
        aside = list(jd.STATE.glob("notify-cards.json.corrupt-*"))
        self.assertEqual(len(aside), 1, "quarantined like torn bytes")
        self.assertEqual(aside[0].read_bytes(), wrong)
        self.assertFalse(self._path().exists())
        self.assertEqual([k for _t, _ok, k in self.notices], ["refused"])

    def test_a_fault_serves_the_last_read_value_and_caches_nothing_new(self):
        km._set_notify_card(self.SID + ":g1", True)
        km._notify_cards_cache.clear()
        self.assertEqual(km._notify_cards(), {self.SID + ":g1": True})   # primes the cache
        key1 = km._notify_cards_cache[str(self._path())][0]
        km._set_notify_all(True)                                        # the file moves on: a new stat key
        st = self._path().stat()
        self.assertNotEqual(key1, (st.st_mtime_ns, st.st_size))
        with _reads_fault(self._path()):
            self.assertEqual(km._notify_cards(), {self.SID + ":g1": True}, "the last value read, not {}")
            self.assertEqual(km._notify_cards_cache[str(self._path())][0], key1, "the fault cached nothing under the new key")
        self.assertEqual(km._notify_cards(), {self.SID + ":g1": True, km.NOTIFY_ALL_KEY: True},
                         "the real, newer file reads once the fault clears")

    def test_a_fault_on_a_cold_cache_serves_the_empty_default_uncached(self):
        km._set_notify_card(self.SID + ":g1", True)
        km._notify_cards_cache.clear()
        with _reads_fault(self._path()):
            self.assertEqual(km._notify_cards(), {})
            self.assertNotIn(str(self._path()), km._notify_cards_cache)
        self.assertEqual(km._notify_cards(), {self.SID + ":g1": True})


class NotifyWsRefusal(unittest.TestCase):
    """The cardNotify WS arm under a store fault: the file is untouched, the poster gets a
    `settingRefused` frame addressed to its card (itemId) on its OWN socket, nothing escapes. The
    feed page has no `warn` handler, so the old frame left the bell painted in the refused state."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty)
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        jd.STATE, km._mark_views_dirty = self.saved
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        self.td.cleanup()

    def test_a_refused_bell_answers_the_poster_and_leaves_the_file_alone(self):
        km._set_notify_card(self.SID + ":g1", True)
        km._notify_cards_cache.clear()
        p = jd.STATE / "notify-cards.json"
        before = p.read_bytes()
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with _reads_fault(p):
            km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g2", "sid": self.SID, "value": True}, client)
        self.assertEqual(p.read_bytes(), before, "the bells file is byte-for-byte unchanged")
        self.assertEqual(len(sent), 1)
        fr = sent[0]
        self.assertEqual((fr["type"], fr["itemId"], fr["sid"], fr["flag"]), ("settingRefused", self.SID + ":g2", self.SID, ""))
        self.assertEqual(fr["gesture"], "bell")
        self.assertIs(fr["value"], False, "the bell state the kernel still paints for this card rides along (here: no override, master off)")
        self.assertIn("couldn't save that bell", fr["text"])
        self.assertIn("notify-cards.json could not be read", fr["text"])
        self.assertEqual(self.dirty, [])

    def test_a_clean_bell_still_lands(self):
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g2", "sid": self.SID, "value": True}, client)
        self.assertEqual(sent, [])
        self.assertEqual(km._notify_cards().get(self.SID + ":g2"), True)
        self.assertEqual(self.dirty, [1])

    def test_a_refusal_carries_the_painted_value_when_the_bell_is_on(self):
        # `value` is what the display path still PAINTS for the card. Every other case here paints off
        # (review find, 2026-09-08: a constant False survived), so this one paints ON: the master is on and
        # the card has no override. The file then moves on (a new stat key) so the faulted read is a real
        # miss served from the primed cache, not a cache hit
        km._set_notify_all(True)
        km._notify_cards_cache.clear()
        km._notify_cards()                                              # prime the display cache
        km._set_notify_card(self.SID + ":g9", False, self.SID)          # a stored mute elsewhere: bumps the stat key
        p = jd.STATE / "notify-cards.json"
        before = p.read_bytes()
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with _reads_fault(p):
            km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g2", "sid": self.SID, "value": False}, client)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["gesture"], "bell")
        self.assertIs(sent[0]["value"], True, "no override, master on: the card paints ON, and the frame says so")
        self.assertEqual(p.read_bytes(), before)


class NotifyRoutesRefuseInTheirOwnShape(unittest.TestCase):
    """POST /notify-all and /notify-turns under a store fault -- a read fault, or a publish that
    fails -- answer 200 with the shape every kernel refusal wears, {ok:false, error} (as /send and
    /new answer theirs), never a 5xx: the shell's post() consumes that shape, whereas a non-2xx
    reached it as raw JSON inside an HTTP error. No `retryable` key: nothing reads one (review find,
    2026-09-08). Live server: the POST needs a real Content-Length read."""

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
        self.saved = (jd.STATE, km._mark_views_dirty, km._send_to_app)
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        self.sent = []
        km._send_to_app = lambda app, m: self.sent.append((app, m))
        km._mark_views_dirty = lambda: None

    def tearDown(self):
        jd.STATE, km._mark_views_dirty, km._send_to_app = self.saved
        km._notify_cards_cache.clear()
        self.td.cleanup()

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _refused(self, path):
        km._set_notify_card("11111111-2222-3333-4444-555555555555:g1", True)   # a populated store
        km._notify_cards_cache.clear()
        p = jd.STATE / "notify-cards.json"
        before = p.read_bytes()
        with _reads_fault(p):
            status, body = self._post(path, {"on": True})
        self.assertEqual(status, 200, "%s: the refusal rides the route's own 200 shape, never a 5xx" % path)
        self.assertIs(body["ok"], False)
        self.assertNotIn("retryable", body, "%s: no key the shell's post() never reads" % path)
        self.assertIn("notify-cards.json could not be read", body["error"])
        self.assertIn("try again", body["error"])
        self.assertEqual(p.read_bytes(), before, "%s: the bells file is untouched" % path)
        self.assertEqual(self.sent, [], "%s: no dashboard is told the switch flipped -- it did not" % path)

    def test_notify_all_refuses_in_the_route_shape(self):
        self._refused("/notify-all")

    def test_notify_turns_refuses_in_the_route_shape(self):
        self._refused("/notify-turns")

    def _refused_publish(self, path):
        # the store READS but its PUBLISH fails: the maintainer's fold on PR #1019 (the write step is a fault
        # boundary too). Before, the OSError out of _atomic_write reached do_POST's catch-all as a 500
        # traceback -- which `romp tag`-style callers and a federation forward read as an unreachable kernel
        km._set_notify_card("11111111-2222-3333-4444-555555555555:g1", True)
        km._notify_cards_cache.clear()
        p = jd.STATE / "notify-cards.json"
        before = p.read_bytes()
        with _writes_fault(p):
            status, body = self._post(path, {"on": True})
        self.assertEqual(status, 200, "%s: a failed publish rides the route's own 200 shape, never a 500" % path)
        self.assertIs(body["ok"], False)
        self.assertNotIn("retryable", body, "%s: no key the shell's post() never reads" % path)
        self.assertIn("notify-cards.json could not be written", body["error"])
        self.assertIn("No space left on device", body["error"])
        self.assertIn("try again", body["error"])
        self.assertEqual(p.read_bytes(), before, "%s: the bells file is untouched" % path)
        self.assertEqual(self.sent, [], "%s: no dashboard is told the switch flipped -- it did not" % path)

    def test_notify_all_refuses_a_failed_publish_in_the_route_shape(self):
        self._refused_publish("/notify-all")

    def test_notify_turns_refuses_a_failed_publish_in_the_route_shape(self):
        self._refused_publish("/notify-turns")

    def test_a_clean_post_still_answers_ok_and_broadcasts(self):
        status, body = self._post("/notify-turns", {"on": True})
        self.assertEqual((status, body), (200, {"ok": True, "on": True}))
        self.assertIn(("shell", {"type": "notifyTurns", "on": True}), self.sent)


_POST_HARNESS = r"""
const src = process.argv[1], answers = JSON.parse(process.argv[2]);
let i = 0;
global.fetch = function (path) {
  const a = answers[i++];
  return Promise.resolve({ ok: a.status < 400, status: a.status,
    json: () => (a.body === undefined ? Promise.reject(new Error('no json')) : Promise.resolve(a.body)),
    text: () => Promise.resolve(a.text || '') });
};
eval(src + '\nglobal.post = post;');                     // the popover's post(), verbatim from the shell
const out = [];
(async () => {
  for (const a of answers) {
    try { out.push({ resolved: await post(a.path, {}) }); }
    catch (e) { out.push({ rejected: String(e && e.message) }); }
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


class ShellSwitchesReadTheRefusal(unittest.TestCase):
    """The bell popover's switches post through one post() helper. With the routes refusing as 200
    ok:false, a helper that resolved on any 2xx would run the SUCCESS arm -- flipping the switch to
    the state the kernel just refused. It must reject on the refusal shape ({ok:false, error}) with
    the route's `error`, so the switch stays where it was and the reason toasts through the same
    fail() the transport errors use. And ONLY on that shape (review find, 2026-09-08): the same
    helper serves the test button, and /push/test answers 200 {ok:false, status, detail} by design
    for a refused or unsubscribed test push -- rejecting every ok:false made the button's own result
    line ('The push service refused it: <status> <detail>') unreachable behind a generic failure."""

    def test_post_rejects_an_ok_false_body_with_its_error_text(self):
        js = km._LANDING_PUSH_JS
        post = js[js.index("function post(path,obj){"):js.index("function b64u(")]
        self.assertIn("if(d&&d.ok===false&&d.error)throw new Error(d.error);", post)
        # the throw sits AFTER the JSON parse and BEFORE the value is handed to the callers
        self.assertLess(post.index("return r.json()"), post.index("d.ok===false"))
        self.assertIn("return d;", post)
        # both switches take the fail arm, which toasts and leaves isOn / turnsOn untouched
        self.assertIn("post('/notify-all',{on:want}).then(function(){isOn=want;paint();},fail)", js)
        self.assertIn("post('/notify-turns',{on:wantT}).then(function(){turnsOn=wantT;paint();},fail)", js)
        # …and the test button reads the push service's verdict off the RESOLVED body, so a refusal
        # there must reach it as a value, not as a rejection
        handler = js[js.index("if(act==='test')"):]
        self.assertIn("(d&&d.status?('The push service refused it: '+d.status+' '+(d.detail||'')+'.')", handler)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_post_executed_passes_a_push_test_verdict_through_and_rejects_only_a_refusal(self):
        js = km._LANDING_PUSH_JS
        post = js[js.index("function post(path,obj){"):js.index("function b64u(")]
        answers = [
            # /push/test's designed answers: a refused push and an unsubscribed device, both 200 ok:false
            # WITHOUT `error` -- they must resolve, so the result arm can say what the service said
            {"path": "/push/test", "status": 200, "body": {"ok": False, "status": 410, "detail": "Gone; the dead subscription was removed"}},
            {"path": "/push/test", "status": 200, "body": {"ok": False, "status": 0, "detail": "this device isn't subscribed yet"}},
            # a switch route's refusal: 200 ok:false WITH the reason -- rejects with exactly that text
            {"path": "/notify-all", "status": 200, "body": {"ok": False, "error": "notify-cards.json could not be read (read failed: [Errno 5] Input/output error) \u2014 the change did not land; try again"}},
            # a success, and a transport error, behave as they always did
            {"path": "/notify-turns", "status": 200, "body": {"ok": True, "on": True}},
            {"path": "/notify-turns", "status": 503, "text": "kernel busy"},
        ]
        out = subprocess.run([shutil.which("node"), "-e", _POST_HARNESS, post, json.dumps(answers)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got[0], {"resolved": answers[0]["body"]}, "a refused test push reaches the result arm")
        self.assertEqual(got[1], {"resolved": answers[1]["body"]}, "an unsubscribed device reaches the result arm")
        self.assertEqual(got[2], {"rejected": answers[2]["body"]["error"]}, "a switch refusal rejects with the route's reason")
        self.assertEqual(got[3], {"resolved": {"ok": True, "on": True}})
        self.assertEqual(got[4], {"rejected": "kernel busy"})


class CardBellJudgedAgainstAProvedDefault(unittest.TestCase):
    """_set_notify_card judges the click against the card's DEFAULT -- the session's own bell, which
    lives in the OTHER store (session-flags.json), else the master. Read through the display reader, a
    fault there folded the session's bell to "unset" and the click was judged against the master
    instead: a mute matching that fabricated default was DELETED under the success path -- the user's
    override erased. The default is read PROVED now: a fault on the flags file refuses the bells write
    exactly like a fault on the bells file."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty)
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        jd.STATE, km._mark_views_dirty = self.saved
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        self.td.cleanup()

    def _seed(self):
        # the master is OFF, this session's bell is ON (its own override), and one of its cards is MUTED
        # (a stored card override False, which differs from its default True)
        km._set_notify_all(False)
        km._set_session_flag(self.SID, "notify", True)
        km._set_notify_card(self.SID + ":g1", False, self.SID)
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        cards_p = jd.STATE / "notify-cards.json"
        self.assertEqual(json.loads(cards_p.read_text()), {self.SID + ":g1": False})
        return cards_p, jd.STATE / "session-flags.json"

    def test_a_card_bell_click_is_refused_when_the_flags_store_cannot_be_read(self):
        cards_p, flags_p = self._seed()
        before = cards_p.read_bytes()
        raised = None
        with _reads_fault(flags_p):                                # the OTHER store faults
            try:
                km._set_notify_card(self.SID + ":g1", False, self.SID)   # the mute re-sent (a second click, a pane retry)
            except Exception as e:                                 # noqa: BLE001 -- before the fix it never raises
                raised = e
        # before the fix: the session's bell folded to unset, the default fell to the master (off), False ==
        # off, and the override was DELETED -- the file rewritten as {}. Now the write is refused.
        self.assertEqual(cards_p.read_bytes(), before, "the bells file must be unchanged when the flags file can't be read")
        self.assertEqual(type(raised).__name__, "_StateUnreadable", "refused loudly (got %r)" % raised)
        self.assertIn("session-flags.json", str(raised), "the refusal names the store that faulted")

    def test_the_ws_arm_refuses_a_card_bell_on_the_other_store_s_fault(self):
        cards_p, flags_p = self._seed()
        before = cards_p.read_bytes()
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with _reads_fault(flags_p):
            km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g1", "sid": self.SID, "value": False}, client)
        self.assertEqual(cards_p.read_bytes(), before)
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["type"], sent[0]["gesture"], sent[0]["itemId"]), ("settingRefused", "bell", self.SID + ":g1"))
        self.assertIn("session-flags.json could not be read", sent[0]["text"])
        self.assertEqual(self.dirty, [])


class NotifyWsWriteFailure(unittest.TestCase):
    """The cardNotify WS arm when the bells store READS but its PUBLISH fails: the maintainer's fold on PR
    #1019. Before, the arm caught only _StateUnreadable and the OSError out of _atomic_write escaped
    _dispatch_ws to the receive loop, which dropped the client. Now the poster gets the same settingRefused
    frame, addressed to the card, saying the file could not be written; the store is untouched; the fault
    is filed once per episode."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty, km._sync_notice)
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear(); km._flags_cache.clear(); km._state_fault_seen.clear()
        vars(km).get("_state_write_fault_seen", {}).clear()
        self.dirty, self.notices = [], []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok))

    def tearDown(self):
        jd.STATE, km._mark_views_dirty, km._sync_notice = self.saved
        km._notify_cards_cache.clear(); km._flags_cache.clear(); km._state_fault_seen.clear()
        vars(km).get("_state_write_fault_seen", {}).clear()
        self.td.cleanup()

    def test_a_failed_publish_answers_the_poster_instead_of_dropping_the_socket(self):
        km._set_notify_card(self.SID + ":g1", True, self.SID)
        km._notify_cards_cache.clear()
        p = jd.STATE / "notify-cards.json"
        before = p.read_bytes()
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with _writes_fault(p):
            for _ in range(2):                                             # two clicks on the same full disk
                try:
                    km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g2", "sid": self.SID, "value": True}, client)
                except OSError:
                    self.fail("the OSError escaped _dispatch_ws -- the receive loop re-raises it and drops the client")
        self.assertTrue(client["alive"])
        self.assertEqual(p.read_bytes(), before, "the bells file is byte-for-byte unchanged")
        self.assertEqual(len(sent), 2, "every click is answered on the delivering socket")
        fr = sent[0]
        self.assertEqual((fr["type"], fr["gesture"], fr["itemId"], fr["sid"]), ("settingRefused", "bell", self.SID + ":g2", self.SID))
        self.assertIn("couldn't save that bell", fr["text"])
        self.assertIn("notify-cards.json could not be written", fr["text"])
        self.assertIs(fr["value"], False, "what the kernel still paints for this card (no override, master off)")
        self.assertEqual(self.dirty, [])
        self.assertEqual(len([t for t, ok in self.notices if not ok]), 1, "filed once per episode, not per click")
        km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": self.SID + ":g2", "sid": self.SID, "value": True}, client)
        self.assertEqual(len(sent), 2, "the disk heals: the click lands and nothing more is said")
        self.assertEqual(self.dirty, [1])
        km._notify_cards_cache.clear()
        self.assertIs(km._notify_cards().get(self.SID + ":g2"), True)


if __name__ == "__main__":
    unittest.main()
