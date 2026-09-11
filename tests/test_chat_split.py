#!/usr/bin/env python3
"""Split screen for the chat (the user 2026-09-08: several sessions open at once instead of tabbing through
them). The dashboard shell can hold N chat COLUMNS: every column past the first is a client-made twin of
#chat-pane around an iframe at /chat?col=N (_LANDING_SPLIT_JS), with its own tab strip, its own persisted
active tab/drafts/scroll (the shim keys its state blob by the column), its own socket, and its own grow
weight between the shared gutters. Two halves are checked here:

  * SOURCE PINS against the served shell + shim: the column id reaches the state key and the connect query,
    the shell's bridges (picker lift, editor selection, the bell's "session in front", the Log's connection
    tracking, Alt+Arrow, Escape, the gutters) know about later columns, the rail carries the split action
    without a data-pane (the rail/mobile parity test counts those), and the kernel stamps the column.
  * EXECUTED: the real _LANDING_SPLIT_JS runs in node against a DOM stub (the test_error_center.py pattern)
    and the whole story is driven — open on a session, DOM order, persistence, the hooks it wires, the
    focus arbitration the panes ask for, close, the cap, restore from storage, and no columns on the phone.

Synthetic only — invented sids, no network, no real DOM.
"""
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_split", os.path.join(BIN, "romp-kernel"))


class SplitSourcePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()
        cls.shim = km._shim("chat", 1)

    def test_the_split_script_is_served_after_the_pane_controller_it_leans_on(self):
        self.assertIn("_LANDING_SPLIT_JS", vars(km), "a module-level _JS constant: test_inline_js_parses checks it parses")
        self.assertIn("var CK='romp-chat-cols'", self.html)
        self.assertGreater(self.html.index("var CK='romp-chat-cols'"), self.html.index("window.__rompPaneToggle=togglePane"),
                           "the split runs after the controller: a restored column registers with the gutters and takes a fair "
                           "grow at boot, and a split of a hidden chat group calls __rompPaneToggle")
        self.assertIn("seed(n,sid);f.src='/chat?col='+n+'&skeleton=1';", km._LANDING_SPLIT_JS,
                      "every later column is /chat?col=N, dialled as a skeleton client of the session its seeded blob names (2026-09-11)")

    def test_the_shim_keys_each_column_s_state_and_names_it_on_the_connect(self):
        # the column comes from the URL; the first column ("" or 1) keeps the unsuffixed key it always had, so no
        # one's persisted active tab, drafts or scroll moves
        self.assertIn('var COL=new URLSearchParams(location.search).get("col")||"";if(COL==="1")COL="";', self.shim)
        self.assertIn('var SK="romp-vscode-state-chat"+(COL?":"+COL:"");', self.shim)
        self.assertIn('(COL?"&col="+encodeURIComponent(COL):"")', self.shim, "the kernel can tell the columns apart in its logs")
        # the ?active= connect hint reads the SAME key, so each column redials with ITS tab first
        self.assertIn('var st0=JSON.parse(localStorage.getItem(SK)||"null");active=(st0&&st0.activeId)||"";', self.shim)
        # …and a later column's FIRST dial has the hint too (2026-09-11): the shell seeds that very key with the session
        # the column opens on before the frame exists, and the frame's src says skeleton=1, which the shim puts on the
        # connect query, so the kernel serves the hinted session whole and the rest as skeleton tabs
        self.assertIn("var BK='romp-vscode-state-chat:';", km._LANDING_SPLIT_JS, "the shell's seed key is the shim's SK for /chat?col=N")
        self.assertIn("st.activeId=sid;try{localStorage.setItem(BK+n,JSON.stringify(st));}catch(e){}}", km._LANDING_SPLIT_JS)
        self.assertIn('var SKEL=new URLSearchParams(location.search).get("skeleton")==="1";', self.shim)
        self.assertIn('+(SKEL?"&skeleton=1":""));', self.shim, "the term closes the connect query, after the column")
        self.assertIn('+((everConnected&&bundleReady&&readyAcked&&!readyQueued)?"&reconnect=1":"")', self.shim,
                      "the redial gate is untouched (tests/test_pane_shim_return.py runs it)")

    def test_the_kernel_stamps_the_column_on_the_client(self):
        src = inspect.getsource(km.Handler)
        self.assertIn('col = (q.get("col") or [""])[0]', src)
        self.assertIn('if col:\n            client["col"] = col', src)
        with open(os.path.join(BIN, "romp-kernel"), encoding="utf-8") as fh:
            self.assertIn("(wid=%s col=%s slot=%s queued=%dB frame=%dB)", fh.read(), "the drop log names the column")

    def test_a_later_column_s_skeleton_dial_survives_the_ready_arm_s_reset(self):
        # The handshake records both flags: `reconnect` so the pusher cycle that lands before the bundle's ready serves
        # the skeleton set (one full, not the board) into a document that may not hear it; `skeletonOnReady` so the
        # ready arm, whose _client_reset_chat_base pops `reconnect` with the set, can re-arm it for the connect push
        # the page CAN hear (tests/test_chat_skeleton_reconnect.py runs the whole cycle)
        src = inspect.getsource(km.Handler)
        self.assertIn('skeleton = (q.get("skeleton") or [""])[0] == "1"', src)
        self.assertIn('if skeleton:', src)
        self.assertIn('client["reconnect"] = True\n            client["skeletonOnReady"] = True', src)
        # the ready arm re-arms the flag PAST the reset and BEFORE its connect push
        i = src.index('msg.get("type") == "ready"')
        body = src[i:i + 3500]
        self.assertIn('if client.pop("skeletonOnReady", False):\n                client["reconnect"] = True', body)
        self.assertLess(body.index("_client_reset_chat_base(client)"), body.index('client.pop("skeletonOnReady", False)'))
        self.assertLess(body.index('client.pop("skeletonOnReady", False)'), body.index("self._push_one(client)"))
        # a pre-ready pop (the flag still set) neither stamps the client ready nor lets its caller consume a parked reveal:
        # _reveal_request aims taps at stamped clients, and this page has no listener yet
        rr = inspect.getsource(km._resolve_reconnect)
        self.assertIn('fresh = bool(c.get("skeletonOnReady"))', rr)
        self.assertIn('if not fresh:', rr)
        self.assertLess(rr.index('c.pop("reconnect", False)'), rr.index('fresh = bool(c.get("skeletonOnReady"))'))
        self.assertLess(rr.index('if not fresh:'), rr.index('c["ready"] = True'))
        self.assertEqual(rr.count("return not fresh"), 2, "the no-hint return and the set's return both say whether a REDIAL popped")
        self.assertNotIn("return True", rr)
        # the hand-over is GONE: the seeded blob's activeId is the page's wantActive, so nothing is posted into the frame
        split = km._LANDING_SPLIT_JS
        self.assertNotIn("postMessage({type:'focus'", split)
        self.assertNotIn("addEventListener('load'", split)

    def test_the_rail_carries_no_split_button(self):
        # the split opens from a tab's menu ("Open in new split") and the palette; a bottom-bar button for it
        # read as clutter (the user 2026-09-08), so the rail and the mobile bar carry none
        self.assertNotIn("rail-split", self.html)
        self.assertNotIn("rail-split", km._LANDING_SPLIT_JS)
        self.assertIn("m.romp!=='openSplit'", km._LANDING_SPLIT_JS, "the tab menu's ask is the door")

    def test_the_css_hides_columns_with_the_chat_group_lifts_by_class_and_never_shows_them_on_the_phone(self):
        self.assertIn("body:not(.po-chat) .chat-col,body:not(.po-chat) .gv-chat{display:none}", self.html)
        self.assertIn("body.picker-open .pane.lifted{display:block!important}", self.html)
        self.assertIn("body.picker-open iframe.lifted{display:block;position:fixed;left:0;right:0;top:0;height:var(--app-h,100dvh);z-index:200;background:transparent}", self.html)
        self.assertNotIn("body.picker-open #f-chat{", self.html, "the lift is by class now — a later column's picker lifts THAT column")
        self.assertIn(".chat-col>.col-x{position:absolute;top:4px;right:6px;z-index:7;", self.html)
        mobile = self.html[self.html.index("@media (max-width:820px),(pointer:coarse) and (max-width:1024px){"):]
        self.assertIn(".chat-col,.gv-chat{display:none!important}", mobile)

    def test_the_shell_s_bridges_know_about_later_columns(self):
        # the picker lift marks the ASKING frame (e.source) and its pane
        js = km._LANDING_SETTINGS_JS
        self.assertIn("var lf=(window.__rompFrameOfWin&&window.__rompFrameOfWin(e.source))||document.getElementById('f-chat');", js)
        self.assertIn("Array.prototype.forEach.call(document.querySelectorAll('.lifted'),function(el){el.classList.remove('lifted');});", js)
        self.assertIn("if(m.on&&lf){lf.classList.add('lifted');if(lf.parentElement)lf.parentElement.classList.add('lifted');}", js)
        self.assertIn("document.body.classList.toggle('picker-open',!!m.on);}", js)
        # a passage selected in the feed's viewer lands in the column showing that session, else the one last used
        self.assertIn("fc=(window.__rompChatTarget&&window.__rompChatTarget(m.sid))||fc;", js)
        # the gear's close hands the keyboard back to the column last worked in, else the first (2026-09-11)
        self.assertIn("if(!m.on){var fid=(window.__rompFocusedChatId&&window.__rompFocusedChatId())||'f-chat';"
                      "var fc=document.getElementById(fid)||document.getElementById('f-chat');", js)
        # the bell's "session in front" reads the column last worked in
        self.assertIn("var fid=window.__rompFocusedChatId&&window.__rompFocusedChatId();", km._LANDING_PUSH_JS)
        # the Log tracks a split column's socket under its own key — never masking the first column's
        errs = km._LANDING_ERRS_JS
        self.assertIn("var st={},stc={};", errs)
        self.assertIn("var col=(m.app==='chat'&&window.__rompColOf)?window.__rompColOf(e.source):'';", errs)
        self.assertIn("if(col){var sc=(m.state==='up')?'up':'down',pc=stc[col];stc[col]=sc;", errs)
        self.assertIn("for(var c in stc){if(stc[c]==='down'&&shown('chat'))return true;}", errs)
        self.assertIn("window.__rompColGone=function(c){delete stc[String(c)];paint();};", errs)
        # the first column's tracking is byte-for-byte what it was
        self.assertIn("var s=(m.state==='up')?'up':'down',prev=st[m.app];st[m.app]=s;", errs)
        # Alt+Arrow walks every chat column, the ring follows, later columns are wired as they are made
        focus = km._LANDING_FOCUS_JS
        self.assertIn("window.__rompFocusedChatId=function(){return document.getElementById(lastChat)?lastChat:'f-chat';};", focus)
        self.assertIn("window.__rompWireFocus=function(f){f.addEventListener('load',function(){wire(f);});wire(f);};", focus)
        self.assertIn("function paneOf(id){return PANE[id]||(window.__rompChatPaneOf?window.__rompChatPaneOf(id):null);}", focus)
        self.assertIn("window.__rompWireEsc=function(f){", km._LANDING_ESC_JS)
        # the gutters: later columns register, gv-a/gv-b's left neighbour is the rightmost chat column
        gut = km._LANDING_JS
        self.assertIn("window.__rompRegisterPane=function(id,k){KEYS[id]=k;if(PANES.indexOf(id)<0)PANES.splice(PANES.indexOf('fleet-pane'),0,id);};", gut)
        self.assertIn("window.__rompUnregisterPane=function(id){", gut)
        self.assertIn("function key(id){return KEYS[id]||(id==='chat-pane'?'chat':id==='fleet-pane'?'fleet':id==='feed-pane'?'feed':'files');}", gut)
        self.assertIn("window.__rompGutter=gutter;", gut)
        self.assertIn("gutter('gv-a',function(){return lastChat();},'fleet-pane');", gut)
        # a pane with no grow yet never averages in as NaN (the first split opened 0px wide — review find 2026-09-08),
        # and a column keeps the width it was dragged to across reloads
        self.assertIn(".filter(function(g){return typeof g==='number'&&isFinite(g);});", gut)
        self.assertIn("window.__rompGrowFairIfNew=function(k){if(typeof grow[k]==='number'&&isFinite(grow[k])){setGrow(k,grow[k]);return;}window.__rompGrowFair(k);};", gut)
        split = km._LANDING_SPLIT_JS
        self.assertIn("if(window.__rompGrowFairIfNew)window.__rompGrowFairIfNew('chat'+n);", split)
        # a refused split says why (the acknowledgement rule), and the tab menu can ask first
        self.assertIn("function canSplit(){return !mobile()&&cols.length+1<MAX;}", split)
        self.assertIn("window.__rompCanSplit=canSplit;", split)
        self.assertIn("Four chat columns at most", split)
        self.assertIn("The phone shows one pane at a time", split)
        # no hand-over focus (2026-09-11): the seeded blob names the tab, and a later reload of the frame keeps the tab
        # its own state names — the same key, written by the page itself from then on
        self.assertNotIn("{once:true}", split)
        self.assertNotIn("type:'focus'", split)

    def test_the_parked_push_tap_reveal_is_addressed_to_the_column_that_consumes_it(self):
        # one chat client consumes the parked tap (_consume_pending_reveal), so the pane's column arbitration
        # must not hand that focus to another column: it rides `own`, the same mark the shell's hand-over wears
        src = inspect.getsource(km._consume_pending_reveal)
        self.assertIn('m["own"] = True', src)
        self.assertIn("client[\"send\"](json.dumps(m))", src)


# ── the split script, RUN ────────────────────────────────────────────────────────────────────────────
HARNESS = r"""
'use strict';
let STORE = {};
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = String(v); }, removeItem: (k) => { delete STORE[k]; } };
const CALLS = { register: [], unregister: [], growFair: [], gutter: [], wireFocus: [], wireEsc: [], colGone: [], events: [], posted: [], focus: [], notify: [], toggle: [] };
let BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
let FOCUSED = 'f-chat';   // what the shell's focus script would report as the column last worked in
let MOBILE = false;       // whether #mtabs is displayed (the phone layout)
let BYID = {};
let WL = {};
function mkEl(tag) {
  const el = {
    tagName: tag, className: '', title: '', textContent: '', src: '', parentElement: null, _id: '',
    style: { flex: '', _props: {}, setProperty(k, v) { this._props[k] = v; }, removeProperty(k) { delete this._props[k]; } },
    _attrs: {}, _ls: {}, children: [], _active: '',
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; },
    insertBefore(c, ref) { c.parentElement = this; const i = this.children.indexOf(ref); if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; },
    remove() { const p = this.parentElement; if (p) { const i = p.children.indexOf(this); if (i >= 0) p.children.splice(i, 1); }
      const drop = (n) => { if (n._id) delete BYID[n._id]; n.children.forEach(drop); }; drop(this); this.parentElement = null; },
    addEventListener(k, f, opts) { (this._ls[k] = this._ls[k] || []).push({ f, once: !!(opts && opts.once) }); },
    fire(k, ev) { const ls = (this._ls[k] || []).slice(); this._ls[k] = ls.filter((l) => !l.once); ls.forEach((l) => l.f(ev || { stopPropagation() {} })); },
  };
  Object.defineProperty(el, 'id', { get() { return this._id; }, set(v) { if (this._id) delete BYID[this._id]; this._id = String(v); if (v) BYID[v] = this; } });
  if (tag === 'iframe') {
    el.contentWindow = { postMessage(m) { CALLS.posted.push({ id: el.id, m }); }, focus() { CALLS.focus.push(el.id); } };
    el.contentDocument = { querySelector(sel) { return (sel === '#tabs .tab.active[data-id]' && el._active) ? { getAttribute: () => el._active } : null; } };
  }
  return el;
}
let ROW = null;
global.document = {
  body: { classList: { contains: (c) => BODY_CLASSES.has(c) } },
  querySelector(sel) { return sel === '.row' ? ROW : null; },
  getElementById(id) { return BYID[id] || null; },
  createElement(tag) { return mkEl(tag); },
};
global.getComputedStyle = (el) => ({ display: el === BYID['mtabs'] ? (MOBILE ? 'flex' : 'none') : 'block' });
global.window = global;
global.addEventListener = (t, f) => { (WL[t] = WL[t] || []).push(f); };
global.dispatchEvent = (ev) => { CALLS.events.push(ev); (WL[ev.type] || []).forEach((f) => f(ev)); return true; };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = (o || {}).detail; } };
global.__rompRegisterPane = (id, k) => CALLS.register.push([id, k]);
global.__rompUnregisterPane = (id) => CALLS.unregister.push(id);
global.__rompGrowFair = (k) => CALLS.growFair.push('fair:' + k);
global.__rompGrowFairIfNew = (k) => CALLS.growFair.push(k);   // what the split calls: fair only when the store holds nothing
global.__rompNotify = (kind, text) => CALLS.notify.push([kind, text]);
global.__rompPaneToggle = (k, to) => CALLS.toggle.push([k, to]);
global.__rompGutter = (gid, leftPick, rightId) => CALLS.gutter.push({ gid, leftPick, rightId });
global.__rompWireFocus = (f) => CALLS.wireFocus.push(f.id);
global.__rompWireEsc = (f) => CALLS.wireEsc.push(f.id);
global.__rompColGone = (c) => CALLS.colGone.push(c);
global.__rompFocusedChatId = () => FOCUSED;
function boot(store, mobile) {
  STORE = Object.assign({}, store || {}); MOBILE = !!mobile; BYID = {}; WL = {}; BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
  for (const k in CALLS) CALLS[k] = [];
  ROW = mkEl('div'); ROW.className = 'row';
  const cp = mkEl('div'); cp.id = 'chat-pane'; const fc = mkEl('iframe'); fc.id = 'f-chat'; cp.appendChild(fc); ROW.appendChild(cp);
  const gva = mkEl('div'); gva.id = 'gv-a'; ROW.appendChild(gva);
  const fp = mkEl('div'); fp.id = 'fleet-pane'; ROW.appendChild(fp);
  const gvb = mkEl('div'); gvb.id = 'gv-b'; ROW.appendChild(gvb);
  const fd = mkEl('div'); fd.id = 'feed-pane'; ROW.appendChild(fd);
  const mt = mkEl('nav'); mt.id = 'mtabs';
  (0, eval)(SPLIT_JS);
}
const SPLIT_JS = __SPLIT_JS__;
function order() { return ROW.children.map((c) => c.id); }
"""

DRIVER = r"""
const out = {};
// 1) a fresh desktop dashboard: one column, nothing made
boot({}, false);
out.fresh = { ids: window.__rompChatFrameIds(), order: order(), lastPane: window.__rompLastChatPane() };
// 2) open a split ON a session (the tab menu's ask): a new column + its gutter before gv-a, persisted, wired; the
//    column's state blob names the session BEFORE the frame exists (merged: what a reused number's blob held survives)
STORE['romp-vscode-state-chat:2'] = JSON.stringify({ activeId: '11111111-2222-3333-4444-555555555500', drafts: { '11111111-2222-3333-4444-555555555500': 'a draft kept' }, scroll: 12 });
const f2 = window.__rompSplitChat('11111111-2222-3333-4444-555555555501');
f2.fire('load'); f2.fire('load');   // the frame's load posts nothing, however often it fires: there is no hand-over any more
out.opened = {
  id: f2.id, src: f2.src, col: f2.getAttribute('data-col'), pane: f2.parentElement.id, paneCls: f2.parentElement.className,
  blob: JSON.parse(STORE['romp-vscode-state-chat:2']),
  flex: f2.parentElement.style.flex, order: order(), stored: STORE['romp-chat-cols'],
  register: CALLS.register.slice(), growFair: CALLS.growFair.slice(), wireFocus: CALLS.wireFocus.slice(), wireEsc: CALLS.wireEsc.slice(),
  gutter: CALLS.gutter.map((g) => ({ gid: g.gid, left: g.leftPick(), right: g.rightId })),
  event: CALLS.events.filter((e) => e.type === 'romp-chat-cols').map((e) => ({ col: e.detail.col, open: e.detail.open, frame: e.detail.frame && e.detail.frame.id })),
  posted: CALLS.posted.slice(), focused: CALLS.focus.slice(),
  closeBtn: f2.parentElement.children.filter((c) => c.className === 'col-x').length,
  ids: window.__rompChatFrameIds(), lastPane: window.__rompLastChatPane(), paneOf: window.__rompChatPaneOf('f-chat-2'),
  colOf: window.__rompColOf(f2.contentWindow), frameOfWin: window.__rompFrameOfWin(f2.contentWindow) === f2,
};
// 3) the arbitration the panes ask before acting on a dashboard-aimed focus
BYID['f-chat']._active = '11111111-2222-3333-4444-555555555500';
f2._active = '11111111-2222-3333-4444-555555555501';
FOCUSED = 'f-chat-2';
out.target = {
  showing1: window.__rompChatTarget('11111111-2222-3333-4444-555555555500').id,   // the column already showing it wins…
  showing2: window.__rompChatTarget('11111111-2222-3333-4444-555555555501').id,
  unknownFocused2: window.__rompChatTarget('11111111-2222-3333-4444-555555555599').id,   // …else the column last worked in
};
FOCUSED = 'f-chat';
out.target.unknownFocused1 = window.__rompChatTarget('11111111-2222-3333-4444-555555555599').id;
FOCUSED = 'f-chat-77';   // a stale id (its column is gone): the first column
out.target.stale = window.__rompChatTarget('').id;
FOCUSED = 'f-chat';
// 4) more columns, up to the cap (four counting the first)
const f3 = window.__rompSplitChat(); const f4 = window.__rompSplitChat(); const f5 = window.__rompSplitChat();
out.cap = { ids: window.__rompChatFrameIds(), fifth: f5, stored: STORE['romp-chat-cols'], order: order(), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit(),
            gutterLeft3: CALLS.gutter.filter((g) => g.gid === 'gv-chat-3')[0].leftPick(),
            gutterLeft4: CALLS.gutter.filter((g) => g.gid === 'gv-chat-4')[0].leftPick(), lastPane: window.__rompLastChatPane() };
// 5) close the middle one: its pane and gutter go, the store follows, the hooks hear it, the neighbour's gutter re-aims
CALLS.focus = [];
window.__rompCloseSplit(3);
out.closed3 = { ids: window.__rompChatFrameIds(), order: order(), stored: STORE['romp-chat-cols'], unregister: CALLS.unregister.slice(), colGone: CALLS.colGone.slice(),
                gutterLeft4: CALLS.gutter.filter((g) => g.gid === 'gv-chat-4')[0].leftPick(), focused: CALLS.focus.slice(),
                closedEvent: CALLS.events.filter((e) => e.type === 'romp-chat-cols' && e.detail.open === false).map((e) => e.detail.col) };
// 6) the palette's close with the FIRST column focused closes the last split; with a split focused, that one
window.__rompCloseSplit();
out.closedLast = { ids: window.__rompChatFrameIds(), stored: STORE['romp-chat-cols'] };
FOCUSED = 'f-chat-2'; window.__rompCloseSplit();
out.closedFocused = { ids: window.__rompChatFrameIds(), stored: STORE['romp-chat-cols'] };
FOCUSED = 'f-chat';
// 7) the tab menu's message from a pane, and the × on the column
CALLS.posted = [];
STORE['romp-vscode-state-chat:2'] = 'not json{';   // a corrupt blob is replaced by the seed, never a throw
window.dispatchEvent({ type: 'message', data: { romp: 'openSplit', sid: '11111111-2222-3333-4444-555555555502' } });
const f2b = BYID['f-chat-2']; f2b.fire('load');
out.viaMessage = { ids: window.__rompChatFrameIds(), posted: CALLS.posted.slice(), src: f2b.src, blob: JSON.parse(STORE['romp-vscode-state-chat:2']) };
f2b.parentElement.children.filter((c) => c.className === 'col-x')[0].fire('click', { stopPropagation() {} });
out.viaX = { ids: window.__rompChatFrameIds(), stored: STORE['romp-chat-cols'] };
// 8) the palette's split opens an empty column — and brings a hidden chat group forward first
BODY_CLASSES.delete('po-chat'); CALLS.toggle = [];
window.__rompSplitChat();
out.viaRail = { ids: window.__rompChatFrameIds(), posted: CALLS.posted.length, toggle: CALLS.toggle.slice(), canSplit: window.__rompCanSplit(),
                blob: JSON.parse(STORE['romp-vscode-state-chat:2']) };   // an EMPTY column seeds nothing: the reused number's blob stands
BODY_CLASSES.add('po-chat');
// 9) the columns a browser had open come back, each on its own state (no focus posted: the pane restores its tab)
const BLOB5 = '{"activeId":"11111111-2222-3333-4444-555555555505","scroll":3}';
boot({ 'romp-chat-cols': '[2,5]', 'romp-vscode-state-chat:5': BLOB5 }, false);
out.restored = { ids: window.__rompChatFrameIds(), order: order(), posted: CALLS.posted.slice(), srcs: [BYID['f-chat-2'].src, BYID['f-chat-5'].src],
                 blob5: STORE['romp-vscode-state-chat:5'], blob5Was: BLOB5, blob2: STORE['romp-vscode-state-chat:2'] || null,
                 lastPane: window.__rompLastChatPane(), nextNumber: (window.__rompSplitChat() || {}).id };
// 10) the phone: nothing is restored and nothing opens
boot({ 'romp-chat-cols': '[2]' }, true);
out.mobile = { ids: window.__rompChatFrameIds(), opened: window.__rompSplitChat('x'), order: order(), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit() };
console.log(JSON.stringify(out));
"""


class SplitExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = HARNESS.replace("__SPLIT_JS__", json.dumps(km._LANDING_SPLIT_JS)) + DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the split's JS threw: " + r.stderr[:1200]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_fresh_dashboard_has_one_column(self):
        o = self.out["fresh"]
        self.assertEqual(o["ids"], ["f-chat"])
        self.assertEqual(o["order"], ["chat-pane", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(o["lastPane"], "chat-pane")

    def test_opening_a_split_on_a_session_makes_a_wired_column_before_the_outline_gutter(self):
        o = self.out["opened"]
        self.assertEqual(o["id"], "f-chat-2")
        self.assertEqual(o["src"], "/chat?col=2&skeleton=1")    # its own state blob + connect param (the shim); a skeleton client of its session
        self.assertEqual(o["col"], "2")
        self.assertEqual(o["pane"], "chat-pane-2")
        self.assertEqual(o["paneCls"], "pane chat-col")
        self.assertEqual(o["flex"], "var(--g-chat2,60) 1 0")    # its own grow var, the gutters' store
        # chat | gv-chat-2 | chat 2 | gv-a | outline | gv-b | feed
        self.assertEqual(o["order"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(json.loads(o["stored"]), [2])
        self.assertEqual(o["register"], [["chat-pane-2", "chat2"]])
        self.assertEqual(o["growFair"], ["chat2"], "a fair width, never a sliver — through the store-aware hook, so a dragged width survives a reload")
        self.assertEqual(o["wireFocus"], ["f-chat-2"])
        self.assertEqual(o["wireEsc"], ["f-chat-2"])
        self.assertEqual(o["gutter"], [{"gid": "gv-chat-2", "left": "chat-pane", "right": "chat-pane-2"}])
        self.assertEqual(o["event"], [{"col": 2, "open": True, "frame": "f-chat-2"}], "palette-main wires its chords on this")
        # opened ON a session (2026-09-11): the column's state blob names it BEFORE the frame exists — the shim's ?active=
        # hint and render.ts's wantActive read that key — and nothing is posted into the frame, however often it loads
        self.assertEqual(o["blob"], {"activeId": "11111111-2222-3333-4444-555555555501",
                                     "drafts": {"11111111-2222-3333-4444-555555555500": "a draft kept"}, "scroll": 12},
                         "activeId set, every other field the reused number's blob held kept")
        self.assertEqual(o["posted"], [], "no hand-over focus: the seeded blob does the work, and load fired twice")
        self.assertEqual(o["focused"], ["f-chat-2"], "the new column takes the keyboard")
        self.assertEqual(o["closeBtn"], 1)
        self.assertEqual(o["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(o["lastPane"], "chat-pane-2", "gv-a's left neighbour is now the split")
        self.assertEqual(o["paneOf"], "chat-pane-2")
        self.assertEqual(o["colOf"], "2")
        self.assertTrue(o["frameOfWin"])

    def test_a_focus_belongs_to_the_column_showing_the_session_else_the_one_last_worked_in(self):
        t = self.out["target"]
        self.assertEqual(t["showing1"], "f-chat")
        self.assertEqual(t["showing2"], "f-chat-2")
        self.assertEqual(t["unknownFocused2"], "f-chat-2")
        self.assertEqual(t["unknownFocused1"], "f-chat")
        self.assertEqual(t["stale"], "f-chat", "a stale focus id falls back to the first column")

    def test_four_columns_at_most(self):
        c = self.out["cap"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"])
        self.assertIsNone(c["fifth"])
        self.assertEqual(c["notify"], [["warn", "Four chat columns at most — close one to open another."]], "a refused split says why")
        self.assertFalse(c["canSplit"], "…and the tab menu can ask before offering the item")
        self.assertEqual(json.loads(c["stored"]), [2, 3, 4])
        self.assertEqual(c["order"][:7], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-3", "chat-pane-3", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(c["gutterLeft3"], "chat-pane-2")
        self.assertEqual(c["gutterLeft4"], "chat-pane-3")
        self.assertEqual(c["lastPane"], "chat-pane-4")

    def test_closing_a_column_removes_it_and_re_aims_its_neighbour_s_gutter(self):
        c = self.out["closed3"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-2", "f-chat-4"])
        self.assertEqual(c["order"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-4", "chat-pane-4", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(json.loads(c["stored"]), [2, 4])
        self.assertEqual(c["unregister"], ["chat-pane-3"], "its grow weight leaves the store")
        self.assertEqual(c["colGone"], ["3"], "the Log drops its connection state")
        self.assertEqual(c["gutterLeft4"], "chat-pane-2", "the gutter picks its left neighbour LIVE")
        self.assertEqual(c["focused"], ["f-chat-2"], "the ring moves to the column before it")
        self.assertEqual(c["closedEvent"], [3])

    def test_the_palette_s_close_takes_the_focused_split_else_the_last(self):
        self.assertEqual(self.out["closedLast"]["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(json.loads(self.out["closedLast"]["stored"]), [2])
        self.assertEqual(self.out["closedFocused"]["ids"], ["f-chat"])
        self.assertEqual(json.loads(self.out["closedFocused"]["stored"]), [])

    def test_a_pane_s_ask_the_column_s_x_and_the_palette_all_drive_the_same_code(self):
        m = self.out["viaMessage"]
        self.assertEqual(m["ids"], ["f-chat", "f-chat-2"], "the lowest free number is reused")
        self.assertEqual(m["src"], "/chat?col=2&skeleton=1")
        self.assertEqual(m["posted"], [], "no focus into the frame: the blob names the session")
        self.assertEqual(m["blob"], {"activeId": "11111111-2222-3333-4444-555555555502"}, "a corrupt blob is replaced by the seed")
        self.assertEqual(self.out["viaX"]["ids"], ["f-chat"])
        self.assertEqual(json.loads(self.out["viaX"]["stored"]), [])
        self.assertEqual(self.out["viaRail"]["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(self.out["viaRail"]["posted"], 0, "an EMPTY column: nothing is posted either")
        self.assertEqual(self.out["viaRail"]["blob"], {"activeId": "11111111-2222-3333-4444-555555555502"},
                         "…and nothing is seeded: the reused number's blob stands, so the column comes up on the tab it last held")
        self.assertEqual(self.out["viaRail"]["toggle"], [["chat", True]], "a split of a hidden chat group brings the group forward")
        self.assertTrue(self.out["viaRail"]["canSplit"])

    def test_the_columns_a_browser_had_open_come_back_on_their_own_state(self):
        r = self.out["restored"]
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-5"])
        self.assertEqual(r["srcs"], ["/chat?col=2&skeleton=1", "/chat?col=5&skeleton=1"], "a restored column dials as a skeleton client too: its blob's activeId is the hint")
        self.assertEqual(r["posted"], [], "no focus: each column's own state blob names its tab")
        self.assertEqual(r["blob5"], r["blob5Was"], "a restore seeds nothing: the blob is byte for byte what the page left")
        self.assertIsNone(r["blob2"], "…and writes none where there was none (the shim's hint is then empty: the kernel serves the column whole)")
        self.assertEqual(r["order"][:5], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-5", "chat-pane-5"])
        self.assertEqual(r["lastPane"], "chat-pane-5")
        self.assertEqual(r["nextNumber"], "f-chat-3", "a new column takes the lowest free number")

    def test_the_phone_never_splits(self):
        m = self.out["mobile"]
        self.assertEqual(m["ids"], ["f-chat"])
        self.assertIsNone(m["opened"])
        self.assertEqual(m["notify"], [["warn", "The phone shows one pane at a time — no split here."]])
        self.assertFalse(m["canSplit"])
        self.assertEqual(m["order"], ["chat-pane", "gv-a", "fleet-pane", "gv-b", "feed-pane"])


if __name__ == "__main__":
    unittest.main()
