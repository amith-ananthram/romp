#!/usr/bin/env python3
"""The read-only observability GETs (both teams' surveys, 2026-08-24): GET /feed.json — exactly what
build_feed ships to the board — and GET /classify?id=<sid> — one session's live classification as
the kernel derives it, a JOIN over reads that already exist (never a second predicate
implementation). Both serve-token-gated. /classify is read-only, no side effects; /feed.json moves
none of the pusher's transition-event work (below), while build_feed's own housekeeping (a
session-order persist when the living set moved, the views store's re-stamp, a fork's inheritance
heal) runs as it did on the old path. Drives the REAL Handler over HTTP (the test_tag_route idiom).
Synthetic only.

/feed.json answers through _pure_feed, never _cached_feed (2026-09-08): the pusher's door diffs the
bells on its cold branch (_feed_notifications advances _NOTIFY_PREV and prunes notify-cards.json),
pushes the app badge and fills the pusher's cache — so on a headless kernel, where nothing ever
warms _built_feed, a monitoring script's GET did all four. The pure path serves the pusher's copy
while the pusher has an audience (the same _feed_audience question _push asks), else its own copy
on the pusher's own reuse question (the view sig, the REBUILD_MIN_S floor, the _views_dirty mark),
else one build shared by every GET in flight; it never fills _built_feed (a 1 Hz poller would
otherwise keep the pusher on its serve branch and mute every bell), and its reads count under the
route's own counters, not the pusher's."""
import json
import os
import queue
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_obs", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


def _client(app):
    """A fake WS client of `app` whose frames land in the returned list (the test_close_confirm shape)."""
    frames = []
    return {"app": app, "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}, frames


def _state_snapshot(root):
    """Every file under STATE with its (mtime_ns, size) — the read-only-ness witness."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_mtime_ns, st.st_size)
    return out


class ObservabilityRoutes(unittest.TestCase):
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
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()
        self._saved = (km._live_map, km.build_feed, km._NOTIFY_PREV[0], km._BADGE_LAST[0],
                       getattr(km, "_PURE_FEED", None), km._views_dirty[0], list(km._clients),
                       km._fleet_view_sig, getattr(km, "_pure_feed_lock", None))
        km._live_map = lambda: {}
        km._built_feed[:] = [None, None, 0, 0]     # a cold pusher cache: headless, nothing warmed it
        km._PURE_FEED = None                        # …and no earlier GET's build either
        km._views_dirty[0] = 0.0
        del km._clients[:]

    def tearDown(self):
        (km._live_map, km.build_feed, km._NOTIFY_PREV[0], km._BADGE_LAST[0],
         km._PURE_FEED, km._views_dirty[0], clients, km._fleet_view_sig, km._pure_feed_lock) = self._saved
        del km._clients[:]
        km._clients.extend(clients)
        jd.STATE = self._state
        km._flags_cache.clear()
        km._built_feed[:] = [None, None, 0, 0]
        self.td.cleanup()

    def _get(self, path, token=True):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url, headers=(
            {"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]} if token else {}))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode()

    def test_both_routes_are_token_gated(self):
        self.assertEqual(self._get("/feed.json", token=False)[0], 403)
        self.assertEqual(self._get("/classify?id=" + SID, token=False)[0], 403)

    def test_feed_json_is_exactly_the_boards_payload_and_read_only(self):
        (jd.STATE / "goals").mkdir(parents=True, exist_ok=True)
        before = _state_snapshot(self.td.name)
        st, d = self._get("/feed.json")
        self.assertEqual(st, 200)
        self.assertEqual(d.get("type"), "feed", "the exact build_feed shape, not a re-derivation")
        self.assertIn("asks", d)
        self.assertIn("now", d)
        # the top-level keys a reader of this route can lean on: build_feed's, plus the build id
        # every served payload carries (the pure path claims one like the pusher's builds do)
        for key in ("type", "asks", "working", "awaiting", "now", "buildId", "order", "sessions", "selfHost"):
            self.assertIn(key, d, "the answer shape is build_feed's own")
        self.assertEqual(_state_snapshot(self.td.name), before, "a GET writes nothing")

    def test_feed_json_on_a_cold_kernel_moves_nothing(self):
        """The defect (2026-09-08): a headless kernel never warms _built_feed, so every GET took
        _cached_feed's cold branch — which is the PUSHER's transition event: it pruned the bell's
        per-card overrides on disk, advanced the notification baseline past changes nobody had been
        told about, pushed a badge frame to the shells, and filled the pusher's cache. A read does
        none of that."""
        armed = json.dumps({"*": True, SID + ":g1": True}, sort_keys=True)   # a master bell + one armed
        (jd.STATE / "notify-cards.json").write_text(armed)                    # card no feed lists any more
        sentinel = {SID + ":g0": "working"}          # the baseline the next PUSHER build must diff against
        km._NOTIFY_PREV[0] = sentinel
        km._BADGE_LAST[0] = None                     # nothing sent since boot: a push here would be a first
        shell, frames = _client("shell")
        km._clients.append(shell)
        st, d = self._get("/feed.json")
        self.assertEqual(st, 200)
        self.assertEqual(d.get("type"), "feed")
        self.assertEqual((jd.STATE / "notify-cards.json").read_text(), armed,
                         "the armed card survives: pruning is the pusher's event, not a read's")
        self.assertIs(km._NOTIFY_PREV[0], sentinel, "the notification baseline did not advance")
        self.assertEqual(frames, [], "no badge frame reached the shell")
        self.assertIsNone(km._BADGE_LAST[0])
        self.assertIsNone(km._built_feed[1], "the pusher's cache is not filled by a read")

    def test_feed_json_reuses_its_copy_on_the_pushers_own_question(self):
        """A poller hitting the route every second must not pay a build per hit, and an idle board
        must not pay one per REBUILD_MIN_S either: the route's copy is reused on the pusher's own
        question (_cached_feed's, minus the connect arm). Inside the floor it is reused whatever the
        inputs did (the floor caps build cost, as it does for the pusher); a _views_dirty mark (a
        mutation the sig cannot see) rebuilds at once; past the floor an UNCHANGED view sig reuses
        and a changed one rebuilds. The first cut reused on the clock alone (review find,
        2026-09-08): a headless idle board rebuilt every 2 s for as long as anything polled, where
        the pusher's own copy would have been reused."""
        calls = []

        def counting_build(now, live):
            calls.append(now)
            return {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": now}

        km.build_feed = counting_build
        sig = ["a"]
        km._fleet_view_sig = lambda now, live: ("SIG", sig[0])   # the inputs' fingerprint, under the test's hand

        def age_past_the_floor():
            pf = km._PURE_FEED
            km._PURE_FEED = (pf[0], pf[1] - km.REBUILD_MIN_S - 1) + tuple(pf[2:])

        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(len(calls), 1, "two GETs inside REBUILD_MIN_S: one build")
        km._views_dirty[0] = time.time()             # an optimistic kernel-side mutation postdates the build
        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(len(calls), 2, "a dirty mark rebuilds")
        age_past_the_floor()
        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(len(calls), 2, "past the floor with the inputs unchanged: reused, as the pusher would")
        sig[0] = "b"                                 # the inputs moved
        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(len(calls), 3, "past the floor, a changed view sig rebuilds")
        sig[0] = "c"                                 # ...and moved again, inside the floor this time
        self.assertEqual(self._get("/feed.json")[0], 200)
        self.assertEqual(len(calls), 3, "inside the floor the clock caps build cost, whatever the inputs did")
        self.assertIsNone(km._built_feed[1], "none of those builds reached the pusher's slot")

    def test_the_routes_audience_predicate_is_the_pushers(self):
        """The route serves the pusher's copy exactly while the pusher would maintain one, so both
        must ask ONE question of a client list (_feed_audience), or the route serves a frozen board
        (or builds needlessly) the day _push's want_feed changes. The first cut held two copies of
        the app tuple together with a source-text pin (review find, 2026-09-08; the pin could not
        pass on main, where the route's copy did not exist). Now the REAL _push is driven per app id
        and whether it built the feed is compared with the route's answer for the same client, with
        the shared question wrapped to witness that both of them asked it."""
        stubs = ("_cached_feed", "_fleet_view_sig", "_chat_tab_sessions", "_retry_parked_creates",
                 "build_timeline", "_cached_timeline", "_feed_audience")
        saved = {nm: getattr(km, nm) for nm in stubs}
        saved_state = (list(km._last_tab_order), km._feed_wire, km._bars_wire)
        asked, consulted = [], []

        def recording_feed(now, live, sig, connect=False):
            asked.append(sig)
            return {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": now, "buildId": 1}

        def witnessed_audience(clients, _real=km._feed_audience):
            consulted.append([c["app"] for c in clients])
            return _real(clients)

        km._cached_feed = recording_feed
        km._feed_audience = witnessed_audience
        km._fleet_view_sig = lambda now, live: ("SIG",)
        km._chat_tab_sessions = lambda now, live: []
        km._retry_parked_creates = lambda: None
        timeline = lambda now, live, *a, **kw: {"lanes": [], "turns": {}, "judging": [], "messages": [], "now": now}
        km.build_timeline = timeline
        km._cached_timeline = timeline
        pusher_built, route_says = {}, {}
        try:
            for app in ("feed", "fleet", "chat", "timeline", "shell"):
                c, _ = _client(app)
                del asked[:]
                del consulted[:]
                km._push([c], connect=True)               # the pusher's own connect push for this one client
                pusher_built[app] = bool(asked)
                self.assertEqual(consulted, [[app]], "_push asked the shared question of its targets")
                del km._clients[:]
                km._clients.append(c)
                del consulted[:]
                route_says[app] = km._pusher_has_feed_audience()
                self.assertEqual(consulted, [[app]], "the route asked the same question of the connected set")
        finally:
            for nm, v in saved.items():
                setattr(km, nm, v)
            km._last_tab_order[:], km._feed_wire, km._bars_wire = saved_state
        self.assertEqual(route_says, pusher_built, "one question, asked by the pusher and the route alike")
        self.assertEqual({app for app, rides in route_says.items() if rides}, {"feed", "fleet", "chat"},
                         "who rides the feed payload: the Sessions pane (app id fleet), the chat for feed['working']")

    def test_concurrent_feed_json_reads_share_one_build(self):
        """Two GETs in flight together build ONCE (review find, 2026-09-08). The server runs a handler
        thread per GET, and the pusher coalesces by being one thread, not by a lock, so without one
        every GET arriving mid-build started a build_feed of its own (the 2026-08-30 pile-up class:
        inline builds stacking on handler threads), and the older build, finishing last, rebound the
        slot so buildId went backwards inside the window. The second GET queues behind the first's
        build and is served it. Event-driven: the build blocks on an Event until the second GET has
        queued, and queuing is observed through the lock's own __enter__ (no sleeps)."""
        parked = queue.Queue()                       # each thread reports where it stopped: "lock" or "build"
        release = threading.Event()

        class SignallingLock:                        # the route's lock, naming each acquirer BEFORE it blocks
            def __init__(self):
                self.real = threading.Lock()

            def __enter__(self):
                parked.put("lock")
                return self.real.__enter__()

            def __exit__(self, *exc):
                return self.real.__exit__(*exc)

        calls = []

        def blocking_build(now, live):
            calls.append(now)
            parked.put("build")
            release.wait(10)
            return {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": now}

        km.build_feed = blocking_build
        km._pure_feed_lock = SignallingLock()
        out = []
        first = threading.Thread(target=lambda: out.append(self._get("/feed.json")))
        first.start()
        stops = [parked.get(timeout=10)]             # the first GET takes the lock...
        if stops[-1] == "lock":
            stops.append(parked.get(timeout=10))     # ...and enters its build
        self.assertEqual(stops[-1], "build")
        second = threading.Thread(target=lambda: out.append(self._get("/feed.json")))
        second.start()
        where = parked.get(timeout=10)               # the second GET: queued behind the lock, or in a build of its own
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual(where, "lock", "the second GET queued behind the first's build instead of building")
        self.assertEqual(len(calls), 1, "two GETs in flight together: one build, served to both")
        self.assertEqual([st for st, _ in out], [200, 200])
        self.assertEqual(len({d["buildId"] for _, d in out}), 1, "both were served the same build")

    def test_feed_json_reads_count_under_the_routes_counters_not_the_pushers(self):
        """A route build or serve moves the route's OWN numbers (review find, 2026-09-08). feedBuild /
        feedServe on /version and the `feed` build kind on /perf are the PUSHER's cost, read as 'a
        rising build count on a quiet board is a bug signature'; a monitoring poller's builds under
        those numbers would forge that signature, or bury a pusher regression in a poller's noise."""
        km.build_feed = lambda now, live: {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": now}
        views0 = dict(km._VIEW_STATS)
        builds0 = km._PERF_STATS.snapshot()["builds"]
        self.assertEqual(self._get("/feed.json")[0], 200)     # cold: a build
        self.assertEqual(self._get("/feed.json")[0], 200)     # inside the floor: a serve
        views1 = dict(km._VIEW_STATS)
        builds1 = km._PERF_STATS.snapshot()["builds"]
        self.assertEqual(views1["feedJsonBuild"] - views0["feedJsonBuild"], 1)
        self.assertEqual(views1["feedJsonServe"] - views0["feedJsonServe"], 1)
        self.assertEqual((views1["feedBuild"], views1["feedServe"]), (views0["feedBuild"], views0["feedServe"]),
                         "the pusher's counters did not move")
        self.assertEqual(builds1["feedJson"]["built"] - builds0["feedJson"]["built"], 1)
        self.assertEqual(builds1["feedJson"]["cached"] - builds0["feedJson"]["cached"], 1)
        self.assertEqual(builds1["feed"], builds0["feed"], "the pusher's /perf build kind did not move")

    def test_feed_json_serves_the_pushers_copy_only_while_the_pusher_has_an_audience(self):
        """With a client riding the feed payload the pusher maintains _built_feed, and the route
        serves exactly that, no build: the feed_src under the panes' frame (their copy carries _push's
        per-push ledgers attach on top, so not byte-identical to a pane's; review find, 2026-09-08).
        When the audience leaves the pusher stops rebuilding and the slot freezes at the last
        disconnect, so it is no longer the answer: the route builds its own copy and leaves the
        pusher's slot as it found it."""
        calls = []

        def counting_build(now, live):
            calls.append(now)
            return {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": now, "fresh": True}

        km.build_feed = counting_build
        warm = {"type": "feed", "asks": [], "working": [], "awaiting": [], "now": 1, "buildId": 7, "warm": True}
        km._built_feed[:] = [("SIG",), warm, time.time(), time.time()]
        feed_client, _ = _client("feed")
        km._clients.append(feed_client)
        st, d = self._get("/feed.json")
        self.assertEqual(st, 200)
        self.assertEqual(d, warm, "the pusher's copy, exactly")
        self.assertEqual(calls, [], "no build while the pusher maintains the slot")
        km._clients.remove(feed_client)              # the last pane closes: the pusher stops rebuilding
        st, d = self._get("/feed.json")
        self.assertEqual(st, 200)
        self.assertEqual(len(calls), 1, "a frozen copy is not served: the route builds its own")
        self.assertTrue(d.get("fresh"))
        self.assertIs(km._built_feed[1], warm, "…and the pusher's slot is untouched")

    def test_classify_requires_an_id(self):
        st, _ = self._get("/classify")
        self.assertEqual(st, 400)

    def test_classify_joins_the_existing_reads_and_is_read_only(self):
        # seed the stores the joined reads consume: a progressing state transition, and a nudge
        # ledger holding this session's goal record (deadWait flag) + a walk-gate journal entry
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "states" / (SID + ".jsonl")).write_text(
            json.dumps({"state": "working", "t": 1000}) + "\n")
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({
            "enabled": True,
            "nudged": {SID + ":g1": {"deadWait": True, "anchor": 5, "at": 6},
                       "someone-else:g9": {"at": 7}},
            "walkGates": {SID: {"gate": "compacting", "at": 8},
                          "someone-else": {"gate": "open-turn", "at": 9}}}))
        km._autonudge_cache.clear()
        before = _state_snapshot(self.td.name)
        st, d = self._get("/classify?id=" + SID)
        self.assertEqual(st, 200)
        self.assertEqual(d["id"], SID)
        self.assertFalse(d["live"], "no live snapshot in this world")
        self.assertEqual(d["state"], {"value": "working", "t": 1000}, "_last_state verbatim")
        self.assertFalse(d["idle"], "the nudge gate's own idle rule: working is progressing")
        self.assertIn("_PROGRESSING_STATES", d["idleRule"], "the input's provenance rides the payload")
        self.assertIsNone(d["awaiting"])
        self.assertIsNone(d["waitingOn"])
        self.assertEqual(d["owesAsks"], [])
        self.assertEqual(d["nudge"]["records"], {SID + ":g1": {"deadWait": True, "anchor": 5, "at": 6}},
                         "only THIS session's ledger rows — deadWait flags ride verbatim")
        self.assertEqual(d["nudge"]["walkGates"], {SID: {"gate": "compacting", "at": 8}},
                         "…and its walk-gate journal entries")
        self.assertTrue(d["nudge"]["enabled"])
        self.assertEqual(_state_snapshot(self.td.name), before, "a GET writes nothing")

    def test_classify_idle_when_the_state_says_stopped(self):
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "states" / (SID + ".jsonl")).write_text(
            json.dumps({"state": "waiting", "t": 2000}) + "\n")
        st, d = self._get("/classify?id=" + SID)
        self.assertTrue(d["idle"])


if __name__ == "__main__":
    unittest.main()
