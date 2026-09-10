#!/usr/bin/env python3
"""The shell tells its panes which panes are on screen, and relays a file click into the Files pane.

A file link clicked in the chat while the Files pane is open must open there whatever the "File links
open in" setting says (the pane being open is the intent), and the chat has no way to see the pane on
its own. The shell owns pane state (_LANDING_COLLAPSE_JS: the po object, apply(), __rompPaneToggle), so
the shell broadcasts it: {romp:'panes',on:{key:bool}} into every pane iframe on every apply(), the
exact event of the set changing (a toggle, the boot apply, another tab's storage event), again on each
iframe's own load, so a pane that boots or reloads after the shell hears the current set, and from the
mobile script on a tab switch or a layout flip. On a phone "on" means the tab showing, not the po flag,
so a po.files left true by a desktop session cannot steer a phone's file links into a tab nobody is
looking at. The chat caches the set and routes by it (render.ts panesOn; ui/webview/file-route.ts).

The three shell scripts involved are the kernel's own inline JavaScript, so they run here under node
against a fake window and document, and the messages they post are read back: the collapse controller,
the mobile script (__rompMobileOn, __rompMobileTab, the re-tell, and the switches the person makes, a tab
tap, a reveal, the chat header's Outline pill, which drop the relay's remembered tab) and the settings
listener's arms (viewFile with pane:'pane', filesViewerClosed, and the browseFiles arm the feed's browser
keeps; that arm's Files-pane branch, a browseFiles with pane:'pane', runs in tests/test_files_pane.py
BrowseRelay).
Synthetic only: placeholder sids, the notes-api demo world, TESTHOST.
"""
import json
import os
import subprocess
import tempfile
import unittest

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_psb", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


def _run(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    assert r.returncode == 0, "the shell script threw: " + r.stderr[:1200]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── the pane controller ───────────────────────────────────────────────────────────────────────────
# One iframe per pane key; the desktop shell (no rail buttons: querySelectorAll is empty), the feed and
# chat and timeline on, the Outline and the Files pane off, the way the served body class starts.
_COLLAPSE_HARNESS = r"""
'use strict';
const POSTED = {}, LOADS = {}, CLS = new Set(['po-chat', 'po-feed', 'po-timeline']), STORE = {};
const KEYS = __KEYS__;
const frames = {};
KEYS.forEach((k) => { frames['f-' + k] = {
  contentWindow: { postMessage: (m) => { (POSTED[k] = POSTED[k] || []).push(JSON.parse(JSON.stringify(m))); } },
  addEventListener: (ev, f) => { if (ev === 'load') (LOADS[k] = LOADS[k] || []).push(f); } }; });
let TAB = 'chat', MOBILE = false;
global.window = global;
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = v; } };
global.location = { search: '' };
global.URLSearchParams = class { get() { return null; } };
global.Event = class { constructor(t) { this.type = t; } };
global.addEventListener = () => {};
global.dispatchEvent = () => true;
global.document = {
  body: { classList: { toggle: (c, on) => { if (on) CLS.add(c); else CLS.delete(c); }, contains: (c) => CLS.has(c) },
          getAttribute: (a) => (a === 'data-tab' ? TAB : null) },
  querySelectorAll: () => [],
  getElementById: (id) => frames[id] || null,
};
window.__rompMobileOn = () => MOBILE;
"""
_COLLAPSE_DRIVER = r"""
const counts = () => Object.fromEntries(KEYS.map((k) => [k, (POSTED[k] || []).length]));
const last = (k) => (POSTED[k] || []).slice(-1)[0];
const out = {};
out.boot = { counts: counts(), chat: last('chat'), files: last('files') };
window.__rompPaneToggle('files', true);
out.on = { counts: counts(), chat: last('chat'), cls: CLS.has('po-files'), store: JSON.parse(STORE['romp-panes'] || 'null') };
window.__rompPaneToggle('files', true);        // already so: no change, so no message claiming one
out.same = { counts: counts() };
(LOADS.chat || []).forEach((f) => f());        // the chat iframe reloads: it hears the current set again
out.reload = { counts: counts(), chat: last('chat') };
MOBILE = true; TAB = 'chat'; window.__rompPanesTell();
out.phoneChat = last('chat');
TAB = 'files'; window.__rompPanesTell();
out.phoneFiles = last('files');
MOBILE = false;
window.__rompPaneToggle('files');              // no `to`: flips it off
out.off = { chat: last('chat'), cls: CLS.has('po-files'), store: JSON.parse(STORE['romp-panes'] || 'null') };
out.teller = typeof window.__rompPanesTell;
console.log(JSON.stringify(out));
"""


# ── the Files control hidden by its gear setting (T317) ─────────────────────────────────────────────────
# The store holds romp:settings.filesControl false and the Files pane is ON (a desktop session left it open):
# the boot apply hides the control (body.no-files-control), closes the pane and saves that, tells the panes the
# pane is unavailable (avail.files false), and refuses to bring it forward; a phone left on the Files tab is
# switched to the chat. Flipping the store back on (the gear's write, heard through the storage listener) shows
# the control again and the pane can open.
_HIDDEN_DRIVER = r"""
const last = (k) => (POSTED[k] || []).slice(-1)[0];
const out = {};
out.boot = { cls: CLS.has('no-files-control'), poFiles: CLS.has('po-files'), store: JSON.parse(STORE['romp-panes'] || 'null'), chat: last('chat') };
window.__rompPaneToggle('files', true);          // a relay's bring-forward, the palette's command: refused
out.refused = { poFiles: CLS.has('po-files'), chat: last('chat') };
MOBILE = true; TAB = 'files'; SWITCHED.length = 0; window.__rompPaneToggle('feed');   // any apply (here a feed flip) on a phone left on the Files tab
out.phone = { switched: SWITCHED.slice() };
MOBILE = false;
STORE['romp:settings'] = JSON.stringify({ filesControl: true }); STORAGE_LISTENERS.forEach((f) => f());   // the gear turns it back on
out.shown = { cls: CLS.has('no-files-control'), chat: last('chat') };
window.__rompPaneToggle('files', true);
out.reopened = { poFiles: CLS.has('po-files'), chat: last('chat') };
console.log(JSON.stringify(out));
"""


class HiddenControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = [k for k, _ in km._PANE_ORDER]
        harness = (_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(keys))
                   .replace("const POSTED = {}, LOADS = {}, CLS = new Set(['po-chat', 'po-feed', 'po-timeline']), STORE = {};",
                            "const POSTED = {}, LOADS = {}, CLS = new Set(['po-chat', 'po-feed', 'po-timeline']), STORE = {"
                            "'romp:settings': JSON.stringify({ filesControl: false }), 'romp-panes': JSON.stringify({ chat: true, fleet: false, feed: true, timeline: true, files: true }) };"
                            "const STORAGE_LISTENERS = [], SWITCHED = [];")
                   .replace("global.addEventListener = () => {};", "global.addEventListener = (ev, f) => { if (ev === 'storage') STORAGE_LISTENERS.push(f); };")
                   + "window.__rompMobileTab = (p) => { SWITCHED.push(p); TAB = p; };\n")
        cls.out = _run(harness + km._LANDING_COLLAPSE_JS + _HIDDEN_DRIVER)

    def test_the_boot_apply_hides_the_control_and_closes_the_open_pane(self):
        b = self.out["boot"]
        self.assertTrue(b["cls"], "body.no-files-control: the rail's toggle and the phone's tab are hidden by CSS")
        self.assertFalse(b["poFiles"], "the pane a desktop session left open closes on the same apply")
        self.assertEqual(b["store"]["files"], False, "…and the close is saved, so a reload stays closed")
        self.assertEqual(b["chat"]["avail"], {"files": False}, "the panes are told the pane is unavailable: file links open over the pane clicked")
        self.assertEqual(b["chat"]["on"]["files"], False)

    def test_the_pane_cannot_be_brought_forward_while_the_control_is_hidden(self):
        r = self.out["refused"]
        self.assertFalse(r["poFiles"], "a relay's bring-forward or the palette's command is refused")
        self.assertEqual(r["chat"]["avail"], {"files": False})

    def test_a_phone_left_on_the_files_tab_is_switched_to_the_chat(self):
        self.assertEqual(self.out["phone"]["switched"], ["chat"])

    def test_the_gears_write_shows_the_control_again_and_the_pane_can_open(self):
        s = self.out["shown"]
        self.assertFalse(s["cls"], "the storage event re-applies: the control shows")
        self.assertEqual(s["chat"]["avail"], {"files": True})
        r = self.out["reopened"]
        self.assertTrue(r["poFiles"], "and the pane opens again")
        self.assertEqual(r["chat"]["on"]["files"], True)


class Broadcast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = [k for k, _ in km._PANE_ORDER]
        cls.out = _run(_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(cls.keys)) + km._LANDING_COLLAPSE_JS + _COLLAPSE_DRIVER)

    def test_the_boot_apply_tells_every_pane_the_set_as_the_flags_stand(self):
        self.assertEqual(self.out["boot"]["counts"], {k: 1 for k in self.keys}, "one message per pane at boot")
        self.assertEqual(self.out["boot"]["chat"], {"romp": "panes", "on": {"chat": True, "timeline": True, "fleet": False, "feed": True, "files": False},
                                                     "avail": {"files": True}})   # avail: the Files control's setting rides every tell (T317)
        self.assertEqual(self.out["boot"]["files"], self.out["boot"]["chat"], "every pane hears the same set")

    def test_a_toggle_is_the_event_and_a_no_change_toggle_is_silent(self):
        self.assertEqual(self.out["on"]["counts"], {k: 2 for k in self.keys})
        self.assertTrue(self.out["on"]["chat"]["on"]["files"], "the Files pane is on after the toggle")
        self.assertTrue(self.out["on"]["cls"], "and the body class the CSS shows the column by")
        self.assertEqual((self.out["on"]["store"] or {}).get("files"), True, "and the set persists")
        # a repeated bring-forward (the viewFile relay on an already-open pane) changes nothing, so it says
        # nothing: a message is a claim that something changed, and the panes must be able to trust it
        self.assertEqual(self.out["same"]["counts"], {k: 2 for k in self.keys})

    def test_a_pane_that_loads_after_the_shell_hears_the_set(self):
        self.assertEqual(self.out["reload"]["counts"]["chat"], 3, "the chat's load handler re-tells it")
        self.assertEqual(self.out["reload"]["counts"]["files"], 2, "and nobody else")
        self.assertEqual(self.out["reload"]["chat"]["on"]["files"], True, "with the current set")

    def test_on_a_phone_on_means_the_tab_showing_not_the_flag(self):
        # po.files is true here (the toggle above), yet the phone on the chat tab reports it off
        self.assertEqual(self.out["phoneChat"]["on"], {"chat": True, "timeline": False, "fleet": False, "feed": False, "files": False})
        self.assertEqual(self.out["phoneFiles"]["on"], {"chat": False, "timeline": False, "fleet": False, "feed": False, "files": True})

    def test_a_toggle_off_reports_the_pane_off(self):
        self.assertFalse(self.out["off"]["chat"]["on"]["files"])
        self.assertFalse(self.out["off"]["cls"])
        self.assertEqual((self.out["off"]["store"] or {}).get("files"), False)

    def test_the_mobile_script_can_re_tell(self):
        self.assertEqual(self.out["teller"], "function", "window.__rompPanesTell is the mobile script's hook")

    def test_the_key_set_is_the_one_pane_list(self):
        # derived from _PANE_ORDER, never a second hand-written list: a pane added there is broadcast
        js = km._LANDING_COLLAPSE_JS
        self.assertIn("var KEYS=" + json.dumps(self.keys) + ";", js)
        self.assertIn('var KEYS=""" + json.dumps([k for k, _ in _PANE_ORDER]) + """;', open(os.path.join(BIN, "romp-kernel")).read())
        self.assertEqual(len(self.keys), 5)
        # every key ships an iframe by the id the broadcast addresses, or a pane is silently never told
        html = km._landing()
        for k in self.keys:
            self.assertIn("<iframe id=f-%s " % k, html, "no iframe for pane key %r" % k)


# ── the mobile script ─────────────────────────────────────────────────────────────────────────────
_MOBILE_HARNESS = r"""
'use strict';
const TOGGLES = [], TELLS = [], MQL = [], MSGS = [], STORE = {};
let MATCHES = true, TAB = null;
global.window = global;
global.innerHeight = 844; global.innerWidth = 390; global.scrollY = 0;
global.scrollTo = () => {};
global.matchMedia = (q) => ({ get matches() { return MATCHES; }, query: q, addEventListener: (ev, f) => { if (ev === 'change') MQL.push(f); } });   // matches reads live, as a MediaQueryList's does
global.requestAnimationFrame = (f) => 1;
global.addEventListener = (ev, f) => { if (ev === 'message') MSGS.push(f); };   // the script's window-message arms (reveal, toggleFleet), driven below
global.visualViewport = { height: 844, scale: 1, addEventListener: () => {} };
global.WebSocket = class { constructor() { this.readyState = 0; } send() {} };
global.encodeURIComponent = (s) => s;
global.location = { protocol: 'http:', host: 'TESTHOST:1' };
global.sessionStorage = { getItem: () => 'wid1' };
global.setTimeout = () => 0;
const pane = (id) => ({ id, classList: { toggle: (c, on) => { TOGGLES.push([id, c, !!on]); } }, contentDocument: {},
  contentWindow: { addEventListener: () => {} }, addEventListener: () => {} });
const PANES = {};
['f-chat', 'f-fleet', 'f-feed', 'f-files', 'f-timeline'].forEach((id) => { PANES[id] = pane(id); });
const TAPS = {};
const button = (key) => ({ getAttribute: (a) => (a === 'data-pane' ? key : null), classList: { toggle() {} },
  addEventListener: (ev, f) => { if (ev === 'click') TAPS[key] = f; } });
const BAR = { offsetHeight: 44, querySelectorAll: (sel) => (sel === 'button[data-pane]' ? [button('chat'), button('feed'), button('files')] : []) };
global.document = {
  visibilityState: 'visible',
  addEventListener: () => {},
  documentElement: { scrollTop: 0, style: { setProperty() {} } },
  body: { setAttribute: (a, v) => { if (a === 'data-tab') TAB = v; }, getAttribute: (a) => (a === 'data-tab' ? TAB : null) },
  getElementById: (id) => (id === 'mtabs' ? BAR : (PANES[id] || null)),
};
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = v; } };
window.__rompPanesTell = () => TELLS.push(TAB);
"""
_MOBILE_DRIVER = r"""
const out = {};
out.boot = { tab: TAB, on: window.__rompMobileOn(), tells: TELLS.slice() };
TOGGLES.length = 0; TELLS.length = 0;
window.__rompMobileTab('files');
out.files = { tab: TAB, mOn: TOGGLES.filter((t) => t[1] === 'm-on' && t[2]).map((t) => t[0]), tells: TELLS.slice(), store: STORE['romp-mobile-tab'] };
TELLS.length = 0;
MQL.forEach((f) => f({}));                                 // the layout flips (a rotation across the breakpoint)
out.flip = { tells: TELLS.slice(), listeners: MQL.length };
MATCHES = false;
out.desktop = window.__rompMobileOn();
window.__rompMobileTab('nowhere');
out.unknown = { tab: TAB };
// the relay remembered a tab; the person's own tap on another tab drops that memory, the relay's own switch keeps it
window.__rompFilesTabFrom = 'chat'; TAPS.feed();
out.tap = { tab: TAB, from: window.__rompFilesTabFrom };
window.__rompFilesTabFrom = 'chat'; window.__rompMobileTab('files');
out.relaySwitch = { tab: TAB, from: window.__rompFilesTabFrom };
// a reveal aimed at the person (a feed card's tap into a session, the kernel's reveal) and the chat header's
// Outline pill (toggleFleet) arrive as window messages and are switches they made too
const arrive = (m) => MSGS.forEach((f) => f({ data: m }));
window.__rompFilesTabFrom = 'chat'; arrive({ romp: 'reveal', pane: 'feed' });
out.reveal = { tab: TAB, from: window.__rompFilesTabFrom, listeners: MSGS.length };
window.__rompFilesTabFrom = 'chat'; arrive({ romp: 'toggleFleet', to: 'chat' });
out.pill = { tab: TAB, from: window.__rompFilesTabFrom };
window.__rompFilesTabFrom = 'chat'; arrive({ romp: 'toggleFleet' });
out.pillOutline = { tab: TAB, from: window.__rompFilesTabFrom };
console.log(JSON.stringify(out));
"""


class MobileScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run(_MOBILE_HARNESS + km._LANDING_MOBILE_JS + _MOBILE_DRIVER)

    def test_the_layout_probe_answers_the_stylesheets_own_media_query(self):
        # one constant lays out the grid AND answers the JS, never two strings that can drift
        self.assertIn("@media " + km._MOBILE_MQ + "{", km._landing())
        self.assertIn("matchMedia(" + json.dumps(km._MOBILE_MQ) + ")", km._LANDING_MOBILE_JS)
        self.assertTrue(self.out["boot"]["on"], "the fake matchMedia matches: the phone layout")
        self.assertFalse(self.out["desktop"], "and reads live: unmatched is the desktop")
        # defined ABOVE the tab-bar lookup, so a desktop shell (no bar) still answers
        js = km._LANDING_MOBILE_JS
        self.assertLess(js.index("window.__rompMobileOn=mobileOn;"), js.index("\nvar bar=document.getElementById('mtabs');if(!bar)return;\n"))

    def test_the_boot_show_lands_on_the_chat_and_the_tab_switch_re_tells_the_panes(self):
        self.assertEqual(self.out["boot"]["tab"], "chat")
        self.assertEqual(self.out["boot"]["tells"], ["chat"], "the boot show re-tells too (a no-op before the collapse script parses)")
        self.assertEqual(self.out["files"]["tab"], "files")
        self.assertEqual(self.out["files"]["mOn"], ["f-files"], "exactly one pane wears m-on")
        self.assertEqual(self.out["files"]["tells"], ["files"], "the switch is the event: re-told once, after data-tab moved")
        self.assertEqual(self.out["files"]["store"], "files")
        self.assertEqual(self.out["unknown"]["tab"], "files", "a key the shell has no pane for switches nothing")

    def test_a_layout_flip_re_tells_the_panes(self):
        self.assertEqual(self.out["flip"]["listeners"], 1, "one change listener on the media query")
        self.assertEqual(self.out["flip"]["tells"], ["files"])

    def test_a_tab_tap_drops_the_relays_remembered_tab_and_the_relays_own_switch_keeps_it(self):
        # phone: the relay remembered 'chat' and switched to Files; the person taps Feed, works there, comes back
        # to Files and closes the file. Without this the close would jump them to Chat, a tab they left on their own.
        self.assertEqual(self.out["tap"], {"tab": "feed", "from": None})
        self.assertEqual(self.out["relaySwitch"], {"tab": "files", "from": "chat"}, "__rompMobileTab itself is the relay's path and keeps the memory")

    def test_a_reveal_and_the_chat_headers_outline_pill_drop_the_remembered_tab_too(self):
        # the same rule as the tap: the relay's memory serves the file's close only while the person has not
        # moved on their own; a reveal (the feed's tap into a session) and the header pill are their moves
        self.assertEqual(self.out["reveal"], {"tab": "feed", "from": None, "listeners": 1})
        self.assertEqual(self.out["pill"], {"tab": "chat", "from": None})
        self.assertEqual(self.out["pillOutline"], {"tab": "fleet", "from": None})

    def test_the_hook_is_exported_for_the_relay(self):
        self.assertIn("window.__rompMobileTab=show;", km._LANDING_MOBILE_JS)
        # the mobile script parses BEFORE the collapse script (its boot show() finds no teller yet; the boot
        # apply that follows tells the panes), and the relay's tab switch happens at message time, after both
        html = km._landing()
        self.assertLess(html.index("window.__rompMobileTab=show;"), html.index("window.__rompPanesTell=broadcast;"))


# ── the settings listener's arms ──────────────────────────────────────────────────────────────────
# The whole settings script runs (it registers the one message listener this drives); the rest of it
# finds no elements and does nothing. `mobile` answers __rompMobileOn; `tab` is the tab showing.
_ARMS_HARNESS = r"""
'use strict';
const LISTENERS = [], TOGGLES = [], TABS = [];
const POSTED = { 'f-files': [], 'f-feed': [], 'f-chat': [] };
let MOBILE = false, TAB = 'chat', FILES_READY = 'complete', FILES_LOADS = [];
const frame = (id) => ({ contentWindow: { postMessage: (m) => POSTED[id].push(JSON.parse(JSON.stringify(m))) },
  contentDocument: { get readyState() { return id === 'f-files' ? FILES_READY : 'complete'; } },
  addEventListener: (ev, f) => { if (ev === 'load' && id === 'f-files') FILES_LOADS.push(f); },
  removeEventListener: (ev, f) => { if (id === 'f-files') FILES_LOADS = FILES_LOADS.filter((g) => g !== f); } });
global.window = global;
global.addEventListener = (ev, f) => { if (ev === 'message') LISTENERS.push(f); };
global.__rompPaneToggle = (k, on) => TOGGLES.push([k, on]);
global.__rompMobileTab = (t) => TABS.push(t);
global.__rompMobileOn = () => MOBILE;
const stub = () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, appendChild() {}, setAttribute() {}, addEventListener() {}, remove() {} });
global.document = {
  body: { classList: { toggle() {}, contains: (c) => c === 'po-chat' || c === 'po-feed' || c === 'po-timeline' },
          getAttribute: (a) => (a === 'data-tab' ? TAB : null), appendChild() {} },
  getElementById: (id) => (id in POSTED ? frame(id) : null),
  createElement: stub, documentElement: { style: { setProperty() {} } },
  querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
};
global.localStorage = { getItem: () => null, setItem() {} };
global.sessionStorage = { getItem: () => 'wid1', setItem() {} };
global.location = { protocol: 'http:', host: 'TESTHOST:1', search: '', reload() {} };
global.fetch = () => new Promise(() => {});
global.setTimeout = () => 0; global.setInterval = () => 0; global.clearTimeout = () => {};
global.Event = class { constructor(t) { this.type = t; } };
"""
_ARMS_DRIVER = r"""
const send = (m) => LISTENERS.forEach((f) => f({ data: m }));
const snap = () => ({ toggles: TOGGLES.slice(), tabs: TABS.slice(), files: POSTED['f-files'].slice(), feed: POSTED['f-feed'].slice(),
  chat: POSTED['f-chat'].slice(), from: window.__rompFilesTabFrom === undefined ? 'undef' : window.__rompFilesTabFrom });
const reset = () => { TOGGLES.length = 0; TABS.length = 0; for (const k in POSTED) POSTED[k].length = 0; delete window.__rompFilesTabFrom; };
const SID = '__SID__';
const identity = { name: 'web', color: { bg: '#123456', fg: '#ffffff' } };
const out = { listeners: LISTENERS.length };
send({ romp: 'viewFile', pane: 'pane', path: '/repo/notes-api/src/app.py', sid: SID, identity });
out.desktop = snap(); reset();
send({ romp: 'viewFile', pane: 'pane', path: '/repo/notes-api/README.md', sid: 'TESTHOST:' + SID });
out.bare = snap(); reset();
MOBILE = true; TAB = 'chat';
send({ romp: 'viewFile', pane: 'pane', path: '/repo/notes-api/src/app.py', sid: SID, identity });
out.phone = snap();
send({ romp: 'filesViewerClosed' });
out.phoneClosed = snap();
send({ romp: 'filesViewerClosed' });
out.phoneClosedAgain = snap(); reset();
TAB = 'files';
send({ romp: 'viewFile', pane: 'pane', path: '/p', sid: SID });
out.already = snap(); reset();
MOBILE = false; window.__rompFilesTabFrom = 'chat';
send({ romp: 'filesViewerClosed' });
out.deskClosed = snap(); reset();
const probe = window.__rompMobileOn; delete window.__rompMobileOn;
send({ romp: 'viewFile', pane: 'pane', path: '/p', sid: SID });
out.noProbe = snap(); reset(); window.__rompMobileOn = probe;
send({ romp: 'viewFile', path: '/p', sid: SID });
out.noPane = snap(); reset();
send({ romp: 'browseFiles', path: '/repo/notes-api', sid: SID });
out.browse = snap(); reset();
// the Files page is still loading when the click arrives: the forward waits for the iframe's load, once
FILES_READY = 'loading';
send({ romp: 'viewFile', pane: 'pane', path: '/repo/notes-api/src/app.py', sid: SID, identity });
out.early = { toggles: TOGGLES.slice(), files: POSTED['f-files'].slice(), waiting: FILES_LOADS.length };
FILES_READY = 'complete'; FILES_LOADS.slice().forEach((f) => f());
out.loaded = { files: POSTED['f-files'].slice(), waiting: FILES_LOADS.length };
FILES_LOADS.slice().forEach((f) => f());
out.reloaded = { files: POSTED['f-files'].slice() }; reset();
send({ type: 'editorSelection', text: 'the auth check', sid: SID, src: 'src/app.py:12' });
out.seed = snap(); reset();
console.log(JSON.stringify(out));
"""


class RelayArms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        js = km._LANDING_SETTINGS_JS.replace("__ROMP_BOOT__", json.dumps("boot-1")).replace("__ROMP_LOADER__", json.dumps(""))
        cls.out = _run(_ARMS_HARNESS + js + _ARMS_DRIVER.replace("__SID__", SID))

    def test_the_script_registers_one_message_listener(self):
        self.assertEqual(self.out["listeners"], 1)

    def test_desktop_a_pane_click_brings_the_files_pane_forward_and_forwards_the_identity(self):
        d = self.out["desktop"]
        self.assertEqual(d["toggles"], [["files", True]], "the Files pane comes forward; the feed is not touched")
        self.assertEqual(d["tabs"], [], "desktop: no mobile tab switch (the column is already visible)")
        self.assertEqual(d["from"], "undef", "and nothing to remember")
        self.assertEqual(d["files"], [{"romp": "viewFile", "path": "/repo/notes-api/src/app.py", "sid": SID,
                                       "identity": {"name": "web", "color": {"bg": "#123456", "fg": "#ffffff"}}}])
        self.assertEqual(d["feed"], [], "nothing reaches the feed")
        self.assertEqual(d["chat"], [])
        # no identity on the relay: the forward carries null (never undefined), and the pane falls to the stub;
        # a remote session's prefixed sid rides through untouched
        b = self.out["bare"]
        self.assertEqual(b["files"], [{"romp": "viewFile", "path": "/repo/notes-api/README.md", "sid": "TESTHOST:" + SID, "identity": None}])

    def test_phone_the_relay_switches_to_the_files_tab_and_the_close_puts_the_person_back_once(self):
        p = self.out["phone"]
        self.assertEqual(p["tabs"], ["files"])
        self.assertEqual(p["from"], "chat", "the tab the click came from is remembered")
        self.assertEqual(p["toggles"], [["files", True]], "the desktop bring-forward still runs (keeps po in step)")
        c = self.out["phoneClosed"]
        self.assertEqual(c["tabs"], ["files", "chat"], "close: back to the remembered tab")
        self.assertIsNone(c["from"], "and the memory is consumed")
        self.assertEqual(self.out["phoneClosedAgain"]["tabs"], ["files", "chat"], "a second close with nothing remembered switches nothing")
        a = self.out["already"]
        self.assertEqual(a["tabs"], [], "already on the Files tab: nothing to switch")
        self.assertEqual(a["from"], "undef", "and nothing to remember")

    def test_desktop_close_drops_a_stale_memory_without_replaying_it(self):
        d = self.out["deskClosed"]
        self.assertEqual(d["tabs"], [])
        self.assertIsNone(d["from"], "dropped, never replayed later (a rotation to desktop between open and close)")

    def test_a_shell_without_the_layout_probe_does_not_throw(self):
        n = self.out["noProbe"]
        self.assertEqual(n["tabs"], [])
        self.assertEqual(len(n["files"]), 1, "the forward still happens")

    def test_a_view_file_naming_no_pane_is_not_relayed(self):
        # the chat opens in place for "here"; a message with no target is not this arm's and goes nowhere
        n = self.out["noPane"]
        self.assertEqual(n["files"], [])
        self.assertEqual(n["feed"], [])
        self.assertEqual(n["toggles"], [])

    def test_a_click_before_the_files_page_has_loaded_is_delivered_on_its_load_once(self):
        # the dashboard just opened (or a phone's hidden iframe boots late): a postMessage into a document whose
        # files.js has not registered its listener would be dropped with the pane brought forward empty
        e = self.out["early"]
        self.assertEqual(e["toggles"], [["files", True]], "the pane still comes forward at once")
        self.assertEqual(e["files"], [], "nothing is posted into a document that cannot hear it yet")
        self.assertEqual(e["waiting"], 1, "one load listener holds the click")
        l = self.out["loaded"]
        self.assertEqual(len(l["files"]), 1, "the load delivers it")
        self.assertEqual(l["files"][0]["path"], "/repo/notes-api/src/app.py")
        self.assertEqual(l["waiting"], 0, "and the listener is gone")
        self.assertEqual(len(self.out["reloaded"]["files"]), 1, "a later reload of the pane does not replay it")

    def test_the_feeds_browse_relay_and_the_quote_seed_forward_are_untouched(self):
        b = self.out["browse"]
        self.assertEqual(b["feed"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": SID}])
        self.assertEqual(b["tabs"], ["feed"], "the feed's browser still switches a phone to the Feed tab")
        self.assertEqual(b["files"], [])
        s = self.out["seed"]
        self.assertEqual(s["chat"], [{"type": "editorSelection", "text": "the auth check", "sid": SID, "src": "src/app.py:12"}])


if __name__ == "__main__":
    unittest.main()
