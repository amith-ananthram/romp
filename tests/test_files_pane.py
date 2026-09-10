#!/usr/bin/env python3
"""The Files pane (app=files): the file viewer as a dashboard column of its own, beside Chat,
Sessions, Outline and Feed, off by default, with the gear's "File links open in" setting routing a
chat file-link click into it. The kernel side, pinned here:

- the pane is not a feed consumer. The viewer is request/response (HTTP /file for the bytes; the
  saveFile and fileGitLink ops answer the sending client), so app=files is outside the feed audience
  and _push builds nothing for it; it counts only where a live pane must count, the conserve-memory
  viewer check.
- the /files page: the chat's styles.css for the viewer's dress, files-pane.css read live for the
  layout and the pane-resident variant (body.fileview-pane), no romp loader (an empty pane is not a
  loading state), the shim with the stale opt-out (a page that receives no pushed view never arms
  the shared "may be stale" prompt), federation.js before files.js.
- the shell: a fifth column after Feed, off by default, with its gutter, grow var, focus, Escape and
  mobile wiring, and the _PANE_ORDER label "Files"; the viewFile relay's pane arm, which brings the
  pane forward and forwards the click, identity included, into it. The arms are executed under node
  in tests/test_pane_state_broadcast.py; this module pins the served pages and the kernel's shape.

Synthetic fixtures only (the notes-api demo world, placeholder sids); nothing here mints a goal.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source


def _has(tc, needle, text, msg=""):
    """assertIn without the dump: a failure names the needle, never a whole page or source file."""
    tc.assertTrue(needle in text, msg or ("missing: %r" % needle))


def _lacks(tc, needle, text, msg=""):
    tc.assertFalse(needle in text, msg or ("present: %r" % needle))

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
km = load_source("romp_kernel_fpane", os.path.join(BIN, "romp-kernel"))
SRC = open(os.path.join(BIN, "romp-kernel")).read()
UI = Path(ROOT) / "ui" / "webview"


class _Backend:
    """The conserve-memory pass's backend, with nothing running: the pass then only records
    whether a viewer is connected, which is the one fact these tests read."""

    def running_sids(self):
        return []

    def live_sessions(self):
        return {}

    def conserve_idle(self, sid):
        return True

    def conserve_close(self, sid):
        return False


class Plumbing(unittest.TestCase):
    """app=files is a viewer to the kernel, not a feed client: keepalives and its own op replies ride
    its socket, nothing is built for it, and it counts as a live dashboard for conserve-memory."""

    def test_files_is_outside_the_feed_audience(self):
        # the ONE question _push asks of its targets (want_feed) and the route asks of the connected set
        self.assertFalse(km._feed_audience([{"app": "files"}]))
        self.assertTrue(km._feed_audience([{"app": "feed"}]))
        self.assertTrue(km._feed_audience([{"app": "files"}, {"app": "chat"}]))
        push = SRC[SRC.index("def _push(targets"):]
        push = push[:push.index("\ndef ")]
        _lacks(self, '"files"', push, "_push names every app it builds for; the Files pane is not one")

    def test_the_socket_accepts_any_app_name(self):
        # the handshake has no allowlist, so app=files connects on a kernel exactly as the other panes do
        _has(self, 'app = (q.get("app") or ["chat"])[0]', SRC)

    def test_an_open_files_pane_counts_as_a_viewer_for_conserve_memory(self):
        # executed: the pass stamps the last-viewer clock when a Files pane is the only client; a socket
        # with no pane behind it (the shell's) does not
        def tick(app, now):
            clients = [{"app": app, "alive": True}]
            with mock.patch.object(km, "_conserve_on", lambda: True), \
                 mock.patch.object(km, "_sdk", lambda: _Backend()), \
                 mock.patch.object(km, "_views_client", lambda: {}), \
                 mock.patch.object(km, "_clients", clients), \
                 mock.patch.object(km, "_conserve_last_viewer", [0]) as last:
                km._conserve_tick(now)
                return last[0]
        self.assertEqual(tick("files", 4242), 4242, "a Files pane is a viewer")
        self.assertEqual(tick("feed", 4243), 4243)
        self.assertEqual(tick("shell", 4244), 0, "the shell's own socket is not a pane")

    def test_the_page_carries_the_shared_dress_the_stale_opt_out_and_no_loader(self):
        page = km._files_page()
        _has(self, "app=files", page)
        _has(self, "var NOSTALE=true;", page, "the shim's stale opt-out is on for this page")
        _has(self, "/dist/styles.css", page)   # the viewer's .fileview-* dress
        _has(self, "<body class=fileview-pane>", page)   # keys the pane-resident variant
        _has(self, "<div id=files-empty></div>", page)
        _has(self, "/dist/federation.js", page)
        _has(self, "/dist/files.js", page)
        self.assertLess(page.index("/dist/federation.js"), page.index("/dist/files.js"), "manager before the bundle")
        _lacks(self, "id=pane-spin", page, "an empty pane is not a loading state")
        _lacks(self, "rel=manifest", page, "a pane, not an install target")
        _has(self, 'if p == "/files":', SRC)
        _has(self, "_files_page()", SRC)
        # the sheet is read live, like fleet-pane.css; a missing one fails loudly on the page, never blank
        css = (UI / "files-pane.css").read_text()
        _has(self, css.splitlines()[-1], page)
        with mock.patch.object(Path, "read_text", side_effect=OSError("gone")):
            _has(self, "needs the ui/ modules", km._files_page())

    def test_the_page_is_served_on_its_route(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/files?token=testtok" % srv.server_address[1], timeout=5) as r:
                self.assertEqual(r.status, 200)
                _has(self, "text/html", r.headers.get("Content-Type", ""))
                body = r.read().decode("utf-8", "replace")
        finally:
            srv.shutdown()
        _has(self, "<body class=fileview-pane>", body)
        _has(self, "/dist/files.js", body)
        _has(self, "body.fileview-pane #romp-fileview{", body, "files-pane.css is inlined live")

    def test_the_stale_opt_out_is_a_keyword_only_this_page_passes(self):
        """The shim arms the "connection lost, what you see may be stale" prompt on an unannounced
        reconnect and retires it on the kernel's connect-time push, the first non-keepalive frame.
        app=files gets no such push (nothing is built for it), so the arm would never clear and the
        second keepalive would raise the shell's shared banner after every unannounced reconnect, for a
        file fetched over HTTP on demand, which a dropped socket cannot make stale. The page renders the
        shim with no_stale=True, which bakes NOSTALE into the template so armStale and clearStale return
        early (the executed state machine is ui/webview/pane-shim-stale.test.ts). The build-drift prompt
        is a separate raise and stands. Every other pane has a live pushed view and keeps the arm."""
        on = km._shim("files", 1, no_stale=True)
        _has(self, "var NOSTALE=true;", on)
        _has(self, "function armStale(why){if(NOSTALE)return;stalePending=why;staleKa=0;}", on)
        _has(self, 'function clearStale(){stalePending="";   // armed but never shown → nothing to see\nif(NOSTALE)return;', on)
        _has(self, "function raiseBuild(){if(buildRaised)return;buildRaised=true;", on, "the build prompt is not gated")
        _has(self, "var NOSTALE=false;", km._shim("feed", 1), "the default keeps the arm")
        for page in (km._chat_page(), km._feed_page(), km._fleet_page(), km._timeline_page()):
            _has(self, "var NOSTALE=false;", page)
            _lacks(self, "var NOSTALE=true;", page)
        # the one page that passes it; the other pane pages call the shim exactly as they did
        shims = re.findall(r'_shim\("(\w+)", v(?:, ([^)]*))?\)', SRC)
        self.assertEqual([app for app, kw in shims if "no_stale=True" in (kw or "")], ["files"])
        self.assertEqual(sorted(app for app, kw in shims), ["chat", "feed", "files", "fleet", "timeline"])

    def test_the_editor_chunk_derives_from_the_pages_own_bundle_tag(self):
        # file-view.ts loads its CodeMirror chunk from a URL rewritten off the page's running bundle
        # <script src>. The Files page's bundle must be one that derivation recognizes, or every Edit in
        # the pane rejects with the raw "no bundle script tag" error and falls to the textarea. The pattern
        # is lifted from the source and run against the tags this page emits.
        view = (UI / "file-view.ts").read_text()
        m = re.search(r"\.find\(\(u\) => /(.+?)/\.test\(u\)\)", view)
        self.assertIsNotNone(m, "the derivation's find literal is where the pin expects it")
        pat = re.compile(m.group(1))
        srcs = re.findall(r"<script src=([^ >]+)", km._files_page())
        self.assertTrue(srcs)
        hits = [s for s in srcs if pat.search("http://TESTHOST:1" + s)]   # the browser's absolute .src
        self.assertEqual(hits, [s for s in srcs if s.startswith("/dist/files.js?v=")], "the bundle, and only the bundle")

    def test_the_pane_resident_variant_lives_only_in_the_pane_sheet(self):
        # the modal variant is mirrored byte-equal in styles.css and feed.css (fileview-parity.test.ts);
        # the pane's override must not enter either, or the mirrors drift
        css = (UI / "files-pane.css").read_text()
        _has(self, "body.fileview-pane #romp-fileview{position:relative;inset:auto;flex:1 1 auto;min-height:0;background:none}", css)
        _has(self, "body.fileview-pane .fileview{width:100%;height:100%;border:0;border-radius:0;box-shadow:none}", css)
        for sheet in ("styles.css", "feed.css"):
            _lacks(self, "fileview-pane", (UI / sheet).read_text(), sheet)
        _lacks(self, "fleet", css.lower(), "no fleet vocabulary in the new sheet")


class Shell(unittest.TestCase):
    """The dashboard grows a fifth pane: the far-right column after Feed, OFF by default (the viewFile
    relay brings it forward when a click routes there), with its own gutter, grow var, and every
    hand-written pane list in the landing JS extended: focus ring, Alt+Arrow columns, Escape wiring, the
    Log's pane names, the mobile tab map, the pane controller. One label, "Files", from _PANE_ORDER."""

    def setUp(self):
        self.html = km._landing()

    def test_the_pane_is_in_the_one_ordering_last(self):
        self.assertEqual(km._PANE_ORDER[-1], ("files", "Files"))
        _has(self, "<div class=rail-btn data-pane=files>Files</div>", self.html)
        _has(self, "<button data-pane=files>Files</button>", self.html)

    def test_the_column_sits_after_the_feed_with_its_gutter_and_grow_var(self):
        flat = self.html.replace('"\n            "', "")
        _has(self, '<div class=gv id=gv-c></div><div class=pane id=files-pane><iframe id=f-files src=/files></iframe></div>', flat)
        self.assertLess(self.html.index("id=feed-pane"), self.html.index("id=gv-c"))
        self.assertLess(self.html.index("id=gv-c"), self.html.index("id=files-pane"))
        self.assertLess(self.html.index("id=files-pane"), self.html.index("id=gh"), "before the timeline band")
        _has(self, "#files-pane{flex:var(--g-files,40) 1 0}", self.html)
        _has(self, "body:not(.po-files) #files-pane{display:none}", self.html)
        # the gutter shows only between two visible panes: hidden when Files is off, or when no column sits to its left
        _has(self, "body:not(.po-files) #gv-c,body:not(.po-chat):not(.po-fleet):not(.po-feed) #gv-c{display:none}", self.html)

    def test_off_by_default_and_toggled_by_the_controller(self):
        _has(self, "<body class='po-chat po-feed po-timeline'>", self.html)   # not po-files
        _has(self, "po={chat:true,fleet:false,feed:true,timeline:true,files:false}", self.html)
        _has(self, "po={chat:false,fleet:false,feed:false,timeline:false,files:false}", self.html)   # the ?panes= reset
        _has(self, "document.body.classList.toggle('po-files',!!po.files)", self.html)
        _has(self, "files:'files pane'", self.html)   # the rail tooltip's words

    def test_every_pane_list_in_the_landing_js_names_it(self):
        _has(self, "'f-files':'files-pane'", km._LANDING_FOCUS_JS)
        _has(self, "var COLS=['f-chat','f-fleet','f-feed','f-files']", km._LANDING_FOCUS_JS)
        _has(self, "['f-chat','f-fleet','f-feed','f-files','f-timeline'].forEach", km._LANDING_ESC_JS)
        _has(self, "['f-chat','f-fleet','f-feed','f-files','f-timeline'].forEach", km._LANDING_MOBILE_JS)
        # the Log's connection-lost label reads the one map, so the pane's row in _PANE_ORDER is the pin
        _has(self, "var PN=" + json.dumps(dict(km._PANE_ORDER)) + ";", km._LANDING_ERRS_JS)
        self.assertEqual(dict(km._PANE_ORDER).get("files"), "Files")
        _has(self, "files:document.getElementById('f-files')", km._LANDING_MOBILE_JS)
        _has(self, "var PANES=['chat-pane','fleet-pane','feed-pane','files-pane'];", self.html)
        _has(self, "grow={chat:60,fleet:34,feed:40,files:40}", self.html)
        _has(self, "id==='feed-pane'?'feed':'files'", self.html)
        _has(self, "gutter('gv-c',function(){var c=document.body.classList;return c.contains('po-feed')?'feed-pane':"
                      "c.contains('po-fleet')?'fleet-pane':'chat-pane';},'files-pane');", self.html)

    def test_mobile_tab_and_the_palette_command(self):
        _has(self, "#chat-pane,#fleet-pane,#feed-pane,#files-pane,#tl-pane{display:contents!important}", self.html)
        _has(self, "#f-chat.m-on,#f-fleet.m-on,#f-feed.m-on,#f-files.m-on{display:block}", self.html)
        pal = (UI / "palette-main.ts").read_text()
        _has(self, '["files", "files"]', pal)
        _has(self, '"f-files"', pal)


class Relay(unittest.TestCase):
    """The shell's message listener gains a viewFile pane arm: a click routed to the Files pane brings
    that pane forward (desktop toggle, phone tab) and forwards the click, identity included, into
    #f-files. The pane stays up, so no restore is owed; the browseFiles arm the feed's browser rides is
    untouched. The arms run under node in tests/test_pane_state_broadcast.py; these pin the shape."""

    HEAD = "if(m.romp==='viewFile'&&m.pane==='pane'){var ff=document.getElementById('f-files');"

    @staticmethod
    def _code(js):
        return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))

    def test_the_pane_arm_brings_the_files_pane_forward_and_forwards_the_identity(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, self.HEAD, js)
        self.assertEqual(js.count("m.romp==='viewFile'"), 1, "one viewFile arm in the shell, aimed at the Files pane")
        self.assertEqual(SRC.count("m.romp==='viewFile'"), 1, "and no other viewFile arm anywhere in the kernel")
        branch = self._code(js.split(self.HEAD)[1].split("if(m.romp==='filesViewerClosed')")[0])
        _has(self, "window.__rompPaneToggle&&window.__rompPaneToggle('files',true)", branch)
        # phone: the Files tab comes forward only in the mobile layout, and the tab the click came from is
        # remembered so the viewer's close puts the person back
        _has(self, "if(window.__rompMobileOn&&window.__rompMobileOn()){var cur=document.body.getAttribute('data-tab')||'chat';", branch)
        _has(self, "if(cur!=='files'){window.__rompFilesTabFrom=cur;window.__rompMobileTab&&window.__rompMobileTab('files');}", branch)
        _has(self, "postMessage({romp:'viewFile',path:m.path,sid:m.sid,identity:m.identity||null},'*')", branch)
        self.assertEqual(branch.count("postMessage("), 1, "one forward, carrying the whole click")
        for tok in ("__rompFeedWasOff", "'f-feed'", "browseClosed"):
            _lacks(self, tok, branch, tok + " belongs to the feed's browser route")

    def test_the_files_viewers_close_restores_the_remembered_tab_mobile_only(self):
        js = km._LANDING_SETTINGS_JS
        handler = ("if(m.romp==='filesViewerClosed'){var back=window.__rompFilesTabFrom;window.__rompFilesTabFrom=null;\n"
                   "  if(back&&window.__rompMobileOn&&window.__rompMobileOn()){try{window.__rompMobileTab&&window.__rompMobileTab(back);}catch(e){}}}")
        _has(self, handler, js)
        # the sender: files.ts posts the close EDGE up (executed in ui/webview/files.test.ts)
        files = (UI / "files.ts").read_text()
        _has(self, 'if (viewerUp && !up && window.parent !== window) window.parent.postMessage({ romp: "filesViewerClosed" }, "*");', files)

    def test_the_feeds_browse_relay_is_untouched(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, "if(m.romp==='browseFiles'){var bf=document.getElementById('f-feed');", js)
        _has(self, "try{window.__rompMobileTab&&window.__rompMobileTab('feed');}catch(e){}   // phone: one pane at a time", js)
        _has(self, "if(m.romp==='browseClosed'&&window.__rompFeedWasOff){window.__rompFeedWasOff=false;", js)

    def test_the_two_ends_agree_on_the_message(self):
        # the chat names its target and carries the session's identity (render.ts openPath, through the
        # gesture reader); the pane validates the identity and caches it per sid (files.ts)
        render = (UI / "render.ts").read_text()
        _has(self, 'window.parent.postMessage({ romp: "viewFile", path, sid: to, pane: "pane",', render)
        _has(self, "fileLinkRoute(settings.fileLinkPane, window.parent !== window, panesOn.files === true)", render)
        files = (UI / "files.ts").read_text()
        _has(self, "asIdentity(m.identity)", files)
        route = (UI / "file-route.ts").read_text()
        _has(self, "export function fileLinkRoute(pane: unknown, framed: boolean, filesOpen: boolean): FileRoute {", route)

    def test_the_gear_and_the_guide_say_the_open_pane_wins(self):
        gear = (UI / "gear.js").read_text()
        _has(self, "While the Files pane is open, the file opens there.", gear)
        _has(self, "<option value=chat>The pane you clicked</option><option value=pane>The Files pane</option>", gear)
        guide = (Path(ROOT) / "docs" / "guide.md").read_text()
        _has(self, "### Files\n", guide)
        _has(self, "While the pane is open, a file link clicked in the chat opens in it.", guide.replace("\n", " "))
        self.assertLess(guide.index("### The outline"), guide.index("### Files"))
        self.assertLess(guide.index("### Files"), guide.index("## Automatic nudges"))


if __name__ == "__main__":
    unittest.main()
