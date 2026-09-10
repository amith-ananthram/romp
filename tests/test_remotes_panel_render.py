#!/usr/bin/env python3
"""The remotes popover's host list must actually RENDER.

`pmode` (peer-bus mode) is computed inside refresh()'s fetch callback, but render() read it as a FREE
variable — it is not in that scope. So every render threw ReferenceError right after `list.innerHTML=''`
had cleared the list and before any row was appended, and the bare `.catch(){}` swallowed it. The panel
showed an empty host list no matter how many remotes were attached, indistinguishable from "none
attached", and survived reloads and kernel restarts (the user 2026-07-22 — hours of misdiagnosis).

Source pins can't catch that class of bug, so this EXECUTES the real injected panel JS in node against a
DOM stub, drives the refresh with one attached host, and asserts a row lands in the list. Any
ReferenceError, typo, or scope slip in that path fails the test.

Synthetic only — placeholder host/token, no network (fetch is stubbed).
"""
import json
import time
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_rpanel", os.path.join(BIN, "romp-kernel"))

TUNNELS = {
    "tunnels": [{
        "host": "TESTHOST", "kernelPort": 29855, "localPort": 51000, "busPort": 51001,
        "checkin": False, "checkinPeer": False, "hasToken": True, "status": "up", "detail": "",
        "sids": ["11111111-2222-3333-4444-555555555555"], "trust": "directed",
        "kernelSha": "abc1234", "localSha": "abc1234", "outOfDate": False,
        "behindBy": 0, "aheadBy": 0, "kernelDate": "",
        "gaveUp": False, "fails": 0, "maxTries": 5,
    }],
    "known": [],
    "peersMode": True,
}

# Minimal DOM/browser stub: enough for the panel IIFE to wire itself up and run one refresh.
HARNESS = r"""
'use strict';
const HTML_SETS = [];           // every string any element was given as innerHTML, in order (the markup sinks)
function mkEl(id){
  return {id:id, hidden:true, _text:'', title:'', style:{}, value:'', className:'',
    children:[], _listeners:{}, _html:'',
    // Assigning innerHTML replaces an element's contents, so it must drop appended children too. Without
    // that, render()'s opening `list.innerHTML=''` left the previous pass's rows in place and every
    // refresh doubled the list.
    get innerHTML(){return this._html;}, set innerHTML(v){this._html=v; this.children=[]; HTML_SETS.push(String(v));},
    // …and so does assigning textContent (the DOM's rule) — the option lists clear themselves that way
    get textContent(){return this._text;}, set textContent(v){this._text=v; this.children=[];},
    classList:{_s:new Set(), add(){}, remove(){}, toggle(){}, contains(){return false;}},
    appendChild(c){this.children.push(c); return c;},
    querySelector(){return null;}, querySelectorAll(){return [];},
    addEventListener(k,f){this._listeners[k]=f;}, removeEventListener(){},
    setAttribute(){}, getAttribute(){return null;}, focus(){}, select(){}, remove(){},
    click(){ if(this.onclick) this.onclick({stopPropagation(){}, preventDefault(){}}); },
    get firstChild(){return this.children[0]||null;}};
}
const ELS = {};
const document = {
  getElementById(id){ if(!ELS[id]) ELS[id]=mkEl(id); return ELS[id]; },
  createElement(t){ return mkEl(t); },
  querySelector(){ return null; }, querySelectorAll(){ return []; },
  addEventListener(){}, body:mkEl('body'),
};
const localStorage = { getItem(){return null;}, setItem(){} };
const TUNNELS = __TUNNELS__;
const PAIRS = __PAIRS__;        // /tunnels/pairs answer; null = the read never lands (loader-state test)
const SUB = __SUB__;            // /tunnels/of answer (a PEER's own rows); null = answer with TUNNELS as before
const POSTS = [];               // every write the panel makes, so a test can assert what Attach sent
function fetch(url, opts){
  if (opts && opts.method === 'POST') {
    POSTS.push({url:url, body:JSON.parse(opts.body || '{}')});
    return Promise.resolve({ ok:true, json(){ return Promise.resolve({ok:true}); } });
  }
  if (url.indexOf('/tunnels/pairs') >= 0) {
    if (PAIRS === null) return new Promise(function(){});
    return Promise.resolve({ ok:true, json(){ return Promise.resolve(PAIRS); } });
  }
  if (url.indexOf('/tunnels/of') >= 0 && SUB !== null) {
    return Promise.resolve({ ok:true, json(){ return Promise.resolve(SUB); } });
  }
  const body = url.indexOf('/ssh-hosts') >= 0 ? {hosts:['TESTHOST']} : TUNNELS;
  return Promise.resolve({ ok:true, json(){ return Promise.resolve(body); } });
}
const ALERTS = [];
function alert(m){ ALERTS.push(String(m)); }
const setTimeout_ = setTimeout;
// listeners are RECORDED so a test can deliver a pane's postMessage (the hostsPending row copy)
const window = { _l:{}, addEventListener(k,f){ (this._l[k]=this._l[k]||[]).push(f); }, location:{reload(){}} };
const console_err = [];
const console = { error(...a){ console_err.push(a.map(String).join(' ')); }, log(){}, warn(){} };

__PANEL_JS__

// drive it: open the panel (sets hidden=false, loads hosts, refreshes) and let the promises settle
ELS['rnet-back'].hidden = false;
window.__rompOpenNet && window.__rompOpenNet();
// A host is an ITEM of two lines, so the markup lives on the item's children rather than on the node
// appended to the list. Walk the whole subtree so this stays true of whatever shape the rows take next.
function collect(el){
  let s = String(el.className || '') + ' ' + String(el.innerHTML || el.textContent || '');
  (el.children || []).forEach(c => { s += ' ' + collect(c); });
  return s;
}
setTimeout_(() => {
  __DRIVE__                    // a test may click things here; then we let its promises settle too
  setTimeout_(() => {
    const list = ELS['rnet-list'];
    const rows = list.children.length;
    const html = list.children.map(collect).join(' | ');
    const add = ELS['rnet-add'], plus = ELS['rnet-plus'], dl = ELS['rnet-hosts'], fs = ELS['rnet-from'];
    process.stdout.write(JSON.stringify({rows:rows, html:html, errors:console_err,
      addHidden:!!add.hidden, plusHidden:!!plus.hidden,
      hosts:dl.children.map(function(o){return o.value;}), hostsHtml:String(dl.innerHTML||''),
      hostsLastDisabled:!!(dl.children.length&&dl.children[dl.children.length-1].disabled),
      fromOpts:fs.children.map(function(o){return [o.value, o.textContent];}), fromHtml:String(fs.innerHTML||''),
      htmlSets:HTML_SETS, posts:POSTS, alerts:ALERTS}), function(){process.exit(0);});
    // exit once the measurement has FLUSHED: a pipe write is asynchronous, and exit() right after it cut a
    // 600-row report mid-string (unterminated JSON); the panel re-arms its own poll timer forever otherwise
  }, 40);
}, 60);
"""


class _PanelHarness:
    """Runs the real panel JS in node against the DOM stub above and returns what it measured. A MIXIN with
    no tests of its own, so the classes below share the driver without inheriting each other's tests (a
    subclass of a TestCase re-runs every inherited test — three copies of 26 node spawns, review find)."""

    def _run(self, drive="", tunnels=None, pairs=None, sub=None):
        js = (HARNESS.replace("__PANEL_JS__", km._LANDING_REMOTES_JS)
                     .replace("__TUNNELS__", json.dumps(tunnels if tunnels is not None else TUNNELS))
                     .replace("__PAIRS__", json.dumps(pairs))
                     .replace("__SUB__", json.dumps(sub))
                     .replace("__DRIVE__", drive))
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, "panel JS crashed:\n%s" % p.stderr[-2000:])
        return json.loads(p.stdout or "{}")


class RemotesPanelRender(_PanelHarness, unittest.TestCase):
    def test_an_attached_host_renders_a_row(self):
        out = self._run()
        self.assertEqual(out.get("errors"), [], "the refresh must not report a failure")
        self.assertGreaterEqual(out.get("rows", 0), 1,
                                "an attached host must render a row (this is the pmode ReferenceError bug)")

    def test_the_row_carries_the_detach_control(self):
        # Detach is the off switch for the reconnect relationship — the whole reason the list exists
        out = self._run()
        self.assertIn("Detach", out.get("html", ""))
        self.assertIn("TESTHOST", out.get("html", ""))

    def test_render_is_given_pmode_rather_than_reading_it_free(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("function render(ts,known,pmode,via,rholds,tiers)", js)
        # pmode rides the cached args (which also let a pairs answer repaint without a new /tunnels)
        self.assertIn("_lastArgs=[ts,(d&&d.known)||[],pmode,(d&&d.viaReach)||[],(d&&d.remoteHolds)||[],(d&&d.peerTiers)||{}]", js)
        self.assertIn("render.apply(null,_lastArgs)", js)

    def test_reverse_trust_mismatch_renders_the_direction_and_a_match_button(self):
        # Both directions of the pair on one row (the user 2026-07-26): ours is the select, theirs is
        # the bus-gossiped declaration; a mismatch wears the warm tint and offers Match.
        tn = json.loads(json.dumps(TUNNELS))
        tn["peerTiers"] = {"TESTHOST": "trusted"}          # ours: directed (fixture) — half-open pair
        out = self._run(tunnels=tn)
        html = out.get("html", "")
        self.assertIn("TESTHOST holds yours: trusted", html)
        self.assertIn("rnet-mismatch", html)
        self.assertIn("Match (directed)", html)

    def test_reverse_trust_matched_pair_is_quiet_metadata_no_button(self):
        tn = json.loads(json.dumps(TUNNELS))
        tn["peerTiers"] = {"TESTHOST": "directed"}
        out = self._run(tunnels=tn)
        html = out.get("html", "")
        self.assertIn("TESTHOST holds yours: directed", html)
        self.assertNotIn("rnet-mismatch", html)
        self.assertNotIn("Match (", html)

    def test_no_tier_gossip_renders_no_reverse_line(self):
        # An older peer (or bus down) declares nothing — the row must not invent a direction.
        out = self._run()
        self.assertNotIn("holds yours", out.get("html", ""))

    def test_match_binding_posts_the_mirror_route(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("button[data-m]", js)
        self.assertIn("/tunnels/trust-mirror", js)

    # ---- remembered vs live (the user 2026-07-28) -------------------------------------------------
    # kernelSha, the drift counts and the peer's mail tier are all answers from the LAST SUCCESSFUL
    # poll — only an `up` row is polled. The panel drew them as current regardless, so a row could say
    # "disconnected" and a drift count and name the peer's mail tier all at once: three claims,
    # two of which it had no way to know. The values stay (a blank row is worse); they must be MARKED.

    def _drifted(self, status="up", stale=False, last_ok=0, tiers=None):
        tn = json.loads(json.dumps(TUNNELS))
        tn["tunnels"][0].update({"status": status, "outOfDate": True, "behindBy": 2, "aheadBy": 0,
                                 "kernelSha": "abc1234", "localSha": "def5678",
                                 # the release each side descends from, added 2026-07-30 — the row names
                                 # the BUILD (tag + commit) and puts the distance in parens after it
                                 "kernelVer": "v0.1.3", "localVer": "v0.2.0",
                                 "stale": stale, "lastOk": last_ok})
        if tiers is not None:
            tn["peerTiers"] = tiers
        return tn

    def test_a_live_row_states_its_drift_plainly(self):
        # The control: an `up` row DID just poll, so its drift is fact and wears no hedge.
        out = self._run(tunnels=self._drifted(status="up", stale=False))
        html = out.get("html", "")
        self.assertIn("(behind 2)", html)   # said in words (2026-07-30), in parens after the build
        self.assertIn("v0.1.3 abc1234", html, "the build it is actually on, named so a human can read it")
        self.assertNotIn("last known", html)
        self.assertNotIn("rnet-stale", html)

    def test_a_disconnected_row_marks_its_drift_as_remembered_not_live(self):
        out = self._run(tunnels=self._drifted(status="down", stale=True, last_ok=1785272930))
        html = out.get("html", "")
        self.assertIn("reconnecting", html, "the row still reports its live state — and says romp is on it (the 2026-08-24 auto-reconnect wording: a row's existence IS standing intent)")
        self.assertIn("last known: v0.1.3 abc1234 (behind 2)", html,
                      "a drift count from an unreachable host must read as memory, not as a finding")
        self.assertIn("rnet-stale", html, "and must carry the muted cue that overrides the accent")
        self.assertIn("last confirmed", html, "hover says WHEN it was true (progressive disclosure)")

    def test_a_never_reached_row_says_so_rather_than_inventing_a_time(self):
        # lastOk 0 = this kernel has not once seen it up; the hover must not imply a moment that never was.
        out = self._run(tunnels=self._drifted(status="gave-up", stale=True, last_ok=0))
        self.assertIn("never confirmed since this kernel started", out.get("html", ""))

    def test_a_disconnected_row_marks_the_peers_mail_tier_as_remembered(self):
        # The other half of the same bug: the reverse tier is gossiped by the peer's bus on an exchange,
        # so a disconnected row is quoting an old exchange and must say so.
        out = self._run(tunnels=self._drifted(status="down", stale=True, last_ok=1785272930,
                                              tiers={"TESTHOST": "trusted"}))
        self.assertIn("TESTHOST holds yours: last known trusted", out.get("html", ""))

    def test_a_live_row_states_the_peers_mail_tier_plainly(self):
        out = self._run(tunnels=self._drifted(status="up", stale=False, tiers={"TESTHOST": "trusted"}))
        html = out.get("html", "")
        self.assertIn("TESTHOST holds yours: trusted", html)
        self.assertNotIn("last known", html)

    def test_the_per_host_settings_sit_on_their_own_line(self):
        # Trust and check-in are set once and left; Detach is an act. Splitting them off line 1 is what
        # stops a phone-width row from pushing Detach past the right edge.
        out = self._run()
        self.assertIn("rnet-row2", out.get("html", ""))
        self.assertIn("rnet-trust", out.get("html", ""))

    def test_the_trust_options_say_what_they_mean(self):
        # A dropdown reading trusted/directed/isolated makes you hover each one to find out what it does.
        out = self._run()
        html = out.get("html", "")
        self.assertIn("directed (held for you)", html)
        self.assertIn("trusted (auto-accept)", html)
        self.assertIn("isolated (no mail)", html)

    def test_the_checkin_box_is_named_for_what_it_does(self):
        # It publishes THIS machine to the remote. Its old label, "keep connected", read as the reconnect
        # setting, so the tooltip had to spend a sentence denying that.
        out = self._run()
        self.assertIn("Share my sessions there", out.get("html", ""))
        self.assertNotIn("keep connected", out.get("html", ""))

    def test_the_add_form_is_closed_until_the_plus_is_clicked(self):
        # Progressive disclosure: the panel's subject is the hosts you have, not the act of adding one.
        shut = self._run()
        self.assertTrue(shut.get("addHidden"), "the add form must start collapsed")
        self.assertFalse(shut.get("plusHidden"), "+ Add a host must be the visible affordance")
        opened = self._run(drive="ELS['rnet-plus'].click();")
        self.assertFalse(opened.get("addHidden"), "+ must open the add form")
        self.assertTrue(opened.get("plusHidden"), "+ gives way to the form it opened")

    def test_a_host_absent_from_ssh_config_can_be_typed_and_attached(self):
        # The point of the free-text box: ssh takes any target you could type after `ssh`, so ~/.ssh/config
        # is a source of completions, not the set of machines you can reach.
        out = self._run(drive="ELS['rnet-plus'].click();"
                              "ELS['rnet-host'].value='someone@198.51.100.7';"
                              "ELS['rnet-attach'].click();")
        posts = [p for p in out.get("posts", []) if p["url"] == "/tunnels"]
        self.assertEqual([p["body"]["host"] for p in posts], ["someone@198.51.100.7"])
        self.assertEqual(out.get("alerts"), [], "a good host must not report a failure")

    def test_a_rejected_host_is_reported_rather_than_swallowed(self):
        # Typos are newly possible now that the host is typed. Fail loudly (CLAUDE.md).
        out = self._run(drive="ELS['rnet-plus'].click();"
                              "ELS['rnet-host'].value='nope';"
                              "fetch=function(u,o){if(o&&o.method==='POST')"
                              "return Promise.resolve({json(){return Promise.resolve({ok:false,error:'invalid host'});}});"
                              "return Promise.resolve({json(){return Promise.resolve(TUNNELS);}});};"
                              "ELS['rnet-attach'].click();")
        self.assertTrue(any("invalid host" in a for a in out.get("alerts", [])),
                        "a refused attach must say so: %r" % (out.get("alerts"),))

    def test_known_and_attached_hosts_both_feed_the_completions(self):
        # What makes a typed-in host stick: attaching records it, so it completes from then on.
        tun = json.loads(json.dumps(TUNNELS))
        tun["known"] = [{"host": "otherbox", "trust": "trusted", "lastAttachedAt": 1}]
        out = self._run(tunnels=tun)
        self.assertIn("TESTHOST", out.get("hosts", []))
        self.assertIn("otherbox", out.get("hosts", []))
        self.assertEqual(out.get("hostsHtml"), "", "the datalist is built from option elements, never markup (2026-09-08)")

    def test_a_down_host_says_it_is_still_being_dialed_and_when(self):
        # romp never stops dialing an attached host (the user 2026-07-29), so the row's job is to prove
        # it: the countdown to the next attempt, and a way to skip the wait. A silent retry loop reads
        # exactly like an abandoned row, which is what the old give-up state was really objecting to.
        tun = json.loads(json.dumps(TUNNELS))
        tun["tunnels"][0].update({"status": "down", "fails": 3, "nextTry": int(time.time()) + 240})
        out = self._run(tunnels=tun)
        html = out.get("html", "")
        self.assertIn("Try now", html)
        self.assertIn("next try in 4m", html)
        self.assertNotIn("stopped trying", html)

    def test_a_down_host_with_no_deadline_yet_still_says_it_is_retrying(self):
        tun = json.loads(json.dumps(TUNNELS))
        tun["tunnels"][0].update({"status": "down", "fails": 1, "nextTry": 0})
        self.assertIn("retrying", self._run(tunnels=tun).get("html", ""))

    # ---- between your machines (the user 2026-08-11) ----------------------------------------------
    # The pair link a↔b appears on NONE of the rows above: every list in the panel manages only this
    # machine's own gates, so making two remote boxes trust each other used to mean opening each
    # box's own dashboard. The section reads each machine's table live (/tunnels/pairs) and writes
    # back through the kernel's trust-remote proxy.

    def _two_hosts(self):
        tn = json.loads(json.dumps(TUNNELS))
        b = json.loads(json.dumps(tn["tunnels"][0]))
        b["host"] = "PEERBOX"
        tn["tunnels"].append(b)
        return tn

    def test_one_host_offers_no_pair_section(self):
        out = self._run()
        self.assertNotIn("Between your machines", out.get("html", ""))

    def test_two_hosts_render_a_row_per_direction_with_the_shared_select(self):
        pairs = {"ok": True, "hosts": {"PEERBOX": {"ok": True}, "TESTHOST": {"ok": True}},
                 "pairs": [{"a": "PEERBOX", "b": "TESTHOST", "ab": "trusted", "ba": ""}]}
        out = self._run(tunnels=self._two_hosts(), pairs=pairs)
        html = out.get("html", "")
        self.assertIn("Between your machines", html)
        self.assertIn("<b>PEERBOX</b> holds <b>TESTHOST</b>", html)
        self.assertIn("<b>TESTHOST</b> holds <b>PEERBOX</b>", html)
        self.assertIn("data-pt-on=", html, "the write control is the same trust select, keyed per direction")
        # a direction with no explicit row renders as directed and says it was never set
        self.assertIn("directed is its default", html)

    def test_an_unreadable_holder_names_its_error_instead_of_a_select(self):
        pairs = {"ok": True, "hosts": {"PEERBOX": {"ok": False, "error": "not connected"},
                                       "TESTHOST": {"ok": True}},
                 "pairs": [{"a": "PEERBOX", "b": "TESTHOST", "ab": None, "ba": "directed"}]}
        out = self._run(tunnels=self._two_hosts(), pairs=pairs)
        html = out.get("html", "")
        self.assertIn("unreadable", html)
        self.assertIn("not connected", html)

    def test_the_pair_read_in_flight_shows_the_loader_not_a_blank(self):
        # PAIRS null = the fetch never settles; the section must say it is working, not sit empty.
        out = self._run(tunnels=self._two_hosts(), pairs=None)
        self.assertIn("reading how your machines hold each other", out.get("html", ""))

    def test_pair_binding_posts_the_trust_remote_route(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("select[data-pt-on]", js)
        self.assertIn("/tunnels/trust-remote", js)
        self.assertIn("/tunnels/pairs", js)


# A hostile string in every place a peer gets to choose one. Markers are chosen so a raw one is unambiguous
# in any innerHTML the panel assigned (`<img src=x` / `<script` / `" onmouseover=`) and its escaped form is
# equally unambiguous (`&lt;img src=x`).
IMG = "<img src=x onerror=alert(1)>"
QUOTE = 'x" onmouseover="alert(1)'
SCRIPT = "<script>alert(1)</script>"


class PeerStringsRenderAsText(_PanelHarness, unittest.TestCase):
    """Every string a PEER chooses reaches this panel through innerHTML — a host it named (a checked-in
    peer names itself; the rows /tunnels/of relays are a peer's whole list), its status word, its build,
    the bus gossip (tiers, relay hosts, holds). They were concatenated into markup as they came, so a
    hostile or merely odd peer scripted the dashboard that displayed it (2026-09-08). Executed against the
    real panel JS: every innerHTML the panel assigned is recorded, and none may carry a raw marker while
    the escaped text must actually be shown (rendered as text, not dropped)."""

    def _assert_inert(self, out, *markers):
        sets = out.get("htmlSets", [])
        self.assertTrue(sets, "the panel rendered something")
        for h in sets:
            for m in ("<img src=x", "<script", '" onmouseover='):
                self.assertNotIn(m, h, "a peer string reached innerHTML as markup:\n%s" % h[:400])
        html = out.get("html", "")
        for m in markers:
            self.assertIn(m, html, "the hostile string must still be SHOWN, as text")

    def test_a_peer_named_host_build_status_and_gossip_render_as_text_on_the_main_rows(self):
        tn = json.loads(json.dumps(TUNNELS))
        tn["tunnels"][0].update({"host": "TEST" + IMG + "HOST", "status": "up", "outOfDate": True,
                                 "behindBy": 2, "aheadBy": 0, "kernelSha": "abc" + QUOTE,
                                 "kernelVer": "v1" + SCRIPT, "localSha": "def5678", "localVer": "v0.2.0",
                                 "kernelDate": "<b>2026</b>", "stale": True, "lastOk": 1785272930})
        tn["peerTiers"] = {"TEST" + IMG + "HOST": "<i>trusted</i>"}
        tn["known"] = [{"host": "known" + IMG, "trust": "trusted", "lastAttachedAt": 1, "attached": True}]
        tn["viaReach"] = [{"host": "relay" + IMG, "via": "hub" + QUOTE, "agents": "<u>3</u>", "trust": "directed"}]
        tn["remoteHolds"] = [{"atHost": "holder" + IMG, "frm": "a", "to": "b", "gist": QUOTE, "origin": "a"}]
        out = self._run(tunnels=tn)
        self.assertEqual(out.get("errors"), [], "the render must not throw on any of it")
        self._assert_inert(out, "TEST&lt;img src=x onerror=alert(1)&gt;HOST", "abc" + "x&quot; onmouseover=&quot;alert(1)",
                           "v1&lt;script&gt;", "&lt;i&gt;trusted&lt;/i&gt;", "known&lt;img", "relay&lt;img",
                           "hub" + "x&quot; onmouseover", "holder&lt;img")
        # the completions list carries the raw NAME as an option value — a value, not markup
        self.assertIn("TEST" + IMG + "HOST", out.get("hosts", []))
        self.assertEqual(out.get("hostsHtml"), "")

    def test_the_connect_from_select_is_option_elements_naming_the_up_hosts(self):
        tn = json.loads(json.dumps(TUNNELS))
        tn["tunnels"][0]["host"] = "up" + QUOTE
        out = self._run(tunnels=tn)
        self.assertEqual(out.get("fromHtml"), "", "no markup was assigned to the select (2026-09-08)")
        self.assertEqual(out.get("fromOpts"), [["", "from: this machine"], ["up" + QUOTE, "from: up" + QUOTE]])

    def test_a_peers_own_rows_in_the_connections_expand_render_as_text(self):
        # The stub has no selector engine, so the expand toggle is stood in for: a button the panel finds
        # under `button[data-x]`, whose data-x names the up host — the panel binds its onclick on render,
        # the drive re-renders (a hostsPending post) and clicks it, and the /tunnels/of stub answers SUB.
        sub = {"ok": True, "of": "TESTHOST", "tunnels": [{
            "host": "peer" + IMG, "status": "up" + SCRIPT, "outOfDate": True, "behindBy": 1, "aheadBy": 0,
            "kernelSha": "abc" + QUOTE, "kernelVer": "v9" + SCRIPT, "localSha": "def5678", "localVer": "v0.2.0",
            "trust": "directed", "fastForward": True, "hasToken": True, "localPort": 5, "kernelPort": 6}]}
        drive = ("var BTN=mkEl('button');BTN.getAttribute=function(){return 'TESTHOST';};"
                 "ELS['rnet-list'].querySelectorAll=function(sel){return sel==='button[data-x]'?[BTN]:[];};"
                 + PendingHostsRowCopy.DELIVER + "BTN.click();")
        out = self._run(drive=drive, sub=sub)
        self.assertEqual(out.get("errors"), [])
        html = out.get("html", "")
        self.assertIn("rnet-subrow", html, "the expand rendered the peer's rows")
        self._assert_inert(out, "peer&lt;img src=x onerror=alert(1)&gt;", "up&lt;script&gt;", "v9&lt;script&gt;")

    def test_rows_the_kernel_left_out_are_said_beneath_the_rows_that_passed(self):
        # tunnels_of counts the peer rows it dropped (no host ssh would accept) as `dropped`; a list that is
        # simply shorter would be a silent thinning, so the note names the count — as text — in BOTH panels
        # (strip.ts droppedRowsNote wears the same sentence; net-remote-controls.test.ts pins the pair)
        sub = {"ok": True, "of": "TESTHOST", "dropped": 2,
               "tunnels": [{"host": "peerbox", "status": "up", "trust": "directed", "hasToken": True}]}
        drive = ("var BTN=mkEl('button');BTN.getAttribute=function(){return 'TESTHOST';};"
                 "ELS['rnet-list'].querySelectorAll=function(sel){return sel==='button[data-x]'?[BTN]:[];};"
                 + PendingHostsRowCopy.DELIVER + "BTN.click();")
        out = self._run(drive=drive, sub=sub)
        self.assertEqual(out.get("errors"), [])
        html = out.get("html", "")
        self.assertIn("peerbox", html, "the rows that passed still render")
        self.assertIn("2 rows from TESTHOST had no usable host and were left out", html)
        one = json.loads(json.dumps(sub)); one["dropped"] = 1; one["tunnels"] = []
        html1 = self._run(drive=drive, sub=one).get("html", "")
        self.assertIn("1 row from TESTHOST had no usable host and was left out", html1)
        self.assertNotIn("has no hosts attached", html1, "a list emptied by the drop is not 'no hosts attached'")

    def test_a_cut_completions_list_says_how_many_it_left_out(self):
        # 600 remembered hosts + the attached one: 512 option values, then ONE disabled marker naming the rest
        # (strip.ts fillHostSelect and the connect-from select wear the same marker)
        tun = json.loads(json.dumps(TUNNELS))
        tun["known"] = [{"host": "h%d" % i, "trust": "directed", "lastAttachedAt": 1} for i in range(600)]
        out = self._run(tunnels=tun)
        hosts = out.get("hosts", [])
        self.assertEqual(len(hosts), 513)
        self.assertEqual(hosts[-1], "\u2026 89 more not shown")
        self.assertTrue(out.get("hostsLastDisabled"), "the marker is not a pickable host")
        self.assertFalse(any("\u2026" in h for h in hosts[:-1]), "the marker is the only non-host entry")

    def test_the_markup_sinks_no_longer_interpolate_a_peers_strings_raw(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("function esc(s)", js, "one escape helper, beside the panel's other one-liners")
        # the rail icon's hover summary keeps a raw `(LBL[t.status]||t.status)`: that is a title PROPERTY (text)
        for gone in ("'<b>'+t.host+'</b>'", "'<b>'+s.host+'</b>'", "'<b>'+k.host+'</b>'", "'<b>'+v.host+'</b>'",
                     "'<b>'+hn+'</b>'", "(LBL[t.status]||t.status)+((t.status", "(LBL[t.status]||t.status)+'.'",
                     "(LBL[s.status]||s.status)+sver", "dl.innerHTML=", "fromSel.innerHTML=", "t.token"):
            self.assertNotIn(gone, js, "a raw peer string in a markup sink came back: %s" % gone)
        self.assertEqual(js.count("document.createElement('option')"), 4,
                         "the completions datalist and the connect-from select build option ELEMENTS — each its "
                         "hosts plus the one disabled cut marker")
        self.assertIn("t.hasToken", js, "the row reads the fact of a token, never the token")


class PendingHostsRowCopy(_PanelHarness, unittest.TestCase):
    """The panel must not contradict a blank board (the user 2026-09-02): after a kernel restart or a
    phone re-foreground the panes can show no trace of an attached host for a while (their relay
    sockets are still (re)dialing, the first payload has not landed) while this panel's row, read off
    the kernel's /tunnels, says a bare 'connected'. Each pane's federation manager posts the hosts IT
    still waits on ({romp:'hostsPending', app, hosts}); a host any pane still pends wears
    'connected · loading sessions…' with the loader until that pane's next post drops it."""

    DELIVER = ("(window._l.message||[]).forEach(function(f){f({data:{romp:'hostsPending',app:'feed',"
               "hosts:['TESTHOST']}});});")
    RETIRE = ("(window._l.message||[]).forEach(function(f){f({data:{romp:'hostsPending',app:'feed',"
              "hosts:[]}});});")

    def test_a_pane_still_waiting_on_an_up_host_marks_its_row_as_loading(self):
        out = self._run(drive=self.DELIVER)
        self.assertEqual(out["rows"], 1)
        self.assertIn("loading sessions\u2026", out["html"])
        self.assertIn("rnet-spin", out["html"], "the romp loader, not a bare word that could equally be stuck")
        self.assertIn("connected", out["html"], "the kernel's own truth stays: the tunnel IS up")

    def test_the_mark_leaves_when_the_pane_reports_the_payload_landed(self):
        out = self._run(drive=self.DELIVER + self.RETIRE)
        self.assertNotIn("loading sessions", out["html"])

    def test_a_host_no_pane_pends_wears_no_mark(self):
        out = self._run()
        self.assertNotIn("loading sessions", out["html"])

    def test_a_down_host_never_wears_the_loading_mark(self):
        # the kernel's own status word covers a down tunnel ("reconnecting…"); the pane-side mark is
        # only for the case where the tunnel is FINE and the dashboard is the one still catching up
        t = json.loads(json.dumps(TUNNELS))
        t["tunnels"][0]["status"] = "down"
        out = self._run(drive=self.DELIVER, tunnels=t)
        self.assertNotIn("loading sessions", out["html"])
        self.assertIn("reconnecting", out["html"])


if __name__ == "__main__":
    unittest.main()
