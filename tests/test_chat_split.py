#!/usr/bin/env python3
"""Chat columns (the user 2026-09-08: several sessions open at once instead of tabbing through them; reworked
2026-09-11 into columns that PARTITION the sessions). The dashboard shell can hold N chat COLUMNS: every column
past the first is a client-made twin of #chat-pane around an iframe at /chat?col=N&skeleton=1 (_LANDING_SPLIT_JS),
a full chat page (its own socket, state blob, drafts and scroll) FILTERED by one shell-owned fact: which sessions
each later column holds, persisted per browser as {v:2, cols:[{n, ids}]}; the first column holds the rest. Two
halves are checked here:

  * SOURCE PINS against the served shell + shim: the column id reaches the state key and the connect query,
    the shell's bridges (picker lift, editor selection, the bell's "session in front", the Log's connection
    tracking, Alt+Arrow, Escape, the gutters) know about later columns, the rail carries no split button, the
    kernel stamps the column and serves a later column as a skeleton client, the partition's functions exist
    and the owner lookup reads no pane's DOM, the parked reveal is addressed and forwarded.
  * EXECUTED: the real _LANDING_SPLIT_JS runs in node against a DOM stub (the test_error_center.py pattern)
    and the whole story is driven — the v1 migration, a move to a new column (the store, the halves, the blob
    seed, no focus posted), a move between columns closing the emptied one, the owner lookup, the emptiness
    message, another dashboard tab's write reconciled, the cross returning sessions home with their drafts,
    drafts travelling on a move, a created session claimed once and never stolen, the restore's seeding, the
    cap, and nothing on the phone.

Synthetic only — invented sids, no network, no real DOM.
"""
import inspect
import json
import os
import re
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

WEB = "11111111-2222-3333-4444-555555555501"
API = "11111111-2222-3333-4444-555555555502"
TESTS = "11111111-2222-3333-4444-555555555503"
X = "11111111-2222-3333-4444-555555555509"


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
                           "grow at boot, and a move to a new column from a hidden chat group calls __rompPaneToggle")
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
        # the two key strings are ONE string: the shell's prefix is the shim's key plus the shim's separator
        shim_key = re.search(r'var SK="([^"]+)"\+\(COL\?"(:)"\+COL:""\);', self.shim)
        shell_key = re.search(r"var BK='([^']+)';", km._LANDING_SPLIT_JS)
        self.assertEqual(shim_key.group(1) + shim_key.group(2), shell_key.group(1))
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
        # the hand-over is GONE: the seeded blob's activeId is the page's wantActive, so no focus is posted into a NEW frame;
        # the one load listener a new frame may carry hands over the moved tab's drafts, never a focus
        split = km._LANDING_SPLIT_JS
        self.assertNotIn("own:true", split)
        self.assertIn("if(state)f.addEventListener('load',function(){adopt(f,sid,state);state=null;});", split)
        self.assertEqual(split.count("addEventListener('load'"), 1)

    def test_the_rail_carries_no_split_button(self):
        # the move opens from a tab's menu ("Move to a new column") and the palette; a bottom-bar button for it
        # read as clutter (the user 2026-09-08), so the rail and the mobile bar carry none
        self.assertNotIn("rail-split", self.html)
        self.assertNotIn("rail-split", km._LANDING_SPLIT_JS)
        self.assertIn("m.romp==='openSplit'", km._LANDING_SPLIT_JS, "the tab menu's ask is a door (until the drag lands)")
        self.assertIn("if(typeof m.sid==='string'&&m.sid)moveTab(m.sid,'new');", km._LANDING_SPLIT_JS, "…and it is the one mutation, not an empty column")

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
        # a passage selected in the feed's viewer lands in the column holding that session, else the one last used
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
        # …and a new column takes HALF the rightmost one (2026-09-11), through the gutters' own normalisation
        # (tests/test_pane_gutters.py runs it); the split calls it with the rightmost pane and the new key
        self.assertIn("window.__rompSplitGrow=function(leftId,newKey){", gut)
        split = km._LANDING_SPLIT_JS
        self.assertIn("if(window.__rompSplitGrow)window.__rompSplitGrow(lastPane(),'chat'+n);", split)
        self.assertIn("if(window.__rompGrowFairIfNew)window.__rompGrowFairIfNew('chat'+n);", split)
        # a refused move says why (the acknowledgement rule), and the tab menu can ask first
        self.assertIn("function canSplit(){return !mobile()&&cols.length+1<MAX;}", split)
        self.assertIn("window.__rompCanSplit=canSplit;", split)
        self.assertIn("Four chat columns at most", split)
        self.assertIn("The phone shows one pane at a time", split)
        self.assertIn("This session is already alone in its column.", split)
        # no hand-over focus (2026-09-11): the seeded blob names the tab, and a later reload of the frame keeps the tab
        # its own state names — the same key, written by the page itself from then on
        self.assertNotIn("{once:true}", split)
        self.assertNotIn("own:true", split)

    def test_the_partition_s_functions_exist_and_the_owner_lookup_reads_no_pane_s_dom(self):
        split = km._LANDING_SPLIT_JS
        # the store's shape and its one-shot migration
        self.assertIn("JSON.stringify({v:2,cols:cols.map(function(c){return {n:c.n,ids:c.ids.slice()};})})", split)
        self.assertIn("if(Array.isArray(raw)){migrated=true;", split, "a v1 array of numbers is read once more…")
        self.assertIn("if(r0.migrated)save();", split, "…and written back in the new shape")
        # the three pure readers, the sets the pages read, the one mutation, the claim
        for needle in ["function ownerOf(sid){", "function sets(){", "function nextNumber(){",
                       "window.__rompChatSets=function(){return mobile()?null:sets();};",
                       "window.__rompCanSplit=canSplit;window.__rompMoveTab=moveTab;",
                       "window.__rompClaimSession=function(sid,col){", "function moveTab(sid,to){"]:
            self.assertIn(needle, split, needle)
        # the owner lookup is ONE lookup: target() reads the entries, never a pane's active tab; the DOM read survives
        # for the palette's "move this session" alone
        target = re.search(r"function target\(sid\)\{.*?\}\n", split).group(0)
        self.assertNotIn("activeIn(", target)
        self.assertIn("frameOfCol(ownerOf(sid))", target)
        self.assertEqual(split.count("activeIn("), 2, "defined once, called once (the palette's move of the focused column's tab)")
        self.assertIn("activeIn(focused())", split)
        # the emptiness message, the drafts hand-off and the other dashboard tab's write
        self.assertIn("m.romp==='colEmpty'&&Array.isArray(m.gone)", split)
        self.assertIn("f.contentWindow.postMessage({romp:'adopt',sid:sid,state:state},'*');", split)
        self.assertIn("__rompTakeSessionState", split)
        self.assertIn("window.addEventListener('storage',function(e){if(!e||e.key!==CK||mobile())return;var r=read();if(!r.migrated)reconcile(r.cols);});", split)
        # the cross's title reads as what it does now
        self.assertIn("x.title='Close this column';", split)

    def test_the_parked_push_tap_reveal_is_addressed_to_the_client_that_consumes_it_and_forwarded_by_the_page(self):
        # one chat client consumes the parked tap (_consume_pending_reveal), so it rides `own`; under the partition the
        # consuming page hands a tap for a session another column holds to that column, without `own` (render.ts)
        src = inspect.getsource(km._consume_pending_reveal)
        self.assertIn('m["own"] = True', src)
        self.assertIn("posts the same message WITHOUT `own` into", src)
        self.assertIn("one hop by construction", src)
        self.assertNotIn("a state the split allows on purpose", src, "two columns on one session is no longer a state the split allows")
        self.assertIn("client[\"send\"](json.dumps(m))", src)


# ── the split script, RUN ────────────────────────────────────────────────────────────────────────────
HARNESS = r"""
'use strict';
let STORE = {};
const CALLS = { register: [], unregister: [], growFair: [], splitGrow: [], gutter: [], wireFocus: [], wireEsc: [], colGone: [], events: [], posted: [], focus: [], notify: [], toggle: [], taken: [], sets: [] };
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = String(v); CALLS.sets.push(k); }, removeItem: (k) => { delete STORE[k]; } };
let BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
let FOCUSED = 'f-chat';   // what the shell's focus script would report as the column last worked in
let MOBILE = false;       // whether #mtabs is displayed (the phone layout)
let BYID = {};
let WL = {};
let TAKE = {};            // frame id → sid → what that page holds for the session (its __rompTakeSessionState answer)
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
    el.contentWindow = {
      postMessage(m) { CALLS.posted.push({ id: el.id, m }); }, focus() { CALLS.focus.push(el.id); },
      __rompTakeSessionState(sid) { const held = TAKE[el.id] && TAKE[el.id][sid]; CALLS.taken.push([el.id, sid, !!held]); if (!held) return null; delete TAKE[el.id][sid]; return held; },
    };
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
global.__rompSplitGrow = (left, key) => { CALLS.splitGrow.push([left, key]); return true; };   // the gutters' halving (tests/test_pane_gutters.py runs the real one)
global.__rompNotify = (kind, text) => CALLS.notify.push([kind, text]);
global.__rompPaneToggle = (k, to) => CALLS.toggle.push([k, to]);
global.__rompGutter = (gid, leftPick, rightId) => CALLS.gutter.push({ gid, leftPick, rightId });
global.__rompWireFocus = (f) => CALLS.wireFocus.push(f.id);
global.__rompWireEsc = (f) => CALLS.wireEsc.push(f.id);
global.__rompColGone = (c) => CALLS.colGone.push(c);
global.__rompFocusedChatId = () => FOCUSED;
function boot(store, mobile) {
  STORE = Object.assign({}, store || {}); MOBILE = !!mobile; BYID = {}; WL = {}; TAKE = {}; FOCUSED = 'f-chat'; BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
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
function ids() { return window.__rompChatFrameIds(); }
function cols() { return JSON.parse(STORE['romp-chat-cols'] || 'null'); }
function blob(n) { return JSON.parse(STORE['romp-vscode-state-chat:' + n] || 'null'); }
function saves() { return CALLS.sets.filter((k) => k === 'romp-chat-cols').length; }
function tgt(sid) { const f = window.__rompChatTarget(sid); return f ? f.id : null; }
function crossOf(fid) { return BYID[fid].parentElement.children.filter((c) => c.className === 'col-x')[0]; }
function msg(data, fromId) { window.dispatchEvent({ type: 'message', data, source: fromId ? BYID[fromId].contentWindow : null }); }
"""

DRIVER = r"""
const out = {};
const WEB = '11111111-2222-3333-4444-555555555501', API = '11111111-2222-3333-4444-555555555502', TESTS = '11111111-2222-3333-4444-555555555503', X = '11111111-2222-3333-4444-555555555509';
// A) a fresh desktop dashboard: one column, nothing made, every session the first column's
boot({}, false);
out.fresh = { ids: ids(), order: order(), lastPane: window.__rompLastChatPane(), sets: window.__rompChatSets(), stored: STORE['romp-chat-cols'] || null,
              targetUnknown: tgt(X), targetNone: tgt(''), saves: saves() };
// B) the v1 store (a bare array of column numbers) migrates once: a number whose blob names a session becomes that
//    session's column; a number with no session is dropped; the v2 shape is written back
boot({ 'romp-chat-cols': '[2]', 'romp-vscode-state-chat:2': JSON.stringify({ activeId: WEB, scroll: 4 }) }, false);
out.migrated = { ids: ids(), stored: cols(), blob2: blob(2), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), saves: saves(), srcs: [BYID['f-chat-2'].src] };
boot({ 'romp-chat-cols': '[2,5]', 'romp-vscode-state-chat:2': JSON.stringify({ activeId: WEB }), 'romp-vscode-state-chat:5': '{}' }, false);
out.migratedDrop = { ids: ids(), stored: cols() };
// C) the moves. A session to a NEW column: the store, the halves, the blob seed, no focus posted, the ring there
boot({}, false);
const f2 = window.__rompMoveTab(API, 'new'); f2.fire('load'); f2.fire('load');
out.moved = { id: f2.id, src: f2.src, col: f2.getAttribute('data-col'), pane: f2.parentElement.id, paneCls: f2.parentElement.className, flex: f2.parentElement.style.flex,
              order: order(), stored: cols(), sets: window.__rompChatSets(), blob2: blob(2), splitGrow: CALLS.splitGrow.slice(), growFair: CALLS.growFair.slice(),
              register: CALLS.register.slice(), wireFocus: CALLS.wireFocus.slice(), wireEsc: CALLS.wireEsc.slice(),
              gutter: CALLS.gutter.map((g) => ({ gid: g.gid, left: g.leftPick(), right: g.rightId })),
              event: CALLS.events.filter((e) => e.type === 'romp-chat-cols').map((e) => ({ col: e.detail.col, open: e.detail.open, frame: e.detail.frame && e.detail.frame.id })),
              posted: CALLS.posted.slice(), focused: CALLS.focus.slice(), taken: CALLS.taken.slice(), crossTitle: crossOf('f-chat-2').title,
              targetApi: tgt(API), targetTests: tgt(TESTS), canSplit: window.__rompCanSplit(), ids: ids(), lastPane: window.__rompLastChatPane(),
              paneOf: window.__rompChatPaneOf('f-chat-2'), colOf: window.__rompColOf(f2.contentWindow), frameOfWin: window.__rompFrameOfWin(f2.contentWindow) === f2 };
// a second new column halves the RIGHTMOST one; then a move BETWEEN later columns closes the emptied origin
const f3 = window.__rompMoveTab(TESTS, 'new');
out.third = { id: f3.id, splitGrow: CALLS.splitGrow.slice(), stored: cols(), gutterLeft3: CALLS.gutter.filter((g) => g.gid === 'gv-chat-3')[0].leftPick(), order: order() };
CALLS.posted = []; CALLS.focus = []; CALLS.unregister = []; CALLS.colGone = []; CALLS.taken = [];
const t3 = window.__rompMoveTab(API, 3);
out.movedInto3 = { target: t3 && t3.id, ids: ids(), order: order(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), focused: CALLS.focus.slice(),
                   unregister: CALLS.unregister.slice(), colGone: CALLS.colGone.slice(), taken: CALLS.taken.slice(),
                   targetApi: tgt(API), targetTests: tgt(TESTS), targetWeb: tgt(WEB),
                   closedEvent: CALLS.events.filter((e) => e.type === 'romp-chat-cols' && e.detail.open === false).map((e) => e.detail.col) };
FOCUSED = 'f-chat-77'; out.movedInto3.stale = tgt('');   // a stale focus id (its column is gone): the first column
FOCUSED = 'f-chat-3'; out.movedInto3.focusedNone = tgt('');   // no session named: the column last worked in
FOCUSED = 'f-chat';
// …and HOME (the first column, which derives): the origin keeps its other member
CALLS.posted = []; CALLS.focus = [];
const t1 = window.__rompMoveTab(API, 1);
out.movedHome = { target: t1 && t1.id, ids: ids(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), focused: CALLS.focus.slice(), targetApi: tgt(API) };
// refusals, each with its line or a null: alone in its column, no such column, no session, already there
CALLS.notify = []; CALLS.posted = []; CALLS.sets = [];
out.refused = { alone: window.__rompMoveTab(TESTS, 'new'), noSuch: window.__rompMoveTab(API, 7), noSid: window.__rompMoveTab('', 'new'),
                same: (window.__rompMoveTab(TESTS, 3) || {}).id, sameHome: (window.__rompMoveTab(API, 1) || {}).id,
                notify: CALLS.notify.slice(), stored: cols(), posted: CALLS.posted.length, saves: saves() };
// the palette's move: nothing active says so; the focused column's active tab moves to a new column (the lowest free number)
CALLS.notify = []; BYID['f-chat']._active = '';
out.paletteNone = { r: window.__rompSplitChat(), notify: CALLS.notify.slice() };
BYID['f-chat']._active = WEB; CALLS.notify = []; BODY_CLASSES.delete('po-chat'); CALLS.toggle = [];
const fp = window.__rompSplitChat();
out.paletteMove = { id: fp && fp.id, stored: cols(), notify: CALLS.notify.slice(), toggle: CALLS.toggle.slice(), blob2: blob(2) };
BODY_CLASSES.add('po-chat');
// D) EMPTINESS: a column's page says which members the kernel no longer lists; the entry is pruned, and closes only when empty
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2);
CALLS.sets = []; CALLS.unregister = [];
msg({ romp: 'colEmpty', gone: [TESTS] }, 'f-chat-2');
out.colEmptyPartial = { ids: ids(), stored: cols(), saves: saves() };
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat');   // from a page with no entry (the first column): ignored
out.colEmptyIgnored = { ids: ids(), stored: cols() };
window.__rompMoveTab(X, 2);   // a member added meanwhile…
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat-2');   // …keeps the column open when the others go
out.colEmptyKept = { ids: ids(), stored: cols() };
msg({ romp: 'colEmpty', gone: [X] }, 'f-chat-2');
out.colEmptyAll = { ids: ids(), stored: cols(), unregister: CALLS.unregister.slice() };
// E) another dashboard tab's write reconciles: what it added is made (seeded like a restore), what it dropped closes, nothing is written back
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] }) }, false);
CALLS.sets = []; CALLS.posted = [];
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }, { n: 4, ids: [API, TESTS] }] });
STORE['romp-vscode-state-chat:4'] = JSON.stringify({ activeId: TESTS, scroll: 9 });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciled = { ids: ids(), order: order(), sets: window.__rompChatSets(), saves: saves(), blob4: blob(4), stored: STORE['romp-chat-cols'], posted: CALLS.posted.slice() };
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 4, ids: [API, TESTS] }] });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciledClose = { ids: ids(), sets: window.__rompChatSets(), saves: saves(), unregister: CALLS.unregister.slice() };
window.dispatchEvent({ type: 'storage', key: 'romp-pane-grow' });   // another key: nothing
out.reconciledOther = { ids: ids() };
// F) the CROSS returns the column's sessions home, drafts and all
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2);
TAKE['f-chat-2'] = { [API]: { draft: 'a draft for api', citations: [], files: [], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
crossOf('f-chat-2').fire('click', { stopPropagation() {} });
out.cross = { ids: ids(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), targetApi: tgt(API), targetTests: tgt(TESTS) };
// G) DRAFTS TRAVEL on a move: to a new column on its load, once; into an open column at once, ahead of the focus
boot({}, false);
TAKE['f-chat'] = { [TESTS]: { draft: 'typed in column one', citations: [{ title: 'a card' }], files: ['/tmp/a.png'], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
const fn2 = window.__rompMoveTab(TESTS, 'new');
out.draftsNew = { id: fn2.id, postedBeforeLoad: CALLS.posted.slice(), taken: CALLS.taken.slice() };
fn2.fire('load'); out.draftsNew.postedAfterLoad = CALLS.posted.slice();
fn2.fire('load'); out.draftsNew.postedAfterSecondLoad = CALLS.posted.length;
const fo = window.__rompMoveTab(WEB, 'new');   // column 3, holding WEB
CALLS.posted = []; CALLS.taken = [];
TAKE['f-chat-2'] = { [TESTS]: { draft: 'more', citations: [], files: [], staged: [{ text: 's', cites: [] }] } };
const to3 = window.__rompMoveTab(TESTS, 3);
out.draftsOpen = { target: to3 && to3.id, posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), ids: ids(), stored: cols() };
// H) a session CREATED from a later column is claimed for it, once, and never stolen from a column that lists it
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(WEB, 'new');
out.claim = { first: window.__rompClaimSession(X, 2), again: window.__rompClaimSession(X, 2), steal: window.__rompClaimSession(X, 3), listed: window.__rompClaimSession(API, 3),
              noSid: window.__rompClaimSession('', 2), noCol: window.__rompClaimSession(TESTS, 9), stored: cols(), targetX: tgt(X) };
// I) the RESTORE seeds each column's blob with the tab it names when that is still a member, else its first member
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB, API] }, { n: 5, ids: [TESTS] }] }), 'romp-vscode-state-chat:2': JSON.stringify({ activeId: API, scroll: 3 }) }, false);
out.restored = { ids: ids(), order: order(), blob2: blob(2), blob5: blob(5), posted: CALLS.posted.slice(), sets: window.__rompChatSets(), saves: saves(),
                 srcs: [BYID['f-chat-2'].src, BYID['f-chat-5'].src], lastPane: window.__rompLastChatPane(), growFair: CALLS.growFair.slice(), splitGrow: CALLS.splitGrow.slice(),
                 nextNumber: (window.__rompMoveTab(X, 'new') || {}).id };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB, API] }] }), 'romp-vscode-state-chat:2': JSON.stringify({ activeId: TESTS }) }, false);
out.restoredMovedAway = { blob2: blob(2) };
// a corrupt store: nothing made, nothing thrown
boot({ 'romp-chat-cols': 'not json{' }, false);
out.corrupt = { ids: ids(), stored: STORE['romp-chat-cols'] };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [] }, { n: 'x', ids: [WEB] }, { n: 3, ids: [API, API] }, { n: 4, ids: [API] }] }) }, false);
out.sanitised = { ids: ids(), sets: window.__rompChatSets() };
// J) four columns at most
boot({}, false);
window.__rompMoveTab(WEB, 'new'); window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new');
CALLS.notify = [];
out.cap = { ids: ids(), fourth: window.__rompMoveTab(X, 'new'), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit(), stored: cols() };
// K) the phone: nothing is restored, the one chat filters nothing, a move is refused, the store keeps the desktop's arrangement
const PHONE_STORE = JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] });
boot({ 'romp-chat-cols': PHONE_STORE }, true);
out.mobile = { ids: ids(), sets: window.__rompChatSets(), moved: window.__rompMoveTab(WEB, 'new'), order: order(), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit(),
               stored: STORE['romp-chat-cols'], storedWas: PHONE_STORE, saves: saves(), target: tgt(WEB) };
console.log(JSON.stringify(out));
"""


class SplitExecutes(unittest.TestCase):
    maxDiff = None

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

    def test_a_fresh_dashboard_has_one_column_that_holds_everything(self):
        o = self.out["fresh"]
        self.assertEqual(o["ids"], ["f-chat"])
        self.assertEqual(o["order"], ["chat-pane", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(o["lastPane"], "chat-pane")
        self.assertEqual(o["sets"], {}, "no later columns: the pages filter nothing")
        self.assertIsNone(o["stored"], "nothing written until something changes")
        self.assertEqual(o["saves"], 0)
        self.assertEqual(o["targetUnknown"], "f-chat", "a session no entry lists is the first column's")
        self.assertEqual(o["targetNone"], "f-chat", "no session named: the column last worked in, here the first")

    def test_a_v1_store_migrates_once_to_the_sessions_the_blobs_named(self):
        m = self.out["migrated"]
        self.assertEqual(m["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(m["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}]}, "written back in the new shape")
        self.assertEqual(m["saves"], 1, "…once")
        self.assertEqual(m["sets"], {"2": [WEB]})
        self.assertEqual(m["blob2"], {"activeId": WEB, "scroll": 4}, "the blob is re-seeded with the same tab: every other field kept")
        self.assertEqual(m["posted"], [], "no focus, no adopt: the blob names the tab")
        self.assertEqual(m["srcs"], ["/chat?col=2&skeleton=1"])
        d = self.out["migratedDrop"]
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2"], "a number whose blob names no session is dropped")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}]})

    def test_a_move_to_a_new_column_makes_a_wired_column_holding_the_session_alone(self):
        o = self.out["moved"]
        self.assertEqual(o["id"], "f-chat-2")
        self.assertEqual(o["src"], "/chat?col=2&skeleton=1", "its own state blob + connect params (the shim); a skeleton client of its session")
        self.assertEqual(o["col"], "2")
        self.assertEqual(o["pane"], "chat-pane-2")
        self.assertEqual(o["paneCls"], "pane chat-col")
        self.assertEqual(o["flex"], "var(--g-chat2,60) 1 0", "its own grow var, the gutters' store")
        self.assertEqual(o["order"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]})
        self.assertEqual(o["sets"], {"2": [API]}, "what every column page filters by")
        self.assertEqual(o["blob2"], {"activeId": API}, "the blob names the session BEFORE the frame exists: the shim's hint, the page's wantActive")
        self.assertEqual(o["splitGrow"], [["chat-pane", "chat2"]], "the rightmost column and the new one each take half its width")
        self.assertEqual(o["growFair"], ["chat2"], "then the store-aware hook finds the half and keeps it")
        self.assertEqual(o["register"], [["chat-pane-2", "chat2"]])
        self.assertEqual(o["wireFocus"], ["f-chat-2"])
        self.assertEqual(o["wireEsc"], ["f-chat-2"])
        self.assertEqual(o["gutter"], [{"gid": "gv-chat-2", "left": "chat-pane", "right": "chat-pane-2"}])
        self.assertEqual(o["event"], [{"col": 2, "open": True, "frame": "f-chat-2"}], "palette-main wires its chords on this")
        self.assertEqual(o["posted"], [], "no hand-over focus and, with nothing held for the session, no adopt — though load fired twice")
        self.assertEqual(o["taken"], [["f-chat", API, False]], "the source page was asked for the session's drafts, once")
        self.assertEqual(o["focused"], ["f-chat-2"], "the new column takes the keyboard")
        self.assertEqual(o["crossTitle"], "Close this column")
        self.assertEqual(o["targetApi"], "f-chat-2", "a focus for the session belongs to the column holding it")
        self.assertEqual(o["targetTests"], "f-chat", "…and one for a session no entry lists to the first column")
        self.assertTrue(o["canSplit"])
        self.assertEqual(o["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(o["lastPane"], "chat-pane-2", "gv-a's left neighbour is now the new column")
        self.assertEqual(o["paneOf"], "chat-pane-2")
        self.assertEqual(o["colOf"], "2")
        self.assertTrue(o["frameOfWin"])

    def test_a_move_between_columns_lands_in_the_target_and_closes_an_emptied_origin(self):
        t = self.out["third"]
        self.assertEqual(t["id"], "f-chat-3")
        self.assertEqual(t["splitGrow"], [["chat-pane", "chat2"], ["chat-pane-2", "chat3"]], "the second new column halves the RIGHTMOST column")
        self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}, {"n": 3, "ids": [TESTS]}]})
        self.assertEqual(t["gutterLeft3"], "chat-pane-2")
        m = self.out["movedInto3"]
        self.assertEqual(m["target"], "f-chat-3", "the target's iframe comes back")
        self.assertEqual(m["ids"], ["f-chat", "f-chat-3"], "column 2 held only the moved session: it closed")
        self.assertEqual(m["order"], ["chat-pane", "gv-chat-3", "chat-pane-3", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(m["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS, API]}]}, "the id joins the target's entry; the emptied entry is gone whole")
        self.assertEqual(m["sets"], {"3": [TESTS, API]})
        self.assertEqual(m["posted"], [{"id": "f-chat-3", "m": {"type": "focus", "id": API}}], "a plain focus into the target: it is the owner now, so its own gate takes it; no `own`")
        self.assertEqual(m["taken"], [["f-chat-2", API, False]], "the drafts were asked of the SOURCE column")
        self.assertEqual(m["unregister"], ["chat-pane-2"], "the closed column's grow leaves the store")
        self.assertEqual(m["colGone"], ["2"], "the Log drops its connection state")
        self.assertEqual(m["closedEvent"], [2])
        self.assertEqual(m["focused"][-1], "f-chat-3", "the ring ends on the target, not on the closed origin's neighbour")
        self.assertEqual(m["targetApi"], "f-chat-3")
        self.assertEqual(m["targetTests"], "f-chat-3")
        self.assertEqual(m["targetWeb"], "f-chat")
        self.assertEqual(m["stale"], "f-chat", "a stale focus id falls back to the first column")
        self.assertEqual(m["focusedNone"], "f-chat-3", "no session named: the column last worked in")
        h = self.out["movedHome"]
        self.assertEqual(h["target"], "f-chat", "the first column derives: the id simply leaves its entry")
        self.assertEqual(h["ids"], ["f-chat", "f-chat-3"], "the origin keeps its other member")
        self.assertEqual(h["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}]})
        self.assertEqual(h["sets"], {"3": [TESTS]})
        self.assertEqual(h["posted"], [{"id": "f-chat", "m": {"type": "focus", "id": API}}])
        self.assertEqual(h["focused"], ["f-chat"])
        self.assertEqual(h["targetApi"], "f-chat")

    def test_a_refused_move_says_why_or_changes_nothing(self):
        r = self.out["refused"]
        self.assertIsNone(r["alone"], "a session alone in a later column has nowhere new to go")
        self.assertIsNone(r["noSuch"], "no such column")
        self.assertIsNone(r["noSid"])
        self.assertEqual(r["same"], "f-chat-3", "already there: the owner's frame, nothing moves")
        self.assertEqual(r["sameHome"], "f-chat")
        self.assertEqual(r["notify"], [["warn", "This session is already alone in its column."]], "the one refusal that is a gesture with nothing to do says so")
        self.assertEqual(r["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}]}, "the store is untouched")
        self.assertEqual(r["posted"], 0)
        self.assertEqual(r["saves"], 0, "no write for a refusal or a no-op")
        p = self.out["paletteNone"]
        self.assertIsNone(p["r"])
        self.assertEqual(p["notify"], [["warn", "No session is open in this column to move."]])
        q = self.out["paletteMove"]
        self.assertEqual(q["id"], "f-chat-2", "the focused column's active tab moves to a new column, the lowest free number")
        self.assertEqual(q["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}, {"n": 2, "ids": [WEB]}]}, "row order, not number order")
        self.assertEqual(q["notify"], [])
        self.assertEqual(q["toggle"], [["chat", True]], "a hidden chat group is brought forward first")
        self.assertEqual(q["blob2"], {"activeId": WEB})

    def test_a_column_s_emptiness_prunes_its_entry_and_closes_it_only_when_nothing_is_left(self):
        p = self.out["colEmptyPartial"]
        self.assertEqual(p["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(p["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "the gone id leaves the entry")
        self.assertEqual(p["saves"], 1)
        self.assertEqual(self.out["colEmptyIgnored"]["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "a page with no entry says nothing that changes anything")
        k = self.out["colEmptyKept"]
        self.assertEqual(k["ids"], ["f-chat", "f-chat-2"], "a member added meanwhile keeps the column open")
        self.assertEqual(k["stored"], {"v": 2, "cols": [{"n": 2, "ids": [X]}]})
        a = self.out["colEmptyAll"]
        self.assertEqual(a["ids"], ["f-chat"], "the last member gone: the column closes")
        self.assertEqual(a["stored"], {"v": 2, "cols": []})
        self.assertEqual(a["unregister"], ["chat-pane-2"])

    def test_another_dashboard_tab_s_write_is_reconciled_without_writing_back(self):
        r = self.out["reconciled"]
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-4"], "the column the other tab added is made here")
        self.assertEqual(r["order"][:5], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(r["sets"], {"2": [WEB], "4": [API, TESTS]})
        self.assertEqual(r["saves"], 0, "the other tab's store is the truth: nothing is written back")
        self.assertEqual(r["blob4"], {"activeId": TESTS, "scroll": 9}, "seeded like a restore: the blob's tab, a member, stands")
        self.assertEqual(r["posted"], [])
        c = self.out["reconciledClose"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-4"], "the column the other tab dropped closes here")
        self.assertEqual(c["sets"], {"4": [API, TESTS]})
        self.assertEqual(c["saves"], 0)
        self.assertEqual(c["unregister"], ["chat-pane-2"])
        self.assertEqual(self.out["reconciledOther"]["ids"], ["f-chat", "f-chat-4"], "another key's event changes nothing")

    def test_the_cross_returns_the_column_s_sessions_to_the_first_column_with_their_drafts(self):
        c = self.out["cross"]
        self.assertEqual(c["ids"], ["f-chat"])
        self.assertEqual(c["stored"], {"v": 2, "cols": []}, "the entry goes whole: the first column derives both sessions")
        self.assertEqual(c["sets"], {})
        self.assertEqual(c["taken"], [["f-chat-2", API, True], ["f-chat-2", TESTS, False]], "the closing page is asked for each member's drafts")
        self.assertEqual(c["posted"], [{"id": "f-chat", "m": {"romp": "adopt", "sid": API, "state": {"draft": "a draft for api", "citations": [], "files": [], "staged": []}}}],
                         "what it held lands in the first column's page; a session with nothing held posts nothing")
        self.assertEqual(c["targetApi"], "f-chat")
        self.assertEqual(c["targetTests"], "f-chat")

    def test_drafts_travel_with_a_moved_tab(self):
        n = self.out["draftsNew"]
        self.assertEqual(n["id"], "f-chat-2")
        self.assertEqual(n["taken"], [["f-chat", TESTS, True]], "taken from the source, synchronously, before the frame exists")
        self.assertEqual(n["postedBeforeLoad"], [], "a new frame cannot hear yet")
        self.assertEqual(n["postedAfterLoad"], [{"id": "f-chat-2", "m": {"romp": "adopt", "sid": TESTS,
                                                  "state": {"draft": "typed in column one", "citations": [{"title": "a card"}], "files": ["/tmp/a.png"], "staged": []}}}],
                         "…and adopts on its load")
        self.assertEqual(n["postedAfterSecondLoad"], 1, "once: a later reload of the frame has them in its own blob")
        o = self.out["draftsOpen"]
        self.assertEqual(o["target"], "f-chat-3")
        self.assertEqual(o["taken"], [["f-chat-2", TESTS, True]])
        self.assertEqual(o["posted"], [{"id": "f-chat-3", "m": {"romp": "adopt", "sid": TESTS, "state": {"draft": "more", "citations": [], "files": [], "staged": [{"text": "s", "cites": []}]}}},
                                       {"id": "f-chat-3", "m": {"type": "focus", "id": TESTS}}], "an open column adopts at once, ahead of the focus that shows the tab")
        self.assertEqual(o["ids"], ["f-chat", "f-chat-3"], "the origin, left empty, closed")
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 3, "ids": [WEB, TESTS]}]})

    def test_a_created_session_is_claimed_for_its_column_once_and_never_stolen(self):
        c = self.out["claim"]
        self.assertTrue(c["first"], "a session no entry lists joins the creating column")
        self.assertFalse(c["again"], "…once")
        self.assertFalse(c["steal"], "a session another column lists is never taken")
        self.assertFalse(c["listed"])
        self.assertFalse(c["noSid"])
        self.assertFalse(c["noCol"])
        self.assertEqual(c["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API, X]}, {"n": 3, "ids": [WEB]}]})
        self.assertEqual(c["targetX"], "f-chat-2")

    def test_the_restore_seeds_each_column_with_a_member_and_reads_a_bad_store_forgivingly(self):
        r = self.out["restored"]
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-5"])
        self.assertEqual(r["order"][:5], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-5", "chat-pane-5"])
        self.assertEqual(r["blob2"], {"activeId": API, "scroll": 3}, "the blob's tab is still a member: it stands, every other field kept")
        self.assertEqual(r["blob5"], {"activeId": TESTS}, "no blob: seeded with the first member")
        self.assertEqual(r["srcs"], ["/chat?col=2&skeleton=1", "/chat?col=5&skeleton=1"], "a restored column dials as a skeleton client of the seeded tab")
        self.assertEqual(r["posted"], [], "no focus, no adopt at a restore")
        self.assertEqual(r["sets"], {"2": [WEB, API], "5": [TESTS]})
        self.assertEqual(r["saves"], 0, "a v2 store is not rewritten at boot")
        self.assertEqual(r["growFair"], ["chat2", "chat5"], "a restored column takes its stored width, else the fair average")
        self.assertEqual(r["splitGrow"], [], "…never the halving, which is a move's")
        self.assertEqual(r["lastPane"], "chat-pane-5")
        self.assertEqual(r["nextNumber"], "f-chat-3", "a new column takes the lowest free number")
        self.assertEqual(self.out["restoredMovedAway"]["blob2"], {"activeId": WEB}, "the blob's tab was moved away while the browser was closed: the first member")
        self.assertEqual(self.out["corrupt"]["ids"], ["f-chat"], "a corrupt store makes nothing and throws nothing")
        s = self.out["sanitised"]
        self.assertEqual(s["ids"], ["f-chat", "f-chat-3"], "an empty entry, a non-numeric number and an entry whose ids were all claimed earlier are dropped")
        self.assertEqual(s["sets"], {"3": [API]}, "an id is in one entry, once")

    def test_four_columns_at_most(self):
        c = self.out["cap"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"])
        self.assertIsNone(c["fourth"])
        self.assertEqual(c["notify"], [["warn", "Four chat columns at most — close one to open another."]], "a refused move says why")
        self.assertFalse(c["canSplit"], "…and the tab menu can ask before offering the item")
        self.assertEqual(c["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}, {"n": 3, "ids": [API]}, {"n": 4, "ids": [TESTS]}]})

    def test_the_phone_never_splits_and_filters_nothing(self):
        m = self.out["mobile"]
        self.assertEqual(m["ids"], ["f-chat"])
        self.assertIsNone(m["sets"], "null sets: the one chat shows everything")
        self.assertIsNone(m["moved"])
        self.assertEqual(m["notify"], [["warn", "The phone shows one pane at a time — no split here."]])
        self.assertFalse(m["canSplit"])
        self.assertEqual(m["order"], ["chat-pane", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(m["stored"], m["storedWas"], "the desktop's arrangement stays in the store")
        self.assertEqual(m["saves"], 0)
        self.assertEqual(m["target"], "f-chat")


if __name__ == "__main__":
    unittest.main()
