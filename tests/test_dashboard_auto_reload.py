"""The dashboard reloads ITSELF on a kernel restart and on a newer served bundle (T265).

The user's ruling of 2026-09-08 supersedes their 2026-07-13 preference for a banner the reader clicks. The reload
core (kernel.py _RELOAD_CORE_JS, window.__rompReload on every kernel-served page) runs here for REAL: node executes
the IIFE between its anchors with fakes for document, window, location, sessionStorage and fetch, one process per
scenario (the core installs once per window), and the scenario reads its state back by name. Pinned alongside:
the wiring (the shim's raise, the shim's reconnect, the shell's socket, the stale banner's poll and fallback, the
pages that embed the core) and the remote exclusion (federation drops remote keepalives, so a REMOTE kernel's
restart or bundle never reaches the core). Synthetic values only."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_autoreload", os.path.join(BIN, "romp-kernel"))

# The browser the core thinks it runs in. `var` at module scope shadows node's globals; the core's own
# `document.addEventListener` calls land in LISTENERS so a scenario can emit the gesture events by name.
HARNESS = r"""
var LISTENERS = {}, STORE = {}, REMOVED = [], FETCHES = [], RELOADS = 0, REFUSE = false, PERSISTED = 0, WLISTENERS = {};
var SEL = { rangeCount: 0, isCollapsed: true, toString: function () { return ""; } };
var COMPOSER = { tagName: "TEXTAREA", value: "" };              // the chat composer, one editable among any
var FOCUSED = true;                                             // document.hasFocus()
var IFRAMES = [];
var document = {
  addEventListener: function (t, f) { (LISTENERS[t] = LISTENERS[t] || []).push(f); },
  getSelection: function () { return SEL; },
  hasFocus: function () { return FOCUSED; },
  getElementById: function (id) { return id === "composer-input" ? COMPOSER : null; },
  activeElement: null,
  body: { classList: { remove: function () { REMOVED.push(Array.prototype.slice.call(arguments)); } } },
  querySelectorAll: function () { return IFRAMES; }
};
var window = { addEventListener: function (t, f) { (WLISTENERS[t] = WLISTENERS[t] || []).push(f); } };
window.parent = window;
window.__rompPersistForReload = function () { PERSISTED++; };
var location = { pathname: "/", reload: function () { RELOADS++; if (REFUSE) throw new Error("host forbids reload"); } };
var sessionStorage = {
  setItem: function (k, v) { STORE[k] = v; }, getItem: function (k) { return k in STORE ? STORE[k] : null; },
  removeItem: function (k) { delete STORE[k]; }
};
var VERSION = null;
function fetch(u) { FETCHES.push(u); return Promise.resolve({ json: function () { return Promise.resolve(VERSION); } }); }
function emit(t) { (LISTENERS[t] || []).forEach(function (f) { f({}); }); }
function wemit(t) { (WLISTENERS[t] || []).forEach(function (f) { f({}); }); }
function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
function state() {
  var R = window.__rompReload;
  return { reloads: RELOADS, fired: R.fired(), owed: R.owed(), waiting: R.waiting, persisted: PERSISTED, removed: REMOVED, refusedFor: R.refusedFor(),
           stored: STORE["romp:reloaded"] ? JSON.parse(STORE["romp:reloaded"]) : null, fetches: FETCHES.length };
}
function out(o) { process.stdout.write("RESULT:" + JSON.stringify(o) + "\n"); }
"""


def run_core(scenario, v=7, boot="1.1"):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    d = tempfile.mkdtemp(prefix="reload-core-")
    path = os.path.join(d, "core.js")
    with open(path, "w") as f:
        f.write(HARNESS + km._reload_core_js(v, boot) + "\n(async function(){\n" + scenario + "\n})();\n")
    r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    shutil.rmtree(d, ignore_errors=True)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")), None)
    assert line, "no RESULT line:\n" + r.stdout
    return json.loads(line[len("RESULT:"):])


class ReloadCoreExecuted(unittest.TestCase):
    def test_a_newer_dv_reloads_at_once_when_idle_and_persists_first(self):
        s = run_core("""
var R = window.__rompReload;
R.noteDv(7); var same = state();          // the page's own build
R.noteDv(5); var older = state();         // an OLDER token (a rolled-back peer) is not drift
R.noteDv(8);
out({ same: same, older: older, after: state() });""")
        self.assertEqual(s["same"]["reloads"], 0)
        self.assertEqual(s["older"]["reloads"], 0)
        a = s["after"]
        self.assertEqual(a["reloads"], 1)
        self.assertTrue(a["fired"])
        self.assertEqual(a["stored"]["reason"], "build")
        self.assertEqual(a["stored"]["detail"], "8")
        self.assertEqual(a["stored"]["from"], 7)
        self.assertEqual(a["stored"]["path"], "/", "the marker names the page that reloaded")
        self.assertEqual(a["persisted"], 1, "the pane's persist hook ran before the reload")
        self.assertIn(["settings-open", "picker-open"], a["removed"], "the lifted modals close")

    def test_a_restart_is_a_reopen_against_a_new_boot_id_never_a_blip(self):
        s = run_core("""
var R = window.__rompReload;
VERSION = { boot: "1.1", dist_ver: 7 }; R.checkBoot(); await tick(); await tick(); var blip = state();
VERSION = { boot: "2.2", dist_ver: 7 }; R.checkBoot(); await tick(); await tick();
out({ blip: blip, after: state() });""")
        self.assertEqual(s["blip"]["reloads"], 0, "the same kernel answered: a socket blip, not a restart")
        self.assertEqual(s["blip"]["fetches"], 1)
        self.assertEqual(s["after"]["reloads"], 1)
        self.assertEqual(s["after"]["stored"]["reason"], "restart")
        self.assertEqual(s["after"]["stored"]["detail"], "2.2")

    def test_version_readings_feed_both_signals(self):
        s = run_core("""
var R = window.__rompReload;
R.noteVersion({ boot: "1.1", dist_ver: 7 }); var quiet = state();
R.noteVersion({ boot: "1.1", dist_ver: 9 });
out({ quiet: quiet, after: state() });""")
        self.assertEqual(s["quiet"]["reloads"], 0)
        self.assertEqual(s["after"]["stored"]["reason"], "build")

    def test_a_held_pointer_arms_and_the_pointerup_fires(self):
        s = run_core("""
var R = window.__rompReload;
emit("pointerdown");
R.noteDv(8); var held = state();
emit("pointerup"); var atOnce = state(); await tick();
out({ held: held, atOnce: atOnce, after: state() });""")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["held"]["waiting"], "pointer")
        self.assertEqual(s["held"]["owed"]["reason"], "build", "armed, not dropped")
        self.assertEqual(s["atOnce"]["reloads"], 0, "the fire waits one tick so the click the same press produces lands on its control first")
        self.assertEqual(s["after"]["reloads"], 1)

    def test_a_drag_in_flight_arms_and_dragend_fires(self):
        s = run_core("""
var R = window.__rompReload;
emit("dragstart"); R.noteDv(8); var held = state(); emit("dragend"); await tick();
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["waiting"], "drag")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["after"]["reloads"], 1)

    def test_a_selection_being_made_arms_and_its_collapse_fires(self):
        s = run_core("""
var R = window.__rompReload;
SEL = { rangeCount: 1, isCollapsed: false, toString: function () { return "some words"; } };
R.noteDv(8); var held = state();
SEL = { rangeCount: 1, isCollapsed: true, toString: function () { return ""; } }; emit("selectionchange"); await tick();
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["waiting"], "selection")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["after"]["reloads"], 1)

    def test_a_composer_with_text_and_focus_arms_and_emptying_or_blurring_fires(self):
        s = run_core("""
var R = window.__rompReload;
document.activeElement = COMPOSER; COMPOSER.value = "half a thought";
R.noteDv(8); var held = state();
COMPOSER.value = "half a thought, more"; emit("input"); await tick(); var typing = state();
COMPOSER.value = ""; emit("input"); await tick();
out({ held: held, typing: typing, after: state() });""")
        self.assertEqual(s["held"]["waiting"], "typing")
        self.assertEqual(s["typing"]["reloads"], 0, "typing keeps the hold")
        self.assertEqual(s["after"]["reloads"], 1, "an emptied composer releases it")
        s2 = run_core("""
var R = window.__rompReload;
document.activeElement = COMPOSER; COMPOSER.value = "draft"; R.noteDv(8);
document.activeElement = null; emit("focusout"); await tick();
out({ after: state() });""")
        self.assertEqual(s2["after"]["reloads"], 1, "a blurred composer releases it (the draft is persisted already)")

    def test_a_window_blur_releases_every_hold(self):
        s = run_core("""
var R = window.__rompReload;
emit("pointerdown"); emit("dragstart"); R.noteDv(8); var held = state(); wemit("blur"); await tick();
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["after"]["reloads"], 1)

    def test_a_refused_reload_falls_back_to_the_banner_hook(self):
        s = run_core("""
var R = window.__rompReload; var refused = [];
R.refused = function (o) { refused.push(o); }; REFUSE = true;
R.noteDv(8); var first = state();
emit("pointerup"); await tick(); emit("selectionchange"); await tick(); R.noteVersion({ boot: "1.1", dist_ver: 8 }); var again = state(); var shownAfterAgain = refused.length;
R.noteDv(9); var newer = state();
out({ refused: refused, first: first, again: again, shownAfterAgain: shownAfterAgain, newer: newer });""")
        f = s["first"]
        self.assertEqual(f["reloads"], 1, "the reload was attempted")
        self.assertFalse(f["fired"], "…and stood down when the host threw")
        self.assertEqual(f["waiting"], "refused")
        self.assertEqual(f["refusedFor"], "build:8", "the refusal latches for this build")
        self.assertEqual(f["stored"], None, "no marker and no un-lifted modal for a reload that never happened")
        self.assertEqual(f["removed"], [])
        self.assertEqual(s["refused"][0], {"reason": "build", "detail": "8"})
        self.assertEqual(s["again"]["reloads"], 1, "gesture ends and the poll do not re-attempt the refused build")
        self.assertEqual(s["shownAfterAgain"], 1, "the banner is shown once for that build")
        self.assertEqual(s["newer"]["reloads"], 2, "a strictly newer build re-arms and tries again (and is refused again on this host)")
        self.assertEqual(len(s["refused"]), 2)

    def test_the_fresh_page_announces_once_from_the_marker(self):
        s = run_core("""
var R = window.__rompReload; var notes = [];
STORE["romp:reloaded"] = JSON.stringify({ reason: "restart", detail: "2.2", from: 6, t: 1 });
var first = R.announce(function (k, t) { notes.push([k, t]); });
var second = R.announce(function (k, t) { notes.push([k, t]); });
out({ first: first, second: second, notes: notes, left: STORE["romp:reloaded"] || null });""")
        self.assertEqual(s["first"], "Reloaded onto build 7 — the kernel restarted.")
        self.assertEqual(s["notes"], [["reload", "Reloaded onto build 7 — the kernel restarted."]])
        self.assertIsNone(s["second"], "one line per reload")
        self.assertIsNone(s["left"], "the marker is consumed")
        s2 = run_core("""
var R = window.__rompReload;
STORE["romp:reloaded"] = JSON.stringify({ reason: "build", detail: "9", from: 6, t: 1 });
out({ first: R.announce(null) });""")
        self.assertEqual(s2["first"], "Reloaded onto build 7 — a newer romp build was served.")
        s3 = run_core("""
var R = window.__rompReload;
STORE["romp:reloaded"] = JSON.stringify({ reason: "build", detail: "9", from: 6, path: "/feed", t: 1 });
out({ first: R.announce(null), left: STORE["romp:reloaded"] || null });""")
        self.assertIsNone(s3["first"], "a marker another page wrote (a standalone /feed reload) is not this page's to announce")
        self.assertIsNotNone(s3["left"], "…and it is LEFT for the page it names (T272, 2026-09-08): sessionStorage is shared across the shell "
                                         "and its same-origin panes, and a pane's shim that read as standalone for a beat consumed the "
                                         "shell's marker before the path check — the shell then found nothing to announce, and the "
                                         "notification-center line the served test waits for never appeared")
        # the shell's marker (path "/") survives a pane's early announce and is announced by the shell itself, once
        s4 = run_core("""
var R = window.__rompReload; var notes = [];
STORE["romp:reloaded"] = JSON.stringify({ reason: "restart", detail: "2.2", from: 6, path: "/", t: 1 });
location.pathname = "/chat"; var pane = R.announce(null);
location.pathname = "/"; var shell = R.announce(function (k, t) { notes.push([k, t]); }); var again = R.announce(null);
out({ pane: pane, shell: shell, again: again, notes: notes, left: STORE["romp:reloaded"] || null });""")
        self.assertIsNone(s4["pane"], "the chat pane leaves the shell's marker alone")
        self.assertEqual(s4["shell"], "Reloaded onto build 7 — the kernel restarted.", "the shell announces its own reload")
        self.assertEqual(s4["notes"], [["reload", "Reloaded onto build 7 — the kernel restarted."]])
        self.assertIsNone(s4["again"], "one line per reload"); self.assertIsNone(s4["left"], "consumed by its own page")

    def test_the_shell_composes_gesture_state_across_its_panes_and_a_pane_forwards_its_request(self):
        s = run_core("""
var R = window.__rompReload;
var paneBusy = "pointer";
var pane = { __rompReload: { busyHere: function () { return paneBusy; } }, __rompPersistForReload: function () { PERSISTED += 10; } };
IFRAMES = [{ contentWindow: pane }];
R.request("build", "8"); var held = state();
paneBusy = ""; R.tryFire();            // the pane's ending event calls the shell's tryFire
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["held"]["waiting"], "pointer", "a pane's held pointer holds the shell's reload")
        self.assertEqual(s["after"]["reloads"], 1)
        self.assertEqual(s["after"]["persisted"], 11, "the shell and every pane persisted before the reload")
        # a pane under a same-origin shell forwards: its own core never fires
        s2 = run_core("""
var shellReqs = [];
window.parent = { __rompReload: { request: function (r, d) { shellReqs.push([r, d]); }, tryFire: function () { shellReqs.push(["tryFire"]); } } };
var R = window.__rompReload;
R.noteDv(8); emit("pointerup"); await tick();
out({ shellReqs: shellReqs, inShell: R.inShell(), after: state() });""")
        self.assertTrue(s2["inShell"])
        self.assertEqual(s2["shellReqs"], [["build", "8"], ["tryFire"]])
        self.assertEqual(s2["after"]["reloads"], 0, "the top document reloads, never the pane alone")

    def test_a_panes_queued_sends_hold_the_reload_until_its_flush(self):
        s = run_core("""
var R = window.__rompReload; var queued = 1;
window.__rompPaneBusy = function () { return queued ? "sends" : ""; };   // the shim: everConnected && queue.length > queuedDiag
R.noteVersion({ boot: "2.2" }); var held = state();
queued = 0; R.ended(); await tick();                                    // the shim's ws.onopen flush
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["reloads"], 0, "a prompt typed during the outage sits in the pane's queue: the shell's earlier reopen must not take the page down")
        self.assertEqual(s["held"]["waiting"], "sends")
        self.assertEqual(s["after"]["reloads"], 1, "the flush is the ending event")

    def test_a_held_reload_wears_a_face_once_per_hold_and_never_for_a_gesture(self):
        # T272 follow-up (the manager's review): nothing displayed the core's `waiting`, so a reload held by a pane's
        # reason (an upload in flight) sat invisible. The `held` hook fires once per owed request and reason — the shell
        # files a notification-center line, a standalone pane raises its bar — and never for a momentary gesture hold.
        s = run_core("""
var R = window.__rompReload; var held = []; R.held = function (b, o) { held.push([b, o && o.reason]); };
var busyReason = "upload";
window.__rompPaneBusy = function () { return busyReason; };
R.noteVersion({ boot: "2.2" });                       // owed, held on the pane's upload
R.ended(); await tick(); R.ended(); await tick();     // re-asks while the same hold stands: no second line
busyReason = "held-send"; R.ended(); await tick();    // the reason changed: one line for it
busyReason = ""; R.ended(); await tick();             // idle: fires
out({ held: held, reloads: RELOADS, waiting: R.waiting });""")
        self.assertEqual(s["held"], [["upload", "restart"], ["held-send", "restart"]], "once per hold reason, with the owed request")
        self.assertEqual(s["reloads"], 1)
        s2 = run_core("""
var R = window.__rompReload; var held = []; R.held = function (b) { held.push(b); };
emit("pointerdown"); R.noteVersion({ boot: "2.2" });   // a held pointer: a gesture hold, no line
var during = state();
emit("pointerup"); await tick();
out({ held: held, during: during, reloads: RELOADS });""")
        self.assertEqual(s2["during"]["waiting"], "pointer")
        self.assertEqual(s2["held"], ["pointer"], "the hook is told every hold; the shell's line filters gestures out (heldReloadText)")
        self.assertEqual(s2["reloads"], 1)

    def test_a_touch_pan_holds_from_pointercancel_until_the_finger_lifts(self):
        s = run_core("""
var R = window.__rompReload;
emit("pointerdown"); emit("pointercancel");       // the touch became a scroll: the browser cancels the pointer, the finger is still down
R.noteDv(8); var panning = state();
emit("pointerup"); await tick(); var stillPanning = state();   // no pointerup comes for a cancelled pointer, but even one must not release the pan
emit("touchend"); await tick();
out({ panning: panning, stillPanning: stillPanning, after: state() });""")
        self.assertEqual(s["panning"]["reloads"], 0)
        self.assertEqual(s["panning"]["waiting"], "pan")
        self.assertEqual(s["stillPanning"]["reloads"], 0)
        self.assertEqual(s["after"]["reloads"], 1, "touchend releases the pan")

    def test_a_selection_counts_only_in_the_focused_document(self):
        s = run_core("""
var R = window.__rompReload;
SEL = { rangeCount: 1, isCollapsed: false, toString: function () { return "an old highlight"; } };
FOCUSED = false; R.noteDv(8);
out({ after: state() });""")
        self.assertEqual(s["after"]["reloads"], 1, "a highlight left in a pane the user is not in is not a gesture being made")

    def test_typing_in_any_editable_holds_not_only_the_composer(self):
        s = run_core("""
var R = window.__rompReload;
var pickerInput = { tagName: "INPUT", type: "text", value: "new-sess" };
document.activeElement = pickerInput; R.noteDv(8); var held = state();
var sel = { tagName: "SELECT", value: "x" }; document.activeElement = sel; emit("focusout"); await tick();
out({ held: held, after: state() });""")
        self.assertEqual(s["held"]["waiting"], "typing", "the new-session picker's name box holds like the composer")
        self.assertEqual(s["held"]["reloads"], 0)
        self.assertEqual(s["after"]["reloads"], 1, "a focused <select> is not text entry")

    def test_a_foreign_parent_leaves_the_pane_to_reload_itself(self):
        s = run_core("""
Object.defineProperty(window, "parent", { get: function () { throw new Error("cross-origin"); } });
var R = window.__rompReload; R.noteDv(8);
out({ inShell: R.inShell(), after: state() });""")
        self.assertFalse(s["inShell"])
        self.assertEqual(s["after"]["reloads"], 1, "an iframe in another app reloads itself")


class ReloadWiringPinned(unittest.TestCase):
    def test_every_kernel_served_page_embeds_the_core_with_its_build_and_boot(self):
        shim = km._shim("feed", 123)
        self.assertIn("/*reload-core*/", shim)
        self.assertLess(shim.find("/*reload-core*/"), shim.find("/*shim-core*/"), "the core is defined before the shim asks it")
        self.assertIn("var LOADED=123,BOOT=%s," % json.dumps(km._BOOT_ID), shim)
        self.assertIn("/*reload-core*/", km._stale_block(123))
        self.assertIn("var LOADED=123,BOOT=%s," % json.dumps(km._BOOT_ID), km._stale_block(123))
        self.assertLess(km._stale_block(123).find("/*reload-core*/"), km._stale_block(123).find("var RL=window.__rompReload;"))
        self.assertIn("var LOADED=0,", km._reload_core())
        with self.assertRaises(RuntimeError):
            km._RELOAD_CORE_JS = km._RELOAD_CORE_JS.replace("/*end-reload-core*/", "", 1)
            try:
                km._reload_core_js()
            finally:
                km._RELOAD_CORE_JS = km._RELOAD_CORE_JS.replace("window.__rompReload=R;})();", "window.__rompReload=R;})();/*end-reload-core*/", 1)

    def test_the_shim_asks_the_core_on_build_drift_and_on_a_standalone_reconnect(self):
        js = km._shim("chat", 5)
        self.assertIn('function raiseBuild(){if(buildRaised)return;buildRaised=true;var R=window.__rompReload;\n'
                      'if(R){R.refused=function(){selfBar("A newer romp build is available.","build");};R.request("build","");}\n'
                      'else selfBar("A newer romp build is available.","build");}', js)
        self.assertNotIn('postMessage({romp:"wsStale",build:1}', js, "the build:1 hand-off to the banner is gone")
        self.assertIn('if(msg&&msg.type==="ka"){if(LOADEDV&&msg.dv&&msg.dv>LOADEDV)raiseBuild();', js, "the keepalive's dv is still the event")
        self.assertIn("if(window.__rompReload&&!window.__rompReload.inShell())window.__rompReload.checkBoot();", js)
        # the pane's queued sends hold the reload, and the flush is the ending event (review find, 2026-09-08)
        self.assertIn('window.__rompPaneBusy=function(){return (everConnected&&queue.length>queuedDiag)?"sends":"";};', js)
        self.assertIn("queue=[];queuedDiag=0;\ntry{if(window.__rompReload)window.__rompReload.ended();}catch(e){}", js)
        # a standalone page consumes its own marker; nobody else would
        self.assertIn("try{if(window.__rompReload&&!window.__rompReload.inShell())window.__rompReload.announce(null);}catch(e){}", js)
        # …on the RECONNECT branch only: the first open is not a restart
        i = js.find("if(wasReconn){")
        self.assertGreater(i, 0)
        self.assertLess(i, js.find("window.__rompReload.checkBoot();"))

    def test_the_shell_asks_on_its_own_sockets_reopen_and_feeds_its_keepalive(self):
        js = km._LANDING_MOBILE_JS
        self.assertIn("var shellOpened=false;", js)
        self.assertIn("if(shellOpened&&window.__rompReload)window.__rompReload.checkBoot();shellOpened=true;", js)
        self.assertIn("if(m&&m.type==='ka'){if(m.dv&&window.__rompReload)window.__rompReload.noteDv(m.dv);}", js)

    def test_the_banner_is_the_refused_fallback_and_the_poll_feeds_the_core(self):
        js = km._STALE_JS
        self.assertIn("if(RL){RL.refused=function(){buildStale=true;show(BUILDMSG);};", js)
        self.assertIn("RL.announce(function(k,t){if(window.__rompNotify)window.__rompNotify(k,t);});}", js)
        self.assertIn("if(RL)RL.noteVersion(v);", js)
        self.assertIn("if(loaded&&served>loaded&&served!==dismissed){if(!RL)show(BUILDMSG);}", js)
        self.assertIn("if(m.build){if(RL)RL.request('build','');else{buildStale=true;show(BUILDMSG);}}", js)
        self.assertIn("if(m&&m.romp==='wsFresh'){connStale=false;if(buildStale)show(BUILDMSG);else box.classList.remove('show');}", js,
                      "the CONNECTION prompt for a plain reconnect is untouched")

    def test_a_held_reload_is_told_to_the_notification_center_and_to_a_standalone_panes_bar(self):
        # the shell's stale script installs the `held` hook: the line names what the reload waits for (an upload, a held
        # send, queued sends) and skips momentary gesture holds; a standalone pane (no shell) raises its own bar
        js = km._STALE_JS
        self.assertIn("RL.held=function(b){var t=(b==='upload'?'The dashboard will reload once the upload in progress finishes.'", js)
        self.assertIn("if(t&&window.__rompNotify)window.__rompNotify('reload',t);", js)
        shim = km._shim("chat", 7) if callable(getattr(km, "_shim", None)) else ""
        self.assertIn("window.__rompReload.held=function(b){var t=(b==='upload'?", shim)
        self.assertIn("if(t)selfBar(t,'held');", shim)
        self.assertIn("!window.__rompReload.inShell()", shim, "only a standalone pane raises its own bar; in the shell the center speaks")

    def test_a_remote_kernels_restart_or_bundle_never_reaches_the_core(self):
        fed = open(os.path.join(ROOT, "ui", "webview", "federation.ts")).read()
        self.assertIn('if (msg && msg.type === "ka") return;', fed, "federation drops a remote kernel's keepalive")
        core = km._reload_core_js()
        self.assertNotIn("__rompLocalSend", core)
        self.assertNotIn("host", core, "the core knows no hosts: it reads THIS page's socket and /version only")
        self.assertIn("fetch('/version'", core, "…and /version is the serving kernel's own")

    def test_the_superseded_rule_is_recorded_with_both_dates(self):
        src = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        block = src[src.index("# ── the dashboard reloads ITSELF"):src.index("_RELOAD_CORE_JS = r")]
        self.assertIn("2026-09-08", block); self.assertIn("2026-07-13", block); self.assertIn("supersedes", block)
        ext = open(os.path.join(ROOT, "vscode-extension", "src", "extension.ts")).read()
        self.assertIn("2026-09-08", ext, "the VS Code exception names the ruling it stands beside")


if __name__ == "__main__":
    unittest.main()
