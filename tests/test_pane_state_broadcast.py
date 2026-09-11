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
const POSTED = {}, LOADS = {}, CLS = new Set(['po-chat', 'po-feed', 'po-timeline']), STORE = {}, STORAGE = [], SETS = {}, TABS = [];
const KEYS = __KEYS__;
// the served markup: the chat and the Files pane carry src, the optional panes carry data-src (the controller
// copies it to src for a pane this browser shows)
const frames = {};
KEYS.forEach((k) => { const attrs = (k === 'chat' || k === 'files') ? { src: '/' + k } : { 'data-src': '/' + k }; frames['f-' + k] = {
  attrs,
  getAttribute: (a) => (a in attrs ? attrs[a] : null),
  setAttribute: (a, v) => { attrs[a] = v; SETS[k] = (SETS[k] || 0) + 1; },
  contentWindow: { postMessage: (m) => { (POSTED[k] = POSTED[k] || []).push(JSON.parse(JSON.stringify(m))); } },
  addEventListener: (ev, f) => { if (ev === 'load') (LOADS[k] = LOADS[k] || []).push(f); } }; });
// one button per pane stands for its rail button AND its phone tab (both are found by data-pane)
const BTNS = {};
KEYS.forEach((k) => { BTNS[k] = { hidden: false, title: '', getAttribute: (a) => (a === 'data-pane' ? k : null), classList: { toggle() {} }, addEventListener() {} }; });
let TAB = 'chat', MOBILE = false;
global.window = global;
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = v; } };
global.location = { search: '' };
global.URLSearchParams = class { get() { return null; } };
global.Event = class { constructor(t) { this.type = t; } };
global.addEventListener = (ev, f) => { if (ev === 'storage') STORAGE.push(f); };
global.dispatchEvent = () => true;
global.document = {
  body: { classList: { toggle: (c, on) => { if (on) CLS.add(c); else CLS.delete(c); }, contains: (c) => CLS.has(c) },
          getAttribute: (a) => (a === 'data-tab' ? TAB : null) },
  querySelectorAll: (sel) => { if (sel === '.rail-btn[data-pane]') return KEYS.map((k) => BTNS[k]); const m = /data-pane=(\w+)/.exec(sel); return m && BTNS[m[1]] ? [BTNS[m[1]]] : []; },
  getElementById: (id) => frames[id] || null,
};
window.__rompMobileOn = () => MOBILE;
window.__rompMobileTab = (t) => { TABS.push(t); TAB = t; };
__SEED__
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
# The store holds NO romp:settings at all (a fresh install: the control is OFF by default since T317b, the user
# 2026-09-10) and the Files pane is ON (a desktop session of the shown-by-default era left it open):
# the boot apply hides the control (body.no-files-control), closes the pane and saves that, tells the panes the
# pane is unavailable (avail.files false), and refuses to bring it forward; a phone left on the Files tab is
# switched to the chat. Flipping the store back on (the gear's write, heard through the storage listener) shows
# the control again and the pane can open.
_HIDDEN_DRIVER = r"""
const last = (k) => (POSTED[k] || []).slice(-1)[0];
const counts = () => Object.fromEntries(KEYS.map((k) => [k, (POSTED[k] || []).length]));
const out = {};
out.boot = { cls: CLS.has('no-files-control'), poFiles: CLS.has('po-files'), store: JSON.parse(STORE['romp-panes'] || 'null'), chat: last('chat'), counts: counts() };
window.__rompPaneToggle('files', true);          // a relay's bring-forward, the palette's command: refused
out.refused = { poFiles: CLS.has('po-files'), chat: last('chat'), counts: counts() };
MOBILE = true; TAB = 'files'; SWITCHED.length = 0; window.__rompPaneToggle('feed');   // any apply (here a feed flip) on a phone left on the Files tab
out.phone = { switched: SWITCHED.slice() };
MOBILE = false;
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true }); STORAGE_LISTENERS.forEach((f) => f());   // the gear turns it back on
out.shown = { cls: CLS.has('no-files-control'), chat: last('chat') };
window.__rompPaneToggle('files', true);
out.reopened = { poFiles: CLS.has('po-files'), chat: last('chat') };
console.log(JSON.stringify(out));
"""


_BOOKMARK_DRIVER = r"""
const out = { poFiles: CLS.has('po-files'), cls: CLS.has('no-files-control'), store: STORE['romp-panes'], chat: (POSTED.chat || []).slice(-1)[0] };
console.log(JSON.stringify(out));
"""


class HiddenControlBookmark(unittest.TestCase):
    """A ?panes=chat,files bookmark opened with the control hidden: the view shows the chat alone (the pane is closed
    on the boot apply), but the stored pane set is NOT rewritten with the bookmark's — a bookmark was always a view,
    never a write (review find: the forced close's save would have replaced the person's stored set)."""

    @classmethod
    def setUpClass(cls):
        keys = [k for k, _ in km._PANE_ORDER]
        stored = json.dumps({"chat": True, "fleet": False, "feed": True, "timeline": True, "files": False})
        # the store is seeded through the harness's __SEED__ slot (the OptionalPanes convention): the declaration
        # line it once rewrote grew the optional-pane collections and no longer matched, leaving the slot unfilled
        harness = (_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(keys))
                   .replace("__SEED__", "STORE['romp:settings'] = JSON.stringify({ filesControl: true }); STORE['romp-panes'] = " + json.dumps(stored) + ";")   # the T317-era key's true (a whole-object save merged it in): never read, the control stays hidden (T317b)
                   .replace("global.location = { search: '' };", "global.location = { search: '?panes=chat,files' };")
                   .replace("global.URLSearchParams = class { get() { return null; } };", "global.URLSearchParams = class { get(k) { return k === 'panes' ? 'chat,files' : null; } };"))
        cls.stored = stored
        cls.out = _run(harness + km._LANDING_COLLAPSE_JS + _BOOKMARK_DRIVER)

    def test_the_bookmark_shows_the_chat_alone_and_writes_nothing(self):
        self.assertFalse(self.out["poFiles"], "the bookmark's files pane is closed: the control is hidden")
        self.assertTrue(self.out["cls"])
        self.assertEqual(self.out["store"], self.stored, "the stored pane set is untouched: a bookmark is a view")
        self.assertEqual(self.out["chat"]["on"], {"chat": True, "timeline": False, "fleet": False, "feed": False, "files": False})
        self.assertEqual(self.out["chat"]["avail"], {"files": False})


class HiddenControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = [k for k, _ in km._PANE_ORDER]
        # seeded through __SEED__ (see HiddenControlBookmark); the harness already collects the storage listeners
        # (STORAGE) and the phone's tab switches (TABS), which the driver reads under its own names
        harness = (_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(keys))
                   .replace("__SEED__", "STORE['romp-panes'] = JSON.stringify({ chat: true, fleet: false, feed: true, timeline: true, files: true });"   # no romp:settings: the default hides (T317b)
                            "const STORAGE_LISTENERS = STORAGE, SWITCHED = TABS;"))
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
        self.assertEqual(r["counts"], self.out["boot"]["counts"], "…silently: no message claiming a change")
        self.assertEqual(self.out["boot"]["counts"], {k: 1 for k in [k for k, _ in km._PANE_ORDER]}, "the boot apply told each pane once")

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
        # the control turned ON by its gear setting (off by default since T317b): the toggles the driver makes are the
        # user's clicks on a control they asked for; seeded through the harness's __SEED__ slot
        cls.out = _run(_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(cls.keys)).replace("__SEED__", "STORE['romp:settings'] = JSON.stringify({ showFilesControl: true });") + km._LANDING_COLLAPSE_JS + _COLLAPSE_DRIVER)

    def test_the_boot_apply_tells_every_pane_the_set_as_the_flags_stand(self):
        self.assertEqual(self.out["boot"]["counts"], {k: 1 for k in self.keys}, "one message per pane at boot")
        self.assertEqual(self.out["boot"]["chat"], {"romp": "panes", "on": {"chat": True, "timeline": True, "fleet": False, "feed": True, "files": False},
                                                     "avail": {"files": True}})   # avail: the Files control's setting rides every tell (T317); on here by the store (off by default since T317b)
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


# ── the optional panes (the user 2026-09-10) ──────────────────────────────────────────────────────
# The gear's Panes section (romp:settings.panes) hides Sessions (timeline), the Outline (fleet) or the Feed
# from this browser's dashboard altogether. The controller reads it at boot and on the storage event: a pane
# off there leaves po and KEYS (togglePane refuses it, the broadcast omits it), wears hidden on its rail
# button and phone tab, and never gets its src (the markup carries data-src); a pane on gets its src once.
_OPT_DRIVER = r"""
const counts = () => Object.fromEntries(KEYS.map((k) => [k, (POSTED[k] || []).length]));
const last = (k) => (POSTED[k] || []).slice(-1)[0];
const src = () => Object.fromEntries(KEYS.map((k) => [k, frames['f-' + k].getAttribute('src')]));
const hidden = () => Object.fromEntries(KEYS.map((k) => [k, BTNS[k].hidden]));
const out = {};
out.boot = { counts: counts(), chat: last('chat'), src: src(), hidden: hidden(), cls: CLS.has('po-feed'), tabs: TABS.slice(), sets: Object.assign({}, SETS) };
window.__rompPaneToggle('feed', true);           // the rail cannot bring a pane back that the gear took out
out.refused = { counts: counts(), cls: CLS.has('po-feed'), chat: last('chat') };
window.__rompPaneToggle('files', true);          // the other panes toggle as ever, and the set persisted omits the hidden one
out.files = { chat: last('chat'), store: JSON.parse(STORE['romp-panes'] || 'null') };
// the gear turns the feed back on: its save lands here as a storage event for romp:settings
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: { feed: true } });
STORAGE.forEach((f) => f({ key: 'romp:settings' }));
out.enabled = { counts: counts(), chat: last('chat'), feed: last('feed'), src: src(), hidden: hidden(), cls: CLS.has('po-feed'), sets: Object.assign({}, SETS) };
(LOADS.feed || []).forEach((f) => f());          // the feed page loads now, after the shell: it hears the set on its load
out.loaded = { counts: counts(), feed: last('feed') };
// and off again, then on: the src is never reassigned (no reload of a live pane), the flag it had comes back
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: { feed: false } });
STORAGE.forEach((f) => f({ key: 'romp:settings' }));
out.off = { chat: last('chat'), cls: CLS.has('po-feed'), hidden: hidden(), src: src() };
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: {} });
STORAGE.forEach((f) => f({ key: 'romp:settings' }));
out.back = { chat: last('chat'), cls: CLS.has('po-feed'), sets: Object.assign({}, SETS) };
// a storage event for another key re-applies but does not re-read the panes
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: { fleet: false } });
STORAGE.forEach((f) => f({ key: 'romp-pane-grow' }));
out.otherKey = { chat: last('chat'), hidden: hidden() };
STORAGE.forEach((f) => f({ key: null }));       // a cleared store is read again
out.cleared = { chat: last('chat'), hidden: hidden() };
// the gear turns the Outline on, whose rail default is OFF: it comes on screen (the reason to turn it on), at a
// fair width, and the set persists so a reload keeps it; the pane's own rail toggle hides it from there
const GREW = []; window.__rompGrowFair = (k) => GREW.push(k);
out.fleetBefore = { cls: CLS.has('po-fleet'), store: JSON.parse(STORE['romp-panes'] || 'null') };
STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: {} });
STORAGE.forEach((f) => f({ key: 'romp:settings' }));
out.fleetOn = { cls: CLS.has('po-fleet'), chat: last('chat'), hidden: hidden(), grew: GREW.slice(), store: JSON.parse(STORE['romp-panes'] || 'null'), src: src() };
window.__rompPaneToggle('fleet');
out.fleetRailOff = { cls: CLS.has('po-fleet'), chat: last('chat'), store: JSON.parse(STORE['romp-panes'] || 'null') };
STORAGE.forEach((f) => f({ key: 'romp:settings' }));   // a save that changes no pane's setting moves nothing
out.fleetStays = { cls: CLS.has('po-fleet'), grew: GREW.slice() };
console.log(JSON.stringify(out));
"""


class OptionalPanes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = [k for k, _ in km._PANE_ORDER]
        # this browser hid the Feed pane in the gear, and a phone was left on the Feed tab
        # the Files control is ON in this browser (off by default since T317b), so the driver's Files toggle is a control the
        # user asked for; every gear save below carries the key too, as the gear's whole-object save does
        seed = "STORE['romp:settings'] = JSON.stringify({ showFilesControl: true, panes: { feed: false } }); TAB = 'feed';"
        cls.out = _run(_COLLAPSE_HARNESS.replace("__KEYS__", json.dumps(cls.keys)).replace("__SEED__", seed) + km._LANDING_COLLAPSE_JS + _OPT_DRIVER)

    def test_a_pane_hidden_in_the_gear_is_not_in_the_dashboard_at_boot(self):
        b = self.out["boot"]
        self.assertEqual(b["chat"]["on"], {"chat": True, "timeline": True, "fleet": False, "files": False}, "the broadcast omits the hidden pane's key")
        self.assertEqual(b["counts"], {"chat": 1, "timeline": 1, "fleet": 1, "feed": 0, "files": 1}, "the hidden pane is told nothing (it has no document)")
        self.assertEqual(b["src"], {"chat": "/chat", "timeline": "/timeline", "fleet": "/fleet", "feed": None, "files": "/files"},
                         "the shown optional panes load from data-src; the hidden one never gets a src")
        self.assertEqual(b["hidden"], {"chat": False, "timeline": False, "fleet": False, "feed": True, "files": False}, "its rail button and phone tab are hidden")
        self.assertFalse(b["cls"], "no po-feed body class: the column is not shown")
        self.assertEqual(b["tabs"], ["chat"], "a phone left on the hidden pane's tab goes back to the chat")
        self.assertEqual(b["sets"], {"timeline": 1, "fleet": 1}, "src set once per shown pane")

    def test_the_rail_toggle_refuses_a_hidden_pane_and_the_persisted_set_omits_it(self):
        r = self.out["refused"]
        self.assertEqual(r["counts"]["chat"], 1, "no re-apply, no broadcast: nothing changed")
        self.assertFalse(r["cls"])
        f = self.out["files"]
        self.assertEqual(f["chat"]["on"], {"chat": True, "timeline": True, "fleet": False, "files": True})
        self.assertNotIn("feed", f["store"], "romp-panes persists the set without the hidden key")

    def test_enabling_in_the_gear_loads_the_pane_and_restores_its_button_tab_and_key(self):
        e = self.out["enabled"]
        self.assertEqual(e["src"]["feed"], "/feed", "the storage event copies data-src to src")
        self.assertEqual(e["hidden"]["feed"], False)
        self.assertTrue(e["cls"], "the pane comes back with its default flag (on)")
        self.assertEqual(e["chat"]["on"], {"chat": True, "timeline": True, "fleet": False, "feed": True, "files": True}, "the broadcast carries the key again")
        self.assertEqual(e["counts"]["feed"], 1, "the re-apply's broadcast reaches the iframe element (the page is still loading)")
        l = self.out["loaded"]
        self.assertEqual(l["counts"]["feed"], 2, "and its own load re-tells it, like any pane that boots after the shell")
        self.assertEqual(l["feed"]["on"]["feed"], True)

    def test_off_again_hides_without_unloading_and_on_again_never_reassigns_the_src(self):
        o = self.out["off"]
        self.assertNotIn("feed", o["chat"]["on"])
        self.assertFalse(o["cls"])
        self.assertTrue(o["hidden"]["feed"])
        self.assertEqual(o["src"]["feed"], "/feed", "a loaded pane keeps its document until the next dashboard load")
        b = self.out["back"]
        self.assertTrue(b["cls"], "the flag it had comes back")
        self.assertEqual(b["chat"]["on"]["feed"], True)
        self.assertEqual(b["sets"], {"timeline": 1, "fleet": 1, "feed": 1}, "src was set exactly once per pane across the whole run")

    def test_only_the_settings_key_or_a_cleared_store_re_reads_the_panes(self):
        self.assertEqual(self.out["otherKey"]["hidden"]["fleet"], False, "a storage event for another key does not re-read")
        self.assertIn("fleet", self.out["otherKey"]["chat"]["on"])
        self.assertEqual(self.out["cleared"]["hidden"]["fleet"], True, "a cleared store (key null) is read again")
        self.assertNotIn("fleet", self.out["cleared"]["chat"]["on"])

    def test_a_pane_turned_on_in_the_gear_comes_on_screen_and_the_rail_hides_it_from_there(self):
        # the Outline's rail default is off, so before this a re-enabled Outline got its button back and nothing else,
        # while the gear's row says the column and its button are gone when off (so back when on). At boot the stored
        # rail flag still rules (a pane the gear shows keeps the state the rail left it in)
        b = self.out["fleetBefore"]
        self.assertFalse(b["cls"], "the Outline was off screen (the boot default, then hidden by the cleared store)")
        self.assertEqual((b["store"] or {}).get("fleet"), False, "and its stored rail flag is off (the Files toggle's save wrote the set)")
        o = self.out["fleetOn"]
        self.assertTrue(o["cls"], "turned on in the gear: the column comes on screen")
        self.assertEqual(o["chat"]["on"]["fleet"], True, "and the panes are told")
        self.assertFalse(o["hidden"]["fleet"], "its rail button and phone tab are back")
        self.assertEqual(o["grew"], ["fleet"], "at a fair width, as the rail's bring-forward gives")
        self.assertEqual(o["store"]["fleet"], True, "persisted, so a reload keeps it")
        self.assertEqual(o["src"]["fleet"], "/fleet", "loaded at boot already: the src is not touched")
        r = self.out["fleetRailOff"]
        self.assertFalse(r["cls"], "the rail toggle hides it from there")
        self.assertEqual(r["store"]["fleet"], False)
        s = self.out["fleetStays"]
        self.assertFalse(s["cls"], "a gear save that changes no pane's setting does not bring it back")
        self.assertEqual(s["grew"], ["fleet"])
        self.assertEqual(self.out["boot"]["chat"]["on"]["fleet"], False, "at boot the stored rail flag rules (off by default)")
        js = km._LANDING_COLLAPSE_JS
        self.assertIn("po[k]=live?true:flagOf(k);", js)
        self.assertIn("reconcile(true);apply();", js, "the storage listener's reconcile is the live one")
        self.assertIn("reconcile();   // the optional panes", js, "the boot one is not")

    def test_the_markup_and_the_mechanism(self):
        html = km._landing()
        for k in ("fleet", "feed", "timeline"):
            self.assertIn("<iframe id=f-%s data-src=/%s>" % (k, k), html, "the optional pane is served without a src")
            self.assertNotIn("<iframe id=f-%s src=" % k, html)
        self.assertIn("<iframe id=f-chat class=m-on src=/chat>", html, "the chat is required and loads at once")
        self.assertIn("<iframe id=f-files src=/files>", html, "the Files pane keeps its rail toggle, not this switch")
        js = km._LANDING_COLLAPSE_JS
        self.assertIn("OPT=['timeline','fleet','feed']", js)
        self.assertIn("SK='romp:settings'", js)
        self.assertIn("on[k]=p[k]!==false", js, "only an explicit false hides (settings.ts paneSet)")
        self.assertIn("f.setAttribute('src',f.getAttribute('data-src'))", js)
        self.assertIn("KEYS=ALL.filter(function(k){return k in po;})", js)
        self.assertLess(js.index("reconcile();   // the optional panes"), js.index("\n  apply();\n"), "reconciled before the first apply")
        self.assertIn(".rail-btn[hidden]{display:none}", html, "the author display:flex would otherwise defeat hidden")
        self.assertIn("#mtabs button[hidden]{display:none}", html)


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
const WSS = [], BADGES = [];   // the shell sockets the script dials; the icon badge calls (0 = cleared)
global.WebSocket = class { constructor() { this.readyState = 0; WSS.push(this); } send() {} };
Object.defineProperty(global, 'navigator', { configurable: true, value: {   // an installed app: badging exists (node's own navigator is getter-only)
  setAppBadge: (n) => { BADGES.push(n); return Promise.resolve(); }, clearAppBadge: () => { BADGES.push(0); return Promise.resolve(); } } });
let FEED_OFF = false;   // the gear's Panes section has the Feed pane off in this browser (the head script's reader, stubbed)
global.__rompPaneEnabled = (k) => !(k === 'feed' && FEED_OFF);
global.encodeURIComponent = (s) => s;
global.location = { protocol: 'http:', host: 'TESTHOST:1' };
global.sessionStorage = { getItem: () => 'wid1' };
global.setTimeout = () => 0;
const pane = (id) => ({ id, classList: { toggle: (c, on) => { TOGGLES.push([id, c, !!on]); } }, contentDocument: {},
  contentWindow: { addEventListener: () => {} }, addEventListener: () => {} });
const PANES = {};
['f-chat', 'f-fleet', 'f-feed', 'f-files', 'f-timeline'].forEach((id) => { PANES[id] = pane(id); });
const TAPS = {}, BUTTONS = {};
const button = (key) => BUTTONS[key] || (BUTTONS[key] = { hidden: false, getAttribute: (a) => (a === 'data-pane' ? key : null), classList: { toggle() {} },
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
// a tab the pane controller hid (its pane is off in the gear's Panes section): not a place to go
TELLS.length = 0; BUTTONS.feed.hidden = true; window.__rompMobileTab('feed');
out.hiddenTab = { tab: TAB, tells: TELLS.slice(), store: STORE['romp-mobile-tab'] };
BUTTONS.feed.hidden = false; window.__rompMobileTab('feed');
out.shownAgain = { tab: TAB };
// the kernel's badge frame on the shell socket: the needs-you count paints the icon while the Feed pane is in this
// dashboard; with the pane off here (the gear's Panes section) the same frame clears it instead
const ws = WSS[0]; const frame = (m) => ws.onmessage({ data: JSON.stringify(m) });
frame({ type: 'badge', n: 3 });
FEED_OFF = true; frame({ type: 'badge', n: 4 });
frame({ type: 'badge', n: 0 });
FEED_OFF = false; frame({ type: 'badge', n: 2 });
frame({ type: 'badge', n: 0 });
out.badges = { sockets: WSS.length, calls: BADGES.slice() };
console.log(JSON.stringify(out));
"""


class MobileScriptDefault(unittest.TestCase):
    """The phone script with NO settings store (a fresh install): the Files control is off by default (T317b, the user
    2026-09-10), so its tab is hidden and a switch aimed at it (a relay, a stored tab) shows the chat instead."""
    @classmethod
    def setUpClass(cls):
        driver = r"""
const out = {};
out.boot = { tab: TAB };
window.__rompMobileTab('files');
out.files = { tab: TAB, store: STORE['romp-mobile-tab'] || null };
window.__rompMobileTab('feed');
out.feed = { tab: TAB };
console.log(JSON.stringify(out));
"""
        cls.out = _run(_MOBILE_HARNESS + km._LANDING_MOBILE_JS + driver)

    def test_a_switch_to_the_hidden_files_tab_shows_the_chat(self):
        self.assertEqual(self.out["files"]["tab"], "chat", "the Files tab is hidden by default: the chat shows instead")
        self.assertEqual(self.out["feed"]["tab"], "feed", "every other tab switches as ever")


class MobileScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the Files control turned ON by its gear setting (off by default since T317b): the taps below land on a tab
        # the person asked for
        harness = _MOBILE_HARNESS.replace('const TOGGLES = [], TELLS = [], MQL = [], MSGS = [], STORE = {};', "const TOGGLES = [], TELLS = [], MQL = [], MSGS = [], STORE = { 'romp:settings': JSON.stringify({ showFilesControl: true }) };")
        assert harness != _MOBILE_HARNESS
        cls.out = _run(harness + km._LANDING_MOBILE_JS + _MOBILE_DRIVER)

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

    def test_a_hidden_tab_is_skipped(self):
        # the pane controller hides the tab of a pane this browser does not show (the gear's Panes section, the
        # user 2026-09-10); a switch to it (a stale romp-mobile-tab, a reveal) must not land on a blank pane
        self.assertEqual(self.out["hiddenTab"], {"tab": "fleet", "tells": [], "store": "fleet"}, "nothing moved, nothing told, nothing persisted")
        self.assertEqual(self.out["shownAgain"]["tab"], "feed", "unhidden, the same switch lands")

    def test_the_badge_frame_paints_the_icon_only_while_the_feed_pane_is_here(self):
        # the user 2026-09-10: the count is the feed's needs-you column; a browser with the Feed pane off in the
        # gear's Panes section wears no badge from it. The kernel sends the frame as ever (nothing kernel-side
        # changes); the shell clears the icon on it here, so a count set before the pane was hidden does not linger
        b = self.out["badges"]
        self.assertEqual(b["sockets"], 1, "one shell socket")
        self.assertEqual(b["calls"], [3, 0, 0, 2, 0], "3 painted; 4 with the pane off clears; 0 clears; the pane back: 2 painted, 0 cleared")
        js = km._LANDING_MOBILE_JS
        self.assertIn("var bn=(window.__rompPaneEnabled&&!window.__rompPaneEnabled('feed'))?0:m.n;", js)
        self.assertIn("try{(bn?navigator.setAppBadge(bn):navigator.clearAppBadge())['catch'](function(e){});}catch(e){}}", js)

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
const POSTED = { 'f-files': [], 'f-feed': [], 'f-chat': [], 'f-settings': [] };
const FOCUSED = [], CLASSES = [];   // contentWindow.focus() calls by iframe id; body class toggles as [class, on]
let MOBILE = false, TAB = 'chat', FILES_READY = 'complete', FILES_LOADS = [], SETTINGS_LOADS = [];
// the served markup's attributes, per iframe id (getElementById hands out a fresh stub each call, so they live here):
// the settings iframe carries data-src, no src, and its document is the empty one until the page loads
const ATTRS = { 'f-settings': { 'data-src': '/settings' } };
let SETTINGS_URL = 'about:blank';
const frame = (id) => ({ contentWindow: { postMessage: (m) => POSTED[id].push(JSON.parse(JSON.stringify(m))), focus: () => FOCUSED.push(id) },
  contentDocument: { get readyState() { return id === 'f-files' ? FILES_READY : 'complete'; }, get URL() { return id === 'f-settings' ? SETTINGS_URL : 'http://TESTHOST:1/' + id.slice(2); } },
  getAttribute: (a) => (ATTRS[id] && a in ATTRS[id] ? ATTRS[id][a] : null),
  setAttribute: (a, v) => { (ATTRS[id] = ATTRS[id] || {})[a] = v; },
  addEventListener: (ev, f) => { if (ev === 'load' && id === 'f-files') FILES_LOADS.push(f); if (ev === 'load' && id === 'f-settings') SETTINGS_LOADS.push(f); },
  removeEventListener: (ev, f) => { if (id === 'f-files') FILES_LOADS = FILES_LOADS.filter((g) => g !== f); } });
global.window = global;
global.addEventListener = (ev, f) => { if (ev === 'message') LISTENERS.push(f); };
global.__rompPaneToggle = (k, on) => TOGGLES.push([k, on]);
global.__rompMobileTab = (t) => TABS.push(t);
global.__rompMobileOn = () => MOBILE;
let FEED_OFF = false;   // the gear's Panes section has the Feed pane off in this browser (the head script's reader, stubbed)
global.__rompPaneEnabled = (k) => !(k === 'feed' && FEED_OFF);
const stub = () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, appendChild() {}, setAttribute() {}, addEventListener() {}, remove() {} });
global.document = {
  body: { classList: { toggle: (c, on) => CLASSES.push([c, !!on]), contains: (c) => c === 'po-chat' || c === 'po-feed' || c === 'po-timeline' },
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
  chat: POSTED['f-chat'].slice(), settings: POSTED['f-settings'].slice(), from: window.__rompFilesTabFrom === undefined ? 'undef' : window.__rompFilesTabFrom });
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
// the gear: a pane's ask and the shell's own opener both land in the settings iframe, never the feed's. The
// iframe is served with data-src: the FIRST ask gives it its src and waits for the page's load; a second ask
// while it waits is not queued (the page's opener toggles); the load delivers one open; from then on asks post
out.opener = typeof window.__rompOpenSettings;
send({ romp: 'openSettings' });
out.gearAsk = Object.assign(snap(), { src: ATTRS['f-settings'].src, waiting: SETTINGS_LOADS.length }); reset();
window.__rompOpenSettings();
out.gearAskAgain = Object.assign(snap(), { src: ATTRS['f-settings'].src, waiting: SETTINGS_LOADS.length }); reset();
SETTINGS_LOADS.slice().forEach((f) => f());   // the empty document's own load, if it comes late: not the page's
out.gearBlankLoad = snap(); reset();
SETTINGS_URL = 'http://TESTHOST:1/settings'; SETTINGS_LOADS.slice().forEach((f) => f());
out.gearLoaded = snap(); reset();
window.__rompOpenSettings();
out.gearOpen = Object.assign(snap(), { src: ATTRS['f-settings'].src }); reset();
SETTINGS_LOADS.slice().forEach((f) => f());   // a later reload of the page replays nothing
out.gearReloaded = snap(); reset();
// the Feed pane off in this browser (the gear's Panes section): a browse ask naming no pane takes the Files pane's
// arm (the feed cannot be lifted), a browseClosed puts nothing back, and a phone gets the Files tab, not the feed's
FEED_OFF = true;
send({ romp: 'browseFiles', path: '/repo/notes-api', sid: SID });
out.browseFeedOff = Object.assign(snap(), { wasOff: window.__rompFeedWasOff === undefined ? 'undef' : window.__rompFeedWasOff }); reset();
window.__rompFeedWasOff = true;   // a browser lifted the feed, then the gear hid the pane before it closed
send({ romp: 'browseClosed' });
out.closedFeedOff = Object.assign(snap(), { wasOff: window.__rompFeedWasOff }); reset(); delete window.__rompFeedWasOff;
MOBILE = true; TAB = 'chat';
send({ romp: 'browseFiles', path: '/repo/notes-api', sid: SID });
out.browseFeedOffPhone = snap(); reset(); MOBILE = false;
FEED_OFF = false;
window.__rompFeedWasOff = true;
send({ romp: 'browseClosed' });
out.closedFeedOn = Object.assign(snap(), { wasOff: window.__rompFeedWasOff }); reset(); delete window.__rompFeedWasOff;
send({ romp: 'browseFiles', path: '/repo/notes-api', sid: SID });
out.browseFeedOn = snap(); reset();
// the gear's lift bridge: opening lifts the settings iframe and leaves the keyboard where it is; closing hides that
// iframe (the keyboard's document) and puts focus back in the chat
send({ romp: 'settings', on: true });
out.gearLifted = { focused: FOCUSED.slice(), classes: CLASSES.slice() }; FOCUSED.length = 0; CLASSES.length = 0;
send({ romp: 'settings', on: false });
out.gearClosed = { focused: FOCUSED.slice(), classes: CLASSES.slice() };
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
                                       "identity": {"name": "web", "color": {"bg": "#123456", "fg": "#ffffff"}}, "frag": None}])
        self.assertEqual(d["feed"], [], "nothing reaches the feed")
        self.assertEqual(d["chat"], [])
        # no identity on the relay: the forward carries null (never undefined), and the pane falls to the stub;
        # a remote session's prefixed sid rides through untouched
        b = self.out["bare"]
        self.assertEqual(b["files"], [{"romp": "viewFile", "path": "/repo/notes-api/README.md", "sid": "TESTHOST:" + SID, "identity": None, "frag": None}])

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

    def test_the_gear_opens_in_the_settings_iframe_never_in_the_feed(self):
        # the gear lives on the /settings page (the user 2026-09-10): the shell's one opener posts into that
        # iframe, and a pane asking for the gear (the feed's login card, gear-host.ts openGear) is forwarded
        # there; the feed hears nothing and no pane is toggled (the settings iframe is not a pane)
        self.assertEqual(self.out["opener"], "function", "__rompOpenSettings is the shell's one opener")
        for k in ("gearAsk", "gearAskAgain", "gearBlankLoad", "gearLoaded", "gearOpen", "gearReloaded"):
            g = self.out[k]
            self.assertEqual(g["feed"], [], k + ": the feed page hosts no gear")
            self.assertEqual(g["toggles"], [], k + ": no pane moves")
            self.assertEqual(g["tabs"], [], k)
        self.assertEqual(self.out["gearLoaded"]["settings"], [{"romp": "openSettings"}], "the page's load delivers the open")
        self.assertEqual(self.out["gearOpen"]["settings"], [{"romp": "openSettings"}], "a later ask posts at once")

    def test_the_settings_page_loads_on_the_first_open_and_the_ask_waits_for_it(self):
        # the iframe is served with data-src (an eagerly loaded gear cost a kernel socket plus one per attached host on
        # every dashboard load, idle until opened): the first ask copies it to src and holds the open for the page's
        # load, since a message into a document still on its way is dropped and the first click would show nothing
        a = self.out["gearAsk"]
        self.assertEqual(a["src"], "/settings", "the first ask gives the iframe its src")
        self.assertEqual(a["settings"], [], "nothing is posted into a page that has not loaded")
        self.assertEqual(a["waiting"], 1, "one load listener holds the ask")
        b = self.out["gearAskAgain"]
        self.assertEqual(b["settings"], [], "a second ask while the page loads is not queued: the page's opener toggles, two would open and close it")
        self.assertEqual(b["waiting"], 1, "and adds no listener")
        self.assertEqual(b["src"], "/settings", "the src is set once, never reassigned")
        self.assertEqual(self.out["gearBlankLoad"]["settings"], [], "the empty document's own load event is not the page's")
        self.assertEqual(self.out["gearLoaded"]["settings"], [{"romp": "openSettings"}], "the page's load delivers the open, once")
        self.assertEqual(self.out["gearOpen"]["src"], "/settings")
        self.assertEqual(self.out["gearReloaded"]["settings"], [], "a later load replays nothing")
        self.assertIn("<iframe id=f-settings data-src=/settings title=Settings></iframe>", km._landing(), "served without a src")

    def test_closing_the_gear_puts_the_keyboard_back_in_the_chat(self):
        # the gear's document is the hidden settings iframe, lifted while open; closing hides it, which drops focus
        # onto the shell body (a keystroke there reaches no pane), so the bridge focuses the chat, the dashboard's
        # default focus. Opening moves nothing: the gear takes the keyboard itself.
        o = self.out["gearLifted"]
        self.assertEqual(o["classes"], [["settings-open", True]])
        self.assertEqual(o["focused"], [], "opening leaves focus alone")
        c = self.out["gearClosed"]
        self.assertEqual(c["classes"], [["settings-open", False]])
        self.assertEqual(c["focused"], ["f-chat"], "closing focuses the chat iframe's window, once")

    def test_the_feeds_browse_relay_and_the_quote_seed_forward_are_untouched(self):
        b = self.out["browse"]
        self.assertEqual(b["feed"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": SID}])
        self.assertEqual(b["tabs"], ["feed"], "the feed's browser still switches a phone to the Feed tab")
        self.assertEqual(b["files"], [])
        s = self.out["seed"]
        self.assertEqual(s["chat"], [{"type": "editorSelection", "text": "the auth check", "sid": SID, "src": "src/app.py:12"}])

    def test_with_the_feed_pane_off_here_a_browse_ask_takes_the_files_pane_and_nothing_lifts_or_restores_the_feed(self):
        # the user 2026-09-10: the Feed pane hidden in the gear's Panes section is not in this dashboard, so the browse
        # relay cannot lift it; the ask goes to the one file browser the dashboard has, the Files pane, and the was-off
        # restore (a flag set while the pane was still here) moves no pane. The kernel is not party to any of this.
        b = self.out["browseFeedOff"]
        self.assertEqual(b["files"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": SID, "identity": None}], "the Files pane's arm takes it")
        self.assertEqual(b["feed"], [], "nothing is posted into a pane that is not here")
        self.assertEqual(b["toggles"], [["files", True]], "the Files pane comes forward; the feed is never lifted")
        self.assertEqual(b["wasOff"], "undef", "no was-off flag: there is nothing to put back later")
        self.assertEqual(b["tabs"], [], "desktop: no tab switch")
        c = self.out["closedFeedOff"]
        self.assertEqual(c["toggles"], [], "browseClosed with the pane off: no pane moves")
        self.assertFalse(c["wasOff"], "…and the stale flag is dropped, never replayed")
        p = self.out["browseFeedOffPhone"]
        self.assertEqual(p["tabs"], ["files"], "a phone goes to the Files tab, not the feed's")
        self.assertEqual(len(p["files"]), 1)
        self.assertEqual(p["feed"], [])
        # the pane back on: the feed's route is as it was, the restore included
        self.assertEqual(self.out["closedFeedOn"]["toggles"], [["feed", False]])
        self.assertFalse(self.out["closedFeedOn"]["wasOff"])
        self.assertEqual(self.out["browseFeedOn"]["feed"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": SID}])
        self.assertEqual(self.out["browseFeedOn"]["files"], [])
        # the source: the pane arm's head admits a pane-less ask while the feed is off here; the restore is guarded
        js = km._LANDING_SETTINGS_JS
        self.assertIn("if(m.romp==='browseFiles'&&(m.pane==='pane'||!feedHere())){var fb=document.getElementById('f-files');", js)
        self.assertIn("if(feedHere())try{window.__rompPaneToggle&&window.__rompPaneToggle('feed',false);}catch(e){}}", js)
        self.assertIn("function feedHere(){return !(window.__rompPaneEnabled&&!window.__rompPaneEnabled('feed'));}", js)


# ── the head script's reader of the gear's Panes setting ─────────────────────────────────────────
# window.__rompPaneEnabled(k): is pane k in this browser's dashboard at all (romp:settings.panes, the gear's
# Panes section; only an explicit false hides). Every shell script that acts on the feed at an event asks it:
# the badge frame, a Log entry's click, a notification's landing, a browse ask. Defined in the HEAD, before any
# iframe or body script, and read from the store on every call.
HELPER = ("window.__rompPaneEnabled=function(k){try{var s=JSON.parse(localStorage.getItem('romp:settings')||'{}'),p=s&&s.panes;"
          "return !(p&&typeof p==='object'&&p[k]===false);}catch(e){return true;}};")
_HELPER_DRIVER = r"""
'use strict';
const STORE = {};
global.window = global;
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null) };
__HELPER__
const ask = () => ['timeline', 'fleet', 'feed', 'chat'].map((k) => window.__rompPaneEnabled(k));
const out = {};
out.empty = ask();                                                       // no store: every pane shown
STORE['romp:settings'] = JSON.stringify({ theme: 'dark' });              // an older store without panes
out.older = ask();
STORE['romp:settings'] = JSON.stringify({ panes: { feed: false } });     // the gear hid the feed
out.feedOff = ask();
STORE['romp:settings'] = JSON.stringify({ panes: { feed: 0, fleet: null, timeline: 'no' } });   // falsy but not false: shown
out.falsy = ask();
STORE['romp:settings'] = '{not json';                                    // a corrupt store: every pane shown
out.corrupt = ask();
STORE['romp:settings'] = JSON.stringify({ panes: 'feed' });              // a non-object panes value: shown
out.notObject = ask();
console.log(JSON.stringify(out));
"""


class PaneEnabledReader(unittest.TestCase):
    def test_the_head_defines_it_before_any_iframe_and_the_scripts_ask_it(self):
        html = km._landing()
        self.assertEqual(html.count(HELPER), 1)
        self.assertLess(html.index(HELPER), html.index("<iframe"), "defined in the head, before the first iframe")
        self.assertLess(html.index(HELPER), html.index("<body"))
        self.assertEqual(html.count("<script>"), 21, "the head's existing script carries it: no new script element "
                         "(main's 20 + the chat split's own script, tests/test_chat_split.py — 2026-09-11)")
        for js in (km._LANDING_ERRS_JS, km._LANDING_MOBILE_JS, km._LANDING_REVEAL_JS, km._LANDING_SETTINGS_JS):
            self.assertIn("window.__rompPaneEnabled&&!window.__rompPaneEnabled('feed')", js, "absent helper = every pane shown")

    def test_only_an_explicit_false_hides_and_a_corrupt_store_hides_nothing(self):
        out = _run(_HELPER_DRIVER.replace("__HELPER__", HELPER))
        self.assertEqual(out["empty"], [True, True, True, True])
        self.assertEqual(out["older"], [True, True, True, True])
        self.assertEqual(out["feedOff"], [True, True, False, True])
        self.assertEqual(out["falsy"], [True, True, True, True])
        self.assertEqual(out["corrupt"], [True, True, True, True])
        self.assertEqual(out["notObject"], [True, True, True, True])


if __name__ == "__main__":
    unittest.main()
