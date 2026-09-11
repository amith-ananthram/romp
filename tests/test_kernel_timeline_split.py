"""Timeline ships in TWO messages so the lanes paint before the heavy bars (the user 2026-06-25, who found startup
still slow and wanted everything else loaded with the bars loaded after).

build_timeline(with_bars=False) builds only the LANES SKELETON (sessions/status, no turns/judging/messages/
nudges); _push sends it as {type:"data"} FIRST, then the cached full build's detail rides a {type:"bars"}
message. Profiling drove this: the timeline was 551ms/1940KB and ~95% of that is bars+judging, so the
skeleton is tiny and lands immediately. (The dead `tokens` field — nothing reads it — was dropped too.)

The skeleton BUILD runs only on the cold live-first connect. A warm push PROJECTS the skeleton from the cached
full build (_timeline_skeleton), serializes the frame once per build (_skel_wire) and dedups it on content the
way every {type:"data"} frame is deduped (the nested clock stripped) — see SkeletonFromCache below. An interval
a session is still in ends at the build's clock on the wire and carries an explicit open mark as its third
element (OpenIntervals below); the renderer reads the mark, never the distance between the end and data.now
(review find, 2026-09-08: a connect frame re-stamps the cycle clock over a cached build, so that distance is
the cache's age, not a fact about the lane).
"""
import inspect
import json
import os
import shutil
import subprocess
import time
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class BuildGating(unittest.TestCase):
    def test_build_timeline_gates_the_heavy_fields_on_with_bars(self):
        src = inspect.getsource(km.build_timeline)
        self.assertIn("with_bars=True", src, "the skeleton/bars switch")
        self.assertIn("if not with_bars:", src, "skeleton skips the per-segment bar dicts")
        self.assertIn("if with_bars:", src, "turns[sid] + judging + messages are gated")
        self.assertNotIn('"tokens"', src, "the dead token field is GONE from the payload (2026-07-07 audit)")
        self.assertNotIn('"nudges"', src, "…and so is the never-rendered nudges array")

    def test_the_host_shim_routes_a_bars_message_to_applyBars(self):
        boot = km._TIMELINE_BOOT
        self.assertIn('m.type==="bars"', boot)
        self.assertIn("panel.applyBars(m)", boot)

    def test_the_host_shim_wraps_its_listener_through_the_page_collector_when_there_is_one(self):
        # The page's performance collector (ui/webview/perf-telemetry.ts) is published on window.__rompPerf by
        # federation.js, which the timeline page loads before this boot (test_browser_owned_order pins the
        # order). Wrapped, the frames the boot dispatches are timed by type like every pane's, so the view's
        # hover/activeChat/revealEvent/models handling is not left inside federation's fed:<type> bracket
        # (2026-09-06); without a collector the plain listener is registered.
        boot = km._TIMELINE_BOOT
        self.assertIn("var onFrame=function(ev){var m=ev.data;if(!m||!panel)return;", boot)
        self.assertIn('var frameListener=(window.__rompPerf&&window.__rompPerf.wrapFrameHandler)'
                      "?window.__rompPerf.wrapFrameHandler(onFrame):onFrame;", boot)
        self.assertIn('window.addEventListener("message",frameListener);', boot)
        self.assertEqual(boot.count('addEventListener("message"'), 1, "one listener, the wrapped one")

    def test_the_host_shim_registers_the_same_listener_with_federation_for_direct_delivery(self):
        # federation.js hands its merged data/bars frames to the handlers registered through window.__rompFed.onFrame
        # by direct call, and dispatches them on window only when nothing registered (ui/webview/federation.ts
        # emit): a "message" listener in another JavaScript world that reads event.data forces a structured clone of
        # the frame on every window dispatch. The boot registers the SAME wrapped listener it puts on window, so a
        # frame reaches it once, timed the same way; a page without the slot (an older federation.js) is unchanged.
        boot = km._TIMELINE_BOOT
        self.assertIn("if(window.__rompFed&&window.__rompFed.onFrame)window.__rompFed.onFrame(frameListener);", boot)
        self.assertLess(boot.index('window.addEventListener("message",frameListener);'),
                        boot.index("window.__rompFed.onFrame(frameListener)"), "window first, the registry after it")

    def test_the_host_shim_run_one_wrapped_listener_registered_with_federation_reaches_the_panel(self):
        # The boot RUN (review find, 2026-09-08; moved here from the TypeScript lane, which must not break on a
        # kernel edit): the self-contained IIFE under node's vm with the three window slots it reads stood in.
        # One window listener, the perf-wrapped one; the registry gets that same function, so a frame arrives
        # once whichever path carries it; a frame through the registry reaches the connected panel.
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "boot.js"), "w") as f:
            f.write(km._TIMELINE_BOOT)
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(r"""
var vm = require("vm"), fs = require("fs");
var boot = fs.readFileSync(process.argv[2], "utf8");
var listeners = [], registered = [], updates = [], posted = [], wrapped = new Map();
var win = {
  acquireVsCodeApi: function () { return { postMessage: function (m) { posted.push(m); } }; },
  addEventListener: function (t, h) { if (t === "message") listeners.push(h); },
  __rompPerf: { wrapFrameHandler: function (h) { var w = function (e) { return h(e); }; wrapped.set(w, h); return w; } },
  __rompFed: { onFrame: function (h) { registered.push(h); return function () {}; } },
};
win.window = win;
vm.runInNewContext(boot, { window: win, HTMLElement: { prototype: {} }, document: {}, URL: URL });
var out = { listeners: listeners.length, wrappedIsListener: wrapped.has(listeners[0]),
            registeredSame: registered.length === 1 && registered[0] === listeners[0] };
win.__rompConnectTimeline({ update: function (d) { updates.push(d); } });
out.posted = posted.map(function (m) { return m.type; });
registered[0]({ data: { type: "data", data: { lanes: 1 } } });
out.updates = updates;
process.stdout.write(JSON.stringify(out));
""")
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "boot.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["listeners"], 1, "one window listener")
        self.assertTrue(out["wrappedIsListener"], "…the wrapped one, so its frames are timed by type")
        self.assertTrue(out["registeredSame"], "the SAME function is registered with federation: no frame arrives twice")
        self.assertEqual(out["posted"], ["ready"], "the connect handshake")
        self.assertEqual(out["updates"], [{"lanes": 1}], "a frame through the registry reached the panel")

    def test_the_lanes_skeleton_does_not_parse_any_transcript(self):
        # cold-start speed (the user 2026-06-26): a fresh kernel (the refresh button = POST /restart) re-parses
        # every transcript (~1.3s). The lanes don't need it — derive them from tmux + goals + the transcript
        # mtime. Only the {type:"bars"} build (with_bars=True) does the real parse.
        calls = {"parse": 0}
        o_ts, o_parse = km._timeline_sessions, km._parse
        km._timeline_sessions = lambda now, tmux, live_only=False: [{"sid": "S", "name": "n", "path": "/no/such/transcript"}]
        km._parse = lambda path, sid, now: (calls.__setitem__("parse", calls["parse"] + 1), {"turns": []})[1]
        try:
            km.build_timeline(0, {}, with_bars=False)
            self.assertEqual(calls["parse"], 0, "the lanes skeleton must NOT parse a transcript")
            km.build_timeline(0, {}, with_bars=True)
            self.assertGreater(calls["parse"], 0, "the bars build DOES parse")
        finally:
            km._timeline_sessions, km._parse = o_ts, o_parse


class TimelinePageLoader(unittest.TestCase):
    """The timeline pane no longer carries the full-pane _pane_spin("host") overlay (the user 2026-06-26): it
    hid the instant #host got its first child — the .romp-tl-wrap on TimelinePanel construction, BEFORE any
    bars — leaving an empty bar gap. The view owns the bars-area loader now (_drawBarsLoader, gated on
    _barsLoaded), so the spinner stays until the deferred {type:"bars"} payload renders."""

    def test_timeline_page_drops_the_full_pane_spin_but_keeps_the_host(self):
        page = km._timeline_page()
        self.assertIn("<div id=host>", page, "the timeline still mounts into #host")
        self.assertNotIn("id=pane-spin", page, "no full-pane _pane_spin overlay (it hid before the bars)")

    def test_other_panes_keep_their_pane_spin(self):
        # the chat/feed/fleet loaders are unaffected — only the timeline's was dropped
        self.assertIn("id=pane-spin", km._pane_spin("content"))


class PushSplit(unittest.TestCase):
    def test_push_ships_the_lanes_skeleton_before_the_bars(self):
        sent, builds = [], []
        client = {"app": "timeline", "send": sent.append, "sent": {}, "alive": True}
        SKEL = {"type": "timeline", "sessions": [{"id": "S"}], "turns": {}, "judging": {},
                "messages": [], "now": 1, "usage": {}}
        FULL = {"type": "timeline", "sessions": [{"id": "S"}], "turns": {"S": [{"id": "b1"}]},
                "judging": [{"k": "planner"}], "messages": [{"m": 1}], "now": 1}
        o_bt, o_ct, o_tmux, o_sig = (km.build_timeline, km._cached_timeline,
                                     km._live_map, km._fleet_view_sig)
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (
            builds.append(with_bars) or (FULL if with_bars else SKEL))
        km._cached_timeline = lambda now, tmux, sig, connect=False: FULL
        km._live_map = lambda: {}
        km._fleet_view_sig = lambda now, tmux: ("sig",)
        try:
            km._push([client])
        finally:
            (km.build_timeline, km._cached_timeline,
             km._live_map, km._fleet_view_sig) = o_bt, o_ct, o_tmux, o_sig
        msgs = [json.loads(s) for s in sent]
        types = [m["type"] for m in msgs]
        self.assertIn("data", types)
        self.assertIn("bars", types)
        self.assertLess(types.index("data"), types.index("bars"), "lanes skeleton ships BEFORE the bars")
        skel = msgs[types.index("data")]["data"]
        self.assertEqual(skel["turns"], {}, "the {type:data} message is the lanes skeleton — no bars")
        bars = msgs[types.index("bars")]
        self.assertEqual(bars["turns"], {"S": [{"id": "b1"}]}, "the heavy bars ride the {type:bars} message")
        for k in ("judging", "messages"):
            self.assertIn(k, bars, "the whole time-plotted detail rides the bars message")
        self.assertEqual(builds, [], "a warm push builds no skeleton: the lanes are projected from the cache")
        self.assertEqual(skel["sessions"], FULL["sessions"], "…and they are the cached build's lanes")

    def test_a_steady_push_of_an_unchanged_timeline_sends_no_bars_and_a_rebuilt_one_sends_a_slotted_delta(self):
        """The pusher hands _send_slot the same bars object while the cached timeline's identity holds (_bars_wire),
        so a delta client's unchanged cycle runs no per-entry compare and sends nothing. When the timeline is
        rebuilt the bars cross as a delta frame, with the slot key on the client while it goes."""
        sent = []
        client = {"app": "timeline", "sent": {}, "alive": True, "delta": True}
        client["send"] = lambda s: sent.append((client.get("curSlot"), json.loads(s)))
        SKEL = {"type": "timeline", "sessions": [{"id": "S"}], "turns": {}, "judging": {},
                "messages": [], "now": 1, "usage": {}}
        FULL1 = {"type": "timeline", "sessions": [{"id": "S"}], "turns": {"S": [{"id": "b1"}]},
                 "judging": {}, "messages": [], "now": 1}
        FULL2 = {"type": "timeline", "sessions": [{"id": "S"}], "turns": {"S": [{"id": "b1"}, {"id": "b2"}]},
                 "judging": {}, "messages": [], "now": 2}
        holder = {"tl": FULL1}
        calls = []
        o_bt, o_ct, o_tmux, o_sig, o_frac, o_order, o_wire = (km.build_timeline, km._cached_timeline, km._live_map,
                                                             km._fleet_view_sig, km._DELTA_MAX_FRACTION, km._client_order,
                                                             km._bars_wire)
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (holder["tl"] if with_bars else SKEL)
        km._cached_timeline = lambda now, tmux, sig, connect=False: holder["tl"]
        km._live_map = lambda: {}
        km._fleet_view_sig = lambda now, tmux: ("sig",)
        km._DELTA_MAX_FRACTION = 10.0         # synthetic payloads are tiny: the size guard would send the whole instead
        km._client_order = lambda *a: calls.append(1) or o_order(*a)
        try:
            km._push([client])                # the skeleton and the keyed full bars
            km._push([client])                # the same timeline object: the same bars object
            n_sent, n_calls = len(sent), len(calls)
            km._push([client])
            self.assertEqual(len(sent), n_sent, "an unchanged timeline sends no bars frame")
            self.assertEqual(len(calls), n_calls, "…and runs no per-entry compare to find that out")
            holder["tl"] = FULL2              # a rebuild: a new timeline object with one more bar
            km._push([client])
        finally:
            (km.build_timeline, km._cached_timeline, km._live_map, km._fleet_view_sig, km._DELTA_MAX_FRACTION,
             km._client_order, km._bars_wire) = o_bt, o_ct, o_tmux, o_sig, o_frac, o_order, o_wire
        bars = [(k, m) for k, m in sent if m["type"] in ("bars", "delta")]
        self.assertEqual([m["type"] for _k, m in bars], ["bars", "delta"])
        self.assertEqual([k for k, _m in bars], [("timelinebars",), ("timelinebars",)],
                         "both bars frames went with the slot key on the client")
        self.assertEqual(set(bars[1][1]["coll"]["turns"]["set"]), {"S\u001fb2"}, "one bar crosses")


class SkeletonFromCache(unittest.TestCase):
    """The warm-path skeleton is a PROJECTION of the cached full build. Every pusher cycle used to run
    build_timeline(with_bars=False) — no parse, but the same per-lane derivation the cached full build had just
    done — and then serialized the frame and re-dumped it once more per client for the dedup compare. A hit
    now serves the last rebuild's lanes byte-for-byte, so a lane moves only when the timeline rebuilds: on a
    view-sig change, on _mark_views_dirty, or at the 5 s bucket. The frame is deduped on CONTENT, the nested
    clock stripped, as every {type:"data"} frame is: a rebuild whose lanes are unchanged sends nothing, and the
    60 s repost is what refreshes the pane's clock sample."""

    FULL = {"type": "timeline", "now": 1,
            "sessions": [{"id": "S", "name": "web", "state": "working", "context": 10,
                          "compactions": [{"t": 5}]}],
            "turns": {"S": [{"id": "b1"}]}, "judging": [{"k": "planner"}], "messages": [{"m": 1}],
            "views": {"active": "all"}, "usage": {"five": {"pct": 3}}}

    class _Clock:
        """km's `time` with a settable skew on time() — so two pushes can carry different clocks without
        sleeping, and everything else (monotonic, sleep) passes through."""
        def __init__(self, real):
            self._real, self.skew = real, 0.0

        def __getattr__(self, name):
            return getattr(self._real, name)

        def time(self):
            return self._real.time() + self.skew

    def setUp(self):
        self.saved = (km.build_timeline, km._cached_timeline, km._live_map, km._fleet_view_sig, km.time)
        self.saved_built, self.saved_dirty = list(km._built_timeline), km._views_dirty[0]
        self.saved_wire = (km._bars_wire, km._skel_wire)
        km._bars_wire = km._skel_wire = None
        self.builds = []
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (
            self.builds.append((with_bars, live_only)) or self.rebuilt())
        km._cached_timeline = lambda now, tmux, sig, connect=False: self.FULL
        km._live_map = lambda: {}
        km._fleet_view_sig = lambda now, tmux: ("sig",)
        self.clock = km.time = self._Clock(time)

    def tearDown(self):
        (km.build_timeline, km._cached_timeline, km._live_map, km._fleet_view_sig, km.time) = self.saved
        km._built_timeline[:] = self.saved_built
        km._views_dirty[0] = self.saved_dirty
        km._bars_wire, km._skel_wire = self.saved_wire

    def rebuilt(self, now=2):
        return dict(self.FULL, now=now, sessions=[dict(self.FULL["sessions"][0], state="ready")])

    @staticmethod
    def _client():
        frames = []
        return {"app": "timeline", "send": frames.append, "sent": {}, "alive": True}, frames

    @staticmethod
    def _data_frames(frames):
        return [json.loads(f) for f in frames if json.loads(f)["type"] == "data"]

    def test_two_pushes_over_an_unchanged_cache_build_nothing_and_resend_no_lanes(self):
        c, frames = self._client()
        km._push([c])
        first = list(frames)
        self.clock.skew = 7.0                       # a later second AND a later 5 s bucket: only the clock moved
        km._push([c])
        self.assertEqual(self.builds, [], "a warm push runs no build_timeline at all — no skeleton build")
        data = self._data_frames(first)
        self.assertEqual(len(data), 1, "the first push ships the lanes frame")
        self.assertEqual(data[0]["data"]["sessions"], self.FULL["sessions"], "…as the cached build's lanes")
        self.assertEqual(data[0]["data"]["now"], self.FULL["now"], "…stamped with the BUILD's clock, not the cycle's")
        self.assertEqual(data[0]["data"]["turns"], {})
        self.assertEqual(data[0]["data"]["judging"], {})
        self.assertEqual(data[0]["data"]["messages"], [])
        self.assertEqual(data[0]["data"]["usage"], self.FULL["usage"], "usage rides the projected skeleton")
        second = frames[len(first):]
        self.assertEqual(sum(len(f) for f in second if json.loads(f)["type"] == "data"), 0,
                         "the second push sent zero bytes on the lanes slot: the same cached build, nothing new")
        self.assertEqual(self.FULL["turns"], {"S": [{"id": "b1"}]}, "the cached build itself is never mutated")
        self.assertEqual(self.FULL["now"], 1)

    def test_the_projection_is_a_copy_with_the_build_clock_and_dedups_on_content(self):
        skel = km._timeline_skeleton(self.FULL)
        self.assertEqual(skel["now"], self.FULL["now"], "the projected frame carries the build's clock")
        self.assertEqual((skel["turns"], skel["judging"], skel["messages"]), ({}, {}, []))
        self.assertEqual(skel["sessions"], self.FULL["sessions"])
        self.assertIsNot(skel, self.FULL)
        self.assertEqual(self.FULL["turns"], {"S": [{"id": "b1"}]}, "a projection is a copy, not a mutation")

        def sig(tl):
            frame = {"type": "data", "data": km._timeline_skeleton(tl)}
            return km._dedup_sig(frame, json.dumps(frame))
        self.assertEqual(sig(self.FULL), sig(dict(self.FULL, now=2)),
                         "two builds whose lanes agree dedup whatever their clocks: the nested `now` is stripped")
        self.assertNotEqual(sig(self.FULL), sig(self.rebuilt(now=1)), "a lane state change compares different")

    def test_a_rebuild_with_unchanged_lanes_sends_nothing_and_a_changed_one_sends_one_frame(self):
        # The frame dedups on content, so a rebuild is not by itself a send: the 5 s bucket rolling on a quiet
        # timeline rebuilds once and ships nothing (the 60 s repost, _DEDUP_REPOST_S, refreshes the clock), a
        # rebuild that moved a lane ships exactly one frame carrying the new build's clock, and the cycles
        # between rebuilds send nothing at all.
        km._cached_timeline = self.saved[1]             # the real cache
        sig, lanes = [("sig",)], [self.FULL["sessions"]]
        km._fleet_view_sig = lambda now, tmux: sig[0]
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (
            self.builds.append((with_bars, live_only)) or dict(self.FULL, now=now, sessions=lanes[0]))
        t = time.time()
        km._built_timeline[:] = [("sig",), self.FULL, t, t]
        km._views_dirty[0] = 0.0
        c, frames = self._client()
        km._push([c])                                   # cycle 1: a hit → the client's first frame
        self.clock.skew = 1.0
        km._push([c])                                   # cycle 2: the same build → nothing
        self.assertEqual(self.builds, [])
        data = self._data_frames(frames)
        self.assertEqual(len(data), 1, "two cycles on one cached build send the lanes frame once")
        self.assertEqual(data[0]["data"]["now"], self.FULL["now"])
        n = len(frames)
        self.clock.skew, sig[0] = 7.0, ("sig", "next bucket")   # the bucket rolled: a rebuild with identical lanes
        km._push([c])
        self.assertEqual(self.builds, [(True, False)], "the rebuild ran once, and built no skeleton")
        self.assertEqual(frames[n:], [], "identical lanes under a new clock: no lanes frame, no bars frame")
        n = len(frames)
        self.clock.skew, sig[0] = 14.0, ("sig", "a lane moved")
        lanes[0] = self.rebuilt()["sessions"]
        km._push([c])
        self.assertEqual(self.builds, [(True, False), (True, False)])
        data = self._data_frames(frames[n:])
        self.assertEqual(len(data), 1, "a rebuild that changed a lane sends the lanes frame once")
        self.assertEqual(data[0]["data"]["sessions"][0]["state"], "ready", "…carrying the rebuild's lanes")
        self.assertGreaterEqual(data[0]["data"]["now"], int(t + 14), "…stamped with the new build's clock")
        self.assertLessEqual(data[0]["data"]["now"], int(self.clock.time()))
        n = len(frames)
        self.clock.skew = 15.0
        km._push([c])                                   # a cycle between rebuilds → nothing
        self.assertEqual(self.builds, [(True, False), (True, False)])
        self.assertEqual(frames[n:], [], "an unchanged cycle between rebuilds sends nothing at all")

    def test_a_connect_push_stamps_the_cached_lanes_with_the_cycle_clock(self):
        # A fresh pane anchors its live edge and window fit on the first data.now it sees, and the cache is as
        # old as the last cycle that had a timeline client (a bucket on a reload; hours after the pane was
        # closed), so the build clock would sit the axis in the past. The connect frame carries the cycle's
        # clock; the next regular cycle's build-clock frame has the same content once the clock is stripped,
        # so it dedups and the pane keeps the sample it anchored on.
        km._built_timeline[:] = [("sig",), self.FULL, 1.0, 1.0]     # a warm cache whose build clock (FULL["now"] = 1) is ancient
        km._views_dirty[0] = 0.0                        # …and FRESH by the kernel's own rule: built under the cycle's sig, no
        #                                                 dirty mark since (a stale cache builds its lanes fresh: the tests below)
        c, frames = self._client()
        km._push([c], connect=True)
        self.assertEqual(self.builds, [], "a warm connect builds nothing: the cached lanes are served")
        data = self._data_frames(frames)
        self.assertEqual(len(data), 1)
        self.assertGreaterEqual(data[0]["data"]["now"], int(time.time()) - 1, "…under the cycle's clock, not the cached build's")
        self.assertEqual(data[0]["data"]["sessions"], self.FULL["sessions"], "…over the cached lanes")
        self.assertEqual(self.FULL["now"], 1, "the cached build is not restamped")
        n = len(frames)
        km._push([c])                                   # the next regular cycle: the same lanes under the build clock
        self.assertEqual(self._data_frames(frames[n:]), [], "…which dedups: the same content, the clock stripped")
        n = len(frames)
        km._push([c])
        self.assertEqual(self._data_frames(frames[n:]), [], "…and unchanged cycles send nothing after that")

    def test_a_connect_over_a_stale_cache_builds_the_lanes_fresh_and_serves_the_cached_bars(self):
        # The cache is as old as the last cycle that had a timeline client (hours, when the pane was closed),
        # and a connect never rebuilds the full build. Re-stamping the cycle clock over lanes that old painted
        # a lane dead for hours as live (and the reverse) until the next cycle's rebuild replaced them: a flap
        # on every reload. The kernel's own freshness rule decides (_timeline_cache_fresh: built under the
        # cycle's view signature or within REBUILD_MIN_S, no dirty mark since): a fresh cache is projected as
        # above; a stale one gets its lanes built fresh, as every connect did before the projection, while the
        # heavy bars still come from the cache (review find, 2026-09-08).
        km._built_timeline[:] = [("older",), self.FULL, 1.0, 1.0]   # built under a signature the world has since left
        km._views_dirty[0] = 0.0
        c, frames = self._client()
        km._push([c], connect=True)
        self.assertEqual(self.builds, [(False, False)], "the lanes are built fresh (no bars); the full build is not")
        data = self._data_frames(frames)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["data"]["sessions"], self.rebuilt()["sessions"], "…and the fresh lanes are what goes")
        self.assertEqual(data[0]["data"]["now"], self.rebuilt()["now"],
                         "under the fresh build's own clock (the real build_timeline takes the cycle's), not a re-stamp")
        bars = [json.loads(f) for f in frames if json.loads(f)["type"] == "bars"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["turns"], self.FULL["turns"], "the bars are the cached build's, as before")

    def test_a_connect_over_a_cache_marked_dirty_since_its_build_builds_the_lanes_fresh(self):
        km._built_timeline[:] = [("sig",), self.FULL, 1.0, 1.0]     # the cycle's own signature…
        km._views_dirty[0] = 1.5                        # …but a writer marked the views dirty after the build started
        c, frames = self._client()
        km._push([c], connect=True)
        self.assertEqual(self.builds, [(False, False)], "a dirty mark is staleness too")
        self.assertEqual(self._data_frames(frames)[0]["data"]["sessions"], self.rebuilt()["sessions"])

    def test_a_dirty_mark_between_two_pushes_rebuilds_and_ships_the_new_lanes(self):
        km._cached_timeline = self.saved[1]             # the real cache, warmed with FULL under the stubbed sig
        km._built_timeline[:] = [("sig",), self.FULL, time.time(), time.time()]
        km._views_dirty[0] = 0.0
        c, frames = self._client()
        km._push([c])
        self.assertEqual(self.builds, [], "an unchanged sig on a warm cache is a hit")
        n = len(frames)
        km._mark_views_dirty()                          # an optimistic kernel-side mutation: the deciding event
        km._push([c])
        self.assertEqual(self.builds, [(True, False)], "the dirty mark rebuilt the FULL timeline, once, and no skeleton")
        data = self._data_frames(frames[n:])
        self.assertEqual(len(data), 1, "the rebuilt lanes ship")
        self.assertEqual(data[0]["data"]["sessions"][0]["state"], "ready", "…carrying the rebuild's state")

    def test_the_skeleton_carries_the_cached_builds_compactions(self):
        # The built skeleton set session = {"turns": []}, so its `compactions` was always [] and the
        # timeline never drew a compaction marker; the projection carries the full build's list, which
        # comes from the fresh parse (the authoritative source). The stub below is the OLD skeleton's
        # shape (no compactions), so a push that built a skeleton would ship [] here.
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (
            self.rebuilt() if with_bars else dict(self.FULL, turns={}, judging=[], messages=[],
                                                  sessions=[dict(self.FULL["sessions"][0], compactions=[])]))
        c, frames = self._client()
        km._push([c])
        lane = self._data_frames(frames)[0]["data"]["sessions"][0]
        self.assertEqual(lane["compactions"], [{"t": 5}])

    def test_the_view_sig_keys_every_row_field_the_lanes_read(self):
        # The sig read `ctx`, which no liveness row carries (rows write `context`), so a context-% change
        # never busted the cache; the fast-mode reason, the subagent rows and the task ids were never keyed.
        # With the skeleton projected from the cache, each of these must bust it or the lane holds the old
        # value until the bucket.
        sig = self.saved[3]
        row = {"state": "working", "model": "m", "effort": "high", "mode": "", "fast": "on", "since": 100,
               "context": 10, "fastReason": "", "modelPending": False, "effortPending": False,
               "authPending": False, "retryCount": 0, "connected": True, "spawning": False,
               "subagents": [], "bgTasks": []}
        base = sig(1000, {"S": dict(row)})
        self.assertEqual(base, sig(1000, {"S": dict(row)}), "stable on identical rows")
        for k, v in (("context", 20), ("fastReason", "cooldown"), ("modelPending", True),
                     ("effortPending", True), ("authPending", True), ("retryCount", 3),
                     ("connected", False), ("spawning", True),
                     ("subagents", [{"type": "Explore", "since": 90}]),
                     ("bgTasks", [{"toolUseId": "t1", "desc": "watch the build", "since": 90}])):
            self.assertNotEqual(base, sig(1000, {"S": {**row, k: v}}), "%s must bust the view sig" % k)
        one = {**row, "subagents": [{"type": "Explore", "since": 90}]}
        self.assertNotEqual(sig(1000, {"S": one}), sig(1000, {"S": {**row, "subagents": [{"type": "Explore", "since": 95}]}}),
                            "a subagent row's fields count, not only how many rows there are: the lane ships the list")
        self.assertEqual(sig(1000, {"S": {**row, "bgTasks": [{"toolUseId": "t1", "lastTool": "Read"}]}}),
                         sig(1000, {"S": {**row, "bgTasks": [{"toolUseId": "t1", "lastTool": "Grep"}]}}),
                         "a task's progress fields are not a lane fact: keyed on the task ids only")
        self.assertNotEqual(sig(1000, {"S": {**row, "bgTasks": [{"toolUseId": "t1"}]}}),
                            sig(1000, {"S": {**row, "bgTasks": [{"toolUseId": "t2"}]}}),
                            "…and a swapped task with the same count busts it")
        self.assertEqual(base, sig(1000, {"S": {**row, "snapT": 123456.0}}), "the snapshot's clock stays out")
        self.assertEqual(base, sig(1000, {"S": {**row, "interrupting": True}}),
                         "`interrupting` is not keyed: the merged liveness row never carries it (the SDK merge copies "
                         "an explicit key list), and the WS stop op marks the views dirty itself")

    def test_the_view_sig_stats_the_files_the_lanes_read(self):
        sig = self.saved[3]
        st = km.jd.STATE
        st.mkdir(parents=True, exist_ok=True)
        usage, views, watches = st / "usage.json", km._views_path(), km.WATCH_FILE
        csid = "33333333-4444-4444-4444-555555555555"     # a private synthetic sid: a comment thread's parent
        cdir, cfile = st / "comments", km._comments_path(csid)
        for f in (usage, views, watches, cfile):
            if f.exists():
                f.unlink()
        try:
            base = sig(1000, {})
            usage.write_text(json.dumps({"five_hour": {"pct": 12}}))
            with_usage = sig(1000, {})
            self.assertNotEqual(base, with_usage, "a usage.json write busts the sig: the lanes frame carries the usage bars")
            views.write_text(json.dumps({"active": "all", "tags": []}))
            with_views = sig(1000, {})
            self.assertNotEqual(with_usage, with_views, "a timeline-views.json write busts the sig: the views blob rides every frame")
            km._save_comments(csid, {"threads": [{"tid": "t1", "status": "open", "createdT": 900}]})
            with_comment = sig(1000, {})
            self.assertNotEqual(with_views, with_comment, "a first comment thread busts the sig: the lane's squares read the store")
            # a REWRITE of the same file (a resolve on a dormant thread changes only the store): the atomic
            # replace lands in the comments directory, so its mtime moves — aged first, so the check does
            # not ride on the clock's granularity
            os.utime(cdir, (1_000_000, 1_000_000))
            aged = sig(1000, {})
            km._save_comments(csid, {"threads": [{"tid": "t1", "status": "resolved", "createdT": 900}]})
            self.assertNotEqual(aged, sig(1000, {}), "a resolve on an existing thread busts the sig too")
            before_watch = sig(1000, {})
            watches.write_text("[]")
            self.assertNotEqual(before_watch, sig(1000, {}), "a watches.json write busts the sig: the awaiting badge reads the watches")
        finally:
            for f in (usage, views, watches, cfile):
                if f.exists():
                    f.unlink()
            try:
                cdir.rmdir()
            except OSError:
                pass


class SkeletonSerializedOncePerBuild(unittest.TestCase):
    """The lanes frame is serialized once per BUILD (_skel_wire) and every timeline client is handed that
    serialization and its signature (pre=skel_pre, sig=skel_sig): a warm cycle over an unchanged cache encodes
    nothing on the lanes slot, however many clients are connected. Pinned on json.dumps calls through the real
    _push (a memo dropped, or a client re-dumping the frame for its own compare, both show up as calls)."""

    FULL = SkeletonFromCache.FULL

    class _Json:
        """km's `json` with dumps() counted; everything else passes through."""
        def __init__(self, real):
            self._real, self.dumps_calls = real, 0

        def __getattr__(self, name):
            return getattr(self._real, name)

        def dumps(self, *a, **k):
            self.dumps_calls += 1
            return self._real.dumps(*a, **k)

    def setUp(self):
        self.saved = (km.build_timeline, km._cached_timeline, km._live_map, km._fleet_view_sig, km.json)
        self.saved_wire = (km._skel_wire, km._bars_wire)
        km._skel_wire = km._bars_wire = None
        km.build_timeline = lambda *a, **k: self.fail("a warm push builds nothing")
        km._cached_timeline = lambda now, tmux, sig, connect=False: self.FULL
        km._live_map = lambda: {}
        km._fleet_view_sig = lambda now, tmux: ("sig",)
        self.json = km.json = self._Json(json)

    def tearDown(self):
        (km.build_timeline, km._cached_timeline, km._live_map, km._fleet_view_sig, km.json) = self.saved
        km._skel_wire, km._bars_wire = self.saved_wire

    def test_two_clients_share_one_serialization_and_a_warm_cycle_encodes_nothing(self):
        c1, f1 = SkeletonFromCache._client()
        c2, f2 = SkeletonFromCache._client()
        km._push([c1, c2])
        d1 = [f for f in f1 if json.loads(f)["type"] == "data"]
        d2 = [f for f in f2 if json.loads(f)["type"] == "data"]
        self.assertEqual(len(d1), 1); self.assertEqual(len(d2), 1)
        self.assertEqual(d1[0], d2[0], "byte-identical: the one serialization went to both")
        self.json.dumps_calls = 0
        km._push([c1, c2])
        self.assertEqual(self.json.dumps_calls, 0,
                         "the second cycle encoded nothing: the frame, its signature and the bars all came from the wire caches")
        self.assertEqual(len([f for f in f1 + f2 if json.loads(f)["type"] == "data"]), 2, "…and sent no lanes frame")


class ProjectionMatchesColdSkeleton(unittest.TestCase):
    """The warm projection (_timeline_skeleton over the full build) and the cold-connect skeleton build
    (build_timeline(with_bars=False)) are ONE wire shape: the same keys, the same lane fields and values, differing
    only where the commit says they may — a lane's `compactions` (the built skeleton parses nothing and ships [])
    and a dead lane's `since` — since the renderer reads both frames through one code path."""

    SID = "66666666-7777-8888-9999-bbbbbbbbbbbb"      # a private synthetic sid

    def setUp(self):
        self.now = int(time.time())
        d = km.jd.STATE / "synthetic-transcripts"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / (self.SID + ".jsonl")
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
        recs = [{"type": "user", "timestamp": iso(self.now - 600), "uuid": "u1", "parentUuid": None,
                 "promptSource": "typed", "message": {"role": "user", "content": "wire up the reconnect banner"}},
                {"type": "assistant", "timestamp": iso(self.now - 590), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}]
        self.path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.saved = km._timeline_sessions
        km._timeline_sessions = lambda now, tmux, live_only=False: [
            {"sid": self.SID, "name": "web", "anchor": None, "path": str(self.path), "mtime": self.now - 590}]
        self.live = {self.SID: {"state": "working", "since": self.now - 590, "model": "m", "effort": "high",
                                "context": 42, "compactPct": None, "color": None, "mode": ""}}

    def tearDown(self):
        km._timeline_sessions = self.saved
        km._parse_cache.pop(str(self.path), None)
        if self.path.exists():
            self.path.unlink()

    def test_the_projection_and_the_cold_skeleton_are_one_wire_shape(self):
        full = km.build_timeline(self.now, self.live, with_bars=True)     # the full build first: the cold path reads its cached parse
        cold = km.build_timeline(self.now, self.live, with_bars=False)
        proj = km._timeline_skeleton(full)
        self.assertEqual(set(proj), set(cold), "the same top-level keys")
        self.assertEqual((proj["turns"], proj["judging"], proj["messages"]),
                         (cold["turns"], cold["judging"], cold["messages"]), "the heavy fields are empty on both")
        self.assertEqual(len(proj["sessions"]), 1); self.assertEqual(len(cold["sessions"]), 1)
        p, c = proj["sessions"][0], cold["sessions"][0]
        self.assertEqual(set(p), set(c), "the same lane fields")
        self.assertLessEqual({k for k in p if p[k] != c[k]}, {"compactions", "since"},
                             "…with the same values, but for the two documented differences")
        self.assertEqual(p["state"], c["state"], "the cold path derives the chip over the cached parse, as the full build did")
        self.assertNotEqual(p["state"], "working", "…not the raw snapshot state (the turn ended)")
        self.assertEqual(p["context"], 42); self.assertTrue(p["live"])


class OpenIntervals(unittest.TestCase):
    """An interval the session is STILL in ends at the build's clock on the wire AND carries True as a third
    element, the open mark the renderer's open detection reads (review find, 2026-09-08). The mark is the
    lane's own state, not a clock compare: the renderer used to read an end within 2 s of the payload's `now`
    as open, and a connect frame re-stamps the cycle clock over the cached build, so that distance was the
    cache's age and a lane blocked right now drew closed. Pinned because a null
    end would be a wire break: every already-loaded renderer (an open dashboard, an installed extension) takes
    Math.min(null, t1) = 0 and drops the stripe for a lane blocked or compacting right now. The clock-stamped
    end costs no per-cycle work: the lanes frame is serialized once per build, and while the state lasts the
    frame goes once per rebuild — where the per-cycle skeleton sent it every cycle."""

    SID = "66666666-7777-8888-9999-aaaaaaaaaaaa"      # a private synthetic sid

    def setUp(self):
        self.p = km.jd.STATE / "states" / (self.SID + ".jsonl")
        self.p.parent.mkdir(parents=True, exist_ok=True)
        km._state_ev_cache.pop(str(self.p), None)
        self.now = 1_000_000
        with open(self.p, "w") as f:
            for row in ({"t": self.now - 400, "state": "permission"}, {"t": self.now - 300, "state": "working"},
                        {"t": self.now - 100, "state": "permission"}):
                f.write(json.dumps(row) + "\n")

    def tearDown(self):
        if self.p.exists():
            self.p.unlink()
        km._state_ev_cache.pop(str(self.p), None)

    def test_an_open_interval_ends_at_the_build_clock_and_a_closed_one_at_its_transition(self):
        now = self.now
        self.assertEqual(km._state_intervals(self.SID, km._NEEDS_INPUT_STATES, now),
                         [[now - 400, now - 300], [now - 100, now, True]])
        self.assertEqual(km._state_intervals(self.SID, "compacting", now), [])
        wire = json.loads(json.dumps(km._state_intervals(self.SID, "permission", now)))
        self.assertEqual(wire[-1], [now - 100, now, True], "the open end serializes as the numeric clock, never null, marked open")
        self.assertEqual(len(wire[0]), 2, "a closed interval carries no mark")

    def test_the_lane_payload_ends_the_open_interval_at_its_own_clock(self):
        o_ts = km._timeline_sessions
        km._timeline_sessions = lambda now, tmux, live_only=False: [
            {"sid": self.SID, "name": "web", "path": "/no/such/transcript-web"}]
        live = {self.SID: {"state": "permission", "since": self.now - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        try:
            tl = km.build_timeline(self.now, live, with_bars=False)
        finally:
            km._timeline_sessions = o_ts
        lane = tl["sessions"][0]
        self.assertEqual(lane["awaiting"], [[self.now - 400, self.now - 300], [self.now - 100, self.now, True]])
        self.assertEqual(lane["awaiting"][-1][1], tl["now"], "the open end equals the payload's clock")
        self.assertIs(lane["awaiting"][-1][2], True, "…and the open mark is what the renderer reads as open")

    def test_a_connect_frame_restamped_over_a_cached_build_keeps_the_open_mark(self):
        # The connect push serves the cached lanes under the CYCLE's clock (SkeletonFromCache), so the open
        # interval's end, still at the BUILD clock, sits behind the frame's `now` by the cache's age. The mark
        # travels with the interval, so the renderer draws the stripe to the live edge however old the cache is
        # (review find, 2026-09-08: the 2 s tolerance read a lane blocked right now as closed on every connect
        # over a cache older than that).
        built = int(time.time()) - 10                   # the cached build's clock: 10 s behind the connect
        km._state_ev_cache.pop(str(self.p), None)
        with open(self.p, "w") as f:
            for row in ({"t": built - 400, "state": "permission"}, {"t": built - 300, "state": "working"},
                        {"t": built - 100, "state": "permission"}):
                f.write(json.dumps(row) + "\n")
        live = {self.SID: {"state": "permission", "since": built - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        saved = (km._timeline_sessions, km._live_map, km._fleet_view_sig, list(km._built_timeline),
                 km._views_dirty[0], km._skel_wire, km._bars_wire)
        try:
            km._timeline_sessions = lambda now, tmux, live_only=False: [
                {"sid": self.SID, "name": "web", "path": "/no/such/transcript-web"}]
            cached = km.build_timeline(built, live, with_bars=False)
            km._built_timeline[:] = [("sig",), cached, time.time(), time.time()]   # fresh by the kernel's own rule
            km._views_dirty[0] = 0.0
            km._skel_wire = km._bars_wire = None
            km._live_map = lambda: {}
            km._fleet_view_sig = lambda now, tmux: ("sig",)
            frames = []
            km._push([{"app": "timeline", "send": frames.append, "sent": {}, "alive": True}], connect=True)
        finally:
            km._timeline_sessions, km._live_map, km._fleet_view_sig = saved[0], saved[1], saved[2]
            km._built_timeline[:] = saved[3]
            km._views_dirty[0] = saved[4]
            km._skel_wire, km._bars_wire = saved[5], saved[6]
        data = [json.loads(f) for f in frames if json.loads(f)["type"] == "data"]
        self.assertEqual(len(data), 1, "the connect frame went")
        d = data[0]["data"]
        span = d["sessions"][0]["awaiting"][-1]
        self.assertGreater(d["now"] - span[1], 2, "the frame's clock is the cycle's, the open end the build's: the old tolerance read this closed")
        self.assertEqual(span, [built - 100, built, True], "the open mark rides the re-stamped frame")


class DeadLaneWindow(unittest.TestCase):
    """Dead lanes default to a 12h window and the FIRST cold paint reads no dead session at all (the user
    2026-06-26: "rarely looking at 48h"; "get the main UI up with the live sessions first, dead in background")."""

    def test_dead_lanes_limited_to_12h_live_only_drops_them_all(self):
        now = 1_000_000
        o_alive, o_sessions, o_ordered = km._alive_sessions, km._sessions, km._ordered
        km._alive_sessions = lambda now, tmux: [{"sid": "LIVE", "name": "l", "path": "/l", "mtime": now}]
        km._sessions = lambda now: [
            {"sid": "LIVE", "name": "l", "path": "/l", "mtime": now},
            {"sid": "RECENT", "name": "r", "path": "/r", "mtime": now - 6 * 3600},    # dead, within 12h
            {"sid": "OLD", "name": "o", "path": "/o", "mtime": now - 30 * 3600},      # dead, beyond 12h
        ]
        km._ordered = lambda lst: lst
        try:
            lanes = {s["sid"] for s in km._timeline_sessions(now, {})}
            self.assertEqual(lanes, {"LIVE", "RECENT"}, "a dead lane >12h old is dropped")
            live = {s["sid"] for s in km._timeline_sessions(now, {}, live_only=True)}
            self.assertEqual(live, {"LIVE"}, "live_only drops every dead lane (cold-start first paint)")
        finally:
            km._alive_sessions, km._sessions, km._ordered = o_alive, o_sessions, o_ordered

    def test_cold_connect_builds_live_only_and_wakes_the_producer_for_the_rest(self):
        calls, sent = [], []
        client = {"app": "timeline", "send": sent.append, "sent": {}, "alive": True}
        SK = {"type": "timeline", "sessions": [], "turns": {}, "judging": {}, "messages": [],
              "now": 1, "usage": {}}
        FB = {"type": "timeline", "sessions": [], "turns": {"S": []}, "judging": {},
              "messages": [], "now": 1}
        o_bt, o_tmux, o_sig = km.build_timeline, km._live_map, km._fleet_view_sig
        o_built = list(km._built_timeline)
        km.build_timeline = lambda now, tmux, with_bars=True, live_only=False: (
            calls.append(("bars" if with_bars else "skel", live_only)) or (FB if with_bars else SK))
        km._live_map = lambda: {}
        km._fleet_view_sig = lambda now, tmux: ("sig",)
        km._built_timeline[0], km._built_timeline[1], km._built_timeline[2] = None, None, 0.0   # cold cache
        km._producer_wake.clear()
        try:
            km._push([client], connect=True)
        finally:
            km.build_timeline, km._live_map, km._fleet_view_sig = o_bt, o_tmux, o_sig
            km._built_timeline[:] = o_built
        self.assertIn(("skel", True), calls, "cold connect: the lanes skeleton is built LIVE-ONLY")
        self.assertIn(("bars", True), calls, "cold connect: the bars are built LIVE-ONLY (no dead reads)")
        self.assertTrue(km._producer_wake.is_set(), "the full live+dead build is warmed in the background")


if __name__ == "__main__":
    unittest.main()
