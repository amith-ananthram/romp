#!/usr/bin/env python3
"""The settings gear on a page of its own (the user 2026-09-10, who wanted the Sessions, Outline and Feed
panes optional: with the gear riding the feed bundle, the Feed pane was structurally required, since every
opener posted openSettings into #f-feed and the shell lifted that iframe). The kernel side, pinned here:

- the /settings page: the gear alone (ui/webview/settings-page.ts hosts gear.js), feed.css for the theme
  tokens and the gear's dress plus gear.css, a transparent page under the modal (the dimmed dashboard
  shows through the lifted iframe), the shim with the stale opt-out (a page that receives no pushed view
  never arms the shared "may be stale" prompt), federation.js before settings-page.js, no romp loader.
- app=settings is a viewer to nothing: outside the feed audience, nothing built for it in _push, and
  not counted by the conserve-memory pass (a hidden always-connected iframe is not a person looking).
- the shell: a hidden #f-settings iframe that is NOT a pane (no _PANE_ORDER row, no rail button, no
  phone tab, no gutter, not inside a .pane), lifted full-window while body.settings-open in place of
  the feed iframe; ONE opener (__rompOpenSettings) that the rail's gear, the phone's settings action
  and a pane's own ask ({romp:'openSettings'} up to the shell) and the palette's command all use; the
  iframe served with data-src and loaded by that opener on the first open (an idle gear cost a kernel
  socket plus one per attached host on every dashboard load), the ask held for the page's load; the Log's
  unread count told to that document; the models frame sent to app settings.
- the feed page: hosts no gear. It sets window.__rompGearOnSettingsPage before feed.js, which then
  skips the mount (ui/webview/gear-host.ts, executed in gear-host.test.ts), and links gear.css no more.
  VS Code's feed panel, which sets no flag, keeps the gear from the same bundle.

The opener's arms run under node in tests/test_pane_state_broadcast.py RelayArms; the served-page and
browser guards (the gear opening in the settings iframe, the feed frame without one) are
tests/test_log_opener_moved.py ServedOpener and tests/test_gear_select_matrix.py ServedMatrix.
Synthetic fixtures only; nothing here mints a goal.
"""
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
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
km = load_source("romp_kernel_settingspage", os.path.join(BIN, "romp-kernel"))
SRC = open(os.path.join(BIN, "romp-kernel")).read()
UI = Path(ROOT) / "ui" / "webview"
PALETTE = (UI / "palette-main.ts").read_text()
FEED_TS = (UI / "feed.ts").read_text()
ESBUILD = (Path(ROOT) / "vscode-extension" / "esbuild.js").read_text()


class _Backend:
    """The conserve-memory pass's backend, with nothing running: the pass then only records whether
    a viewer is connected, which is the one fact these tests read."""

    def running_sids(self):
        return []

    def live_sessions(self):
        return {}

    def conserve_idle(self, sid):
        return True

    def conserve_close(self, sid):
        return False


class Plumbing(unittest.TestCase):
    """app=settings is a socket for the gear's ops and their replies, and nothing else."""

    def test_settings_is_outside_the_feed_audience_and_nothing_is_built_for_it(self):
        self.assertFalse(km._feed_audience([{"app": "settings"}]))
        self.assertTrue(km._feed_audience([{"app": "settings"}, {"app": "chat"}]))
        push = SRC[SRC.index("def _push(targets"):]
        push = push[:push.index("\ndef ")]
        _lacks(self, '"settings"', push, "_push names every app it builds for; the settings page is not one")

    def test_a_settings_client_is_not_a_viewer_for_conserve_memory(self):
        # executed: the pass stamps the last-viewer clock for a pane, never for the always-connected
        # settings iframe (hidden until the gear opens) or the shell's own socket
        def tick(app, now):
            clients = [{"app": app, "alive": True}]
            with mock.patch.object(km, "_conserve_on", lambda: True), \
                 mock.patch.object(km, "_sdk", lambda: _Backend()), \
                 mock.patch.object(km, "_views_client", lambda: {}), \
                 mock.patch.object(km, "_clients", clients), \
                 mock.patch.object(km, "_conserve_last_viewer", [0]) as last:
                km._conserve_tick(now)
                return last[0]
        self.assertEqual(tick("settings", 4242), 0, "the settings iframe is not a person looking")
        self.assertEqual(tick("feed", 4243), 4243, "a pane is")

    def test_the_models_frame_reaches_the_settings_app(self):
        # the gear's cached /models list follows a pick (test_model_versions.py executes the send)
        fn = SRC[SRC.index("def _models_changed"):]
        fn = fn[:fn.index("\ndef ")]
        _has(self, 'for app in ("chat", "timeline", "feed", "settings"):', fn)


class Page(unittest.TestCase):
    def test_the_page_carries_the_gear_host_the_dress_the_stale_opt_out_and_no_loader(self):
        page = km._settings_page()
        _has(self, "app=settings", page)
        _has(self, "var NOSTALE=true;", page, "the shim's stale opt-out is on for this page")
        _has(self, "/dist/feed.css", page)    # the theme tokens + the gear's dress (gear.css reads its variables)
        _has(self, "/dist/gear.css", page)
        _has(self, "<body class=settings-page>", page)
        _has(self, "html,body.settings-page{background:transparent}", page, "the dimmed dashboard shows through")
        _has(self, "/dist/federation.js", page)
        _has(self, "/dist/settings-page.js", page)
        self.assertLess(page.index("/dist/federation.js"), page.index("/dist/settings-page.js"), "manager before the bundle")
        _lacks(self, "id=pane-spin", page, "nothing loads to wait for")
        _lacks(self, "/dist/feed.js", page, "the feed bundle is not this page's")
        _lacks(self, "rel=manifest", page, "a page, not an install target")
        _has(self, 'if p == "/settings":', SRC)
        _has(self, "_settings_page()", SRC)

    def test_the_page_is_served_on_its_route(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/settings?token=testtok" % srv.server_address[1], timeout=5) as r:
                self.assertEqual(r.status, 200)
                _has(self, "text/html", r.headers.get("Content-Type", ""))
                body = r.read().decode("utf-8", "replace")
        finally:
            srv.shutdown()
        _has(self, "<body class=settings-page>", body)
        _has(self, "/dist/settings-page.js", body)
        _has(self, 'var APP="settings"', body, "the shim connects as app=settings")

    def test_the_bundle_is_an_entry_point_that_hosts_the_gear(self):
        _has(self, '"../ui/webview/settings-page.ts"', ESBUILD)
        page = (UI / "settings-page.ts").read_text()
        _has(self, 'require("./gear.js")', page)
        _has(self, "initGear(", page)
        _has(self, "{ ownPage: true }", page, "a page that is nothing but the gear: no pane rect to pin")


class FeedPage(unittest.TestCase):
    def test_the_feed_page_hosts_no_gear(self):
        page = km._feed_page()
        _has(self, "window.__rompGearOnSettingsPage=true;", page, "the flag feed.js reads before mounting")
        self.assertLess(page.index("__rompGearOnSettingsPage"), page.index("/dist/feed.js"), "set before the bundle runs")
        _lacks(self, "/dist/gear.css", page, "the modal's stylesheet belongs to the page that hosts it")
        _has(self, "if (hostsGear(window)) initGear(", FEED_TS, "the mount is gated on the flag (gear-host.ts)")
        _lacks(self, 'window.postMessage({ romp: "openSettings" }', FEED_TS, "no same-document opener remains: openGear goes up to the shell")


class Shell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()

    def test_the_settings_iframe_is_embedded_hidden_and_is_not_a_pane(self):
        h = self.html
        _has(self, "<iframe id=f-settings data-src=/settings title=Settings></iframe>", h, "served without a src: the page loads on the first open")
        _lacks(self, "id=f-settings src=", h)
        self.assertLess(h.index("id=f-settings"), h.index("<div class=col>"), "outside the pane grid, never inside a .pane")
        _has(self, "#f-settings{display:none}", h)
        _has(self, "body.settings-open #f-settings{display:block;position:fixed;inset:0;z-index:200;background:transparent}", h)
        _lacks(self, "body.settings-open #f-feed", h, "the feed iframe is no longer lifted")
        _lacks(self, "body.settings-open #feed-pane", h, "…nor un-hidden for the gear")
        self.assertNotIn("settings", [k for k, _ in km._PANE_ORDER])
        _lacks(self, "data-pane=settings", h, "no rail button, no phone tab")
        _lacks(self, "id=settings-pane", h, "no pane div, no gutter")
        # the lift rule outranks the phone layout's bare `iframe{display:none}` by specificity, so the same
        # rule serves both layouts (the mobile test pins the phone side)

    def test_one_opener_and_every_caller_uses_it(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, "window.__rompOpenSettings=function(){var f=document.getElementById('f-settings');", js)
        # the first open gives the iframe its src and holds the ask for the page's load (a message into a document
        # still loading is dropped); a second ask while one waits is not queued (the page's opener toggles)
        _has(self, "if(!f.getAttribute('src')){var u=f.getAttribute('data-src');if(!u)return;sPend=true;f.setAttribute('src',u);", js)
        _has(self, "if(sPend){sPend=false;open();}});return;}", js)
        _has(self, "if(sPend)return;", js)
        _has(self, "if(gear)gear.onclick=function(){window.__rompOpenSettings();};", js, "the rail's gear")
        _has(self, "if(m.romp==='openSettings')window.__rompOpenSettings();", js, "a pane's ask is forwarded")
        _has(self, "var A={settings:function(){try{window.__rompOpenSettings&&window.__rompOpenSettings();}catch(e){}},", km._LANDING_MOBILE_JS, "the phone's action")
        _has(self, 'run: () => { if (w.__rompOpenSettings) w.__rompOpenSettings(); },', PALETTE, "the palette's settings.open goes through the shell's opener (the page may not be loaded yet)")
        _lacks(self, 'pane("f-settings")!.contentWindow!.postMessage({ romp: "openSettings" }', PALETTE)
        _lacks(self, 'pane("f-feed")!.contentWindow!.postMessage({ romp: "openSettings" }', PALETTE)
        # nothing in the shell posts the open into the feed any more
        for m in re.finditer(r"getElementById\('f-feed'\)[^\n]*", SRC):
            _lacks(self, "openSettings", m.group(0), "an opener still aims at the feed: " + m.group(0)[:80])

    def test_the_logs_unread_count_is_told_to_the_gears_document(self):
        _has(self, "function tell(n){var f=document.getElementById('f-settings');", km._LANDING_ERRS_JS)


if __name__ == "__main__":
    unittest.main()
