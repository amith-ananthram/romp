#!/usr/bin/env python3
"""Mobile shell: the combined landing page collapses to a one-pane-at-a-time tab switcher on a
narrow/touch viewport, and the kernel tells the shell to switch to Chat when a feed/timeline tap
brings the chat forward. Pure-HTML + routing asserts; no real session data.
"""
import json
import os
import subprocess
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_mobile", os.path.join(BIN, "romp-kernel"))


class LandingShell(unittest.TestCase):
    def test_three_panes_are_addressable_iframes(self):
        html = km._landing()
        for fid in ("id=f-chat", "id=f-feed", "id=f-timeline"):
            self.assertIn(fid, html)

    def test_mobile_tabbar_one_button_per_pane(self):
        html = km._landing()
        self.assertIn("id=mtabs", html)
        for pane in ("data-pane=chat", "data-pane=fleet", "data-pane=feed", "data-pane=timeline"):
            self.assertIn(pane, html)

    def test_outline_is_a_mobile_tab_not_desktop_only(self):
        # the user 2026-07-11, who couldn't access the outline view in the mobile UI — the fleet pane was
        # explicitly desktop-only (#fleet-pane display:none!important, no tab, no switcher entry).
        html = km._landing()
        self.assertIn(">Outline</button>", html)                       # the tab exists, labeled Outline
        self.assertIn("#f-chat.m-on,#f-fleet.m-on,#f-feed.m-on,#f-files.m-on{display:block}", html)   # ...and shows as the active pane
        self.assertNotIn("#fleet-pane{display:none!important}", html)  # the desktop-only exclusion is gone
        self.assertIn("fleet:document.getElementById('f-fleet')", km._LANDING_MOBILE_JS)
        # the chat header's Fleet pill / the fleet's back-to-chat (toggleFleet) is a tab switch on mobile
        self.assertIn("'toggleFleet'", km._LANDING_MOBILE_JS)

    def test_rail_actions_reachable_on_mobile(self):
        # the user 2026-07-11: settings / the network panel / usage stats were rail-only (the rail is
        # hidden on mobile). data-act buttons on the bar; each routes to the existing machinery.
        html = km._landing()
        for act in ("data-act=settings", "data-act=net", "data-act=usage", "data-act=restart"):
            self.assertIn(act, html)
        # ICONS, not words (the user 2026-07-11): settings wears the desktop rail's own gear glyph, net its
        # network-tree SVG; usage gets the theme's own motif — two stacked fill bars at different levels
        # the settings icon is the SAME gear the desktop rail uses (U+26ED ⛭), not the outlined star it had
        self.assertIn("data-act=settings data-keycmd=settings.open aria-label=Settings title=Settings>⛭</button>", html)
        self.assertIn("id=rail-gear data-keycmd=settings.open title=Settings aria-label=Settings>⛭</div>", html)  # matches the rail
        self.assertNotIn("&#9885;", html)                               # the old outlined-star glyph is gone
        self.assertIn("data-act=net data-keycmd=net.open aria-label='Remote kernels'", html)
        self.assertIn("<rect x='1' y='3' width='9' height='4' rx='1' fill='currentColor'/>", html)   # the used-bar fill
        self.assertNotIn(">Gear</button>", html)
        self.assertIn("window.__rompOpenSettings&&window.__rompOpenSettings();", km._LANDING_MOBILE_JS)   # same path as the desktop gear: the settings iframe
        self.assertIn("__rompOpenNet", km._LANDING_MOBILE_JS)           # opens the shell's remotes panel
        self.assertIn("window.__rompOpenNet=open", km._LANDING_REMOTES_JS)
        self.assertIn("__rompUsagePanel", km._LANDING_MOBILE_JS)        # the tooltip's bars as a modal
        self.assertIn("window.__rompUsagePanel=function", km._LANDING_USAGE_JS)
        self.assertIn("#ru-tip.ru-modal", html)                         # centered placement for the panel
        # the lifted-fullscreen settings iframe must override the mobile display:none
        self.assertIn("body.settings-open #f-settings{display:block;position:fixed", html)

    def test_mobile_restart_button_reuses_the_rail_refresh_kernel_restart(self):
        # the user 2026-07-22: there was no restart-kernel affordance on mobile (the rail's own ↻ is hidden
        # there). Add a bar button that fires the SAME restart the rail does — factored to window.__rompRestart
        # (POST /restart, poll /healthz, reload) so both surfaces share one path, not a copy.
        html = km._landing()
        # …and it wears the SAME browser-style reload svg as the rail (the ↻ text glyph is gone, 2026-07-27)
        self.assertIn("data-act=restart data-keycmd=kernel.restart aria-label='Restart kernel' title='Restart kernel'>" + km._REFRESH_SVG + "</button>", html)
        self.assertIn("window.__rompRestart=function", km._LANDING_SETTINGS_JS)   # the shared restart path
        self.assertIn("fetch('/restart',{method:'POST'})", km._LANDING_SETTINGS_JS)
        self.assertIn("rf.onclick=function(){rf.style.pointerEvents='none';rf.style.opacity='0.5';window.__rompRestart();}", km._LANDING_SETTINGS_JS)
        self.assertIn("restart:function(){try{window.__rompRestart", km._LANDING_MOBILE_JS)   # the bar routes to it

    def test_mobile_bar_reservation_collapses_while_the_keyboard_is_open(self):
        # the user 2026-07-22: focusing the composer opened the keyboard and left a dead black band between
        # the box and the keyboard — the fixed bar's reserved height (--mtabs-h) showing through while the
        # bar itself was hidden behind the keyboard. Collapse the reservation to 0 when the keyboard is open
        # (visual viewport much shorter than the layout viewport), restore it when the keyboard closes.
        js = km._LANDING_MOBILE_JS
        # scale-aware (the user 2026-08-19): a desktop pinch shrinks vv.height by the zoom factor; height*scale
        # recovers the layout height, so a pinch never reads as "keyboard open" (or re-fits --app-h smaller)
        self.assertIn("function kbOpen(){var vv=window.visualViewport;return vv?(window.innerHeight-vv.height*(vv.scale||1)>120):false;}", js)
        # desktop (fine pointer) uses innerHeight outright — pinch-immune in every browser, no scale
        # arithmetic (desktop Firefox does not reliably report vv.scale during a pinch); the visual
        # viewport drives the fit only on coarse-pointer devices, where keyboards/toolbars live
        self.assertIn("var coarse=window.matchMedia&&matchMedia('(pointer: coarse)').matches;", js)
        self.assertIn("var h=(!coarse||!vv)?window.innerHeight:Math.round(vv.height*(vv.scale||1));", js)
        self.assertIn("--mtabs-h',(kbOpen()?0:(bar.offsetHeight||0))+'px'", js)

    def test_usage_modal_dismisses_via_a_real_backdrop_not_a_document_click(self):
        # the user 2026-07-22: on mobile the Usage panel got STUCK — an outside tap landed on a content
        # iframe (a different document), so the shell's document-level click listener never fired and the
        # modal never closed. Fix: a real full-screen backdrop in the SHELL document (like the net panel's
        # #rnet-back) catches the tap, so any tap over it dismisses. The #ru-tip is pointer-events:none, so
        # a tap that visually lands on the panel still reaches the backdrop underneath and closes it.
        html, js = km._landing(), km._LANDING_USAGE_JS
        self.assertIn("ru-back", js)                              # the backdrop element is created
        self.assertIn("back.onclick=off", js)                    # a tap on the backdrop closes the modal
        self.assertIn("back.classList.add('on')", js)            # ...shown when the panel opens
        self.assertIn("#ru-back{position:fixed;inset:0", html)   # full-screen, in the shell document
        self.assertIn("#ru-back.on{display:block}", html)
        # the old broken mechanism (a document-level capture click listener) is gone
        self.assertNotIn("addEventListener('click',off,true)", js)

    def test_desktop_unchanged_tabbar_hidden_until_breakpoint(self):
        html = km._landing()
        self.assertIn("#mtabs{display:none}", html)   # hidden by default (desktop)
        self.assertIn("@media", html)                 # a breakpoint reveals it + collapses to one pane
        self.assertIn(".m-on{display:block}", html)   # the single active pane on mobile
        # the desktop shell is the flex pane row (chat | fleet | feed | timeline)
        self.assertIn(".col{display:flex", html)
        self.assertIn("src=/chat", html)
        self.assertIn("data-src=/feed", html)   # optional panes load from data-src (the Panes setting)
        self.assertIn("data-src=/timeline", html)

    def test_the_shell_leaves_a_hair_of_slack_down_the_right_edge(self):
        # The panes tiled flush to the window, so whatever sat hard right inside one — a feed card's
        # controls, the timeline's lock padlock at the now-edge, the rail's right-pinned actions — was
        # pressed against the frame or clipped by it (the user 2026-07-23). .col is the one wrapper that
        # covers the pane row, the timeline band and the bottom rail together.
        html = km._landing()
        # height:100%, not 100vh: the body is what clips, and on iOS 100vh is the address-bar-collapsed
        # viewport, which pushed the rail out of the bottom (the user 2026-07-29)
        self.assertIn(".col{display:flex;flex-direction:column;height:100%;box-sizing:border-box;padding-right:3px}", html)
        # border-box, or the strip ADDS to the 100vh box and the shell overflows instead of insetting
        self.assertIn("box-sizing:border-box;padding-right:3px", html)

    def test_the_right_edge_strip_is_desktop_only(self):
        # One pane fills a phone screen, so a 3px sliver of backdrop down its edge reads as a rendering
        # fault, not as slack. The desktop longhand survives the media query unless it is named there.
        html = km._landing()
        i = html.index("@media (max-width:820px),(pointer:coarse)")
        mobile = html[i:i + 2000]
        self.assertIn("padding-right:0", mobile, "the mobile .col must cancel the desktop strip")

    def test_mobile_pane_has_explicit_height_not_auto(self):
        # regression: the mobile pane was sized with height:auto + bottom offset; mobile browsers read
        # height:auto on an iframe as "size to content" and collapse it (chat shrank to its tab bar).
        html = km._landing()
        self.assertIn("100dvh", html)                          # explicit, address-bar-aware viewport height
        self.assertNotIn("height:auto;display:none", html)     # the collapsing iframe rule is gone

    def test_shell_reserves_the_bar_height_so_it_cannot_cover_the_pane(self):
        # regression: a position:fixed bar overlapped the chat composer (which is why flex briefly replaced
        # it). The bar is fixed again — glued to the viewport bottom so no dead space can sit below it — but
        # now .col RESERVES the bar's measured height (--mtabs-h) as padding-bottom, so the iframes tile
        # ABOVE the bar and it can't cover the composer. One pane shows at a time, keyed off body[data-tab].
        html = km._landing()
        self.assertIn("padding-bottom:var(--mtabs-h", html)    # .col reserves the bar's height
        self.assertIn("--mtabs-h", km._LANDING_MOBILE_JS)      # ...measured from the live bar (offsetHeight)
        self.assertIn("#f-timeline.m-on{display:block}", html) # timeline is a mobile tab pane (it lives in the row now)
        self.assertIn("data-tab", km._LANDING_MOBILE_JS)       # show() marks the active pane on <body>

    def test_shell_reveal_listener_wired(self):
        html = km._landing()
        self.assertIn("app=shell", html)              # shell WS catches kernel reveals (feed/timeline tap)
        self.assertIn("'reveal'", html)               # ...and window reveals (timeline deep-link)

    def test_timeline_iframe_is_the_fourth_pane(self):
        # the timeline is its own rail-toggled pane now (the user 2026-06-24), not a bottom band: the iframe
        # carries id=f-timeline inside #tl-pane, and the old stale-id splitter bug must not regress.
        html = km._landing()
        self.assertIn("id=f-timeline", html)                      # the iframe carries this id
        # data-src, not src (the user 2026-09-10): the band is an optional pane, loaded by the pane controller
        # only where this browser's gear shows it (tests/test_pane_state_broadcast.py OptionalPanes)
        self.assertIn("<div class=pane id=tl-pane><iframe id=f-timeline data-src=/timeline></iframe></div>", html)
        self.assertNotIn("getElementById('t')", km._LANDING_JS)   # the stale id is gone

    def test_mobile_switcher_is_isolated_in_its_own_script(self):
        # the switcher runs in a separate <script> so a splitter throw can't disable the tab bar. Each shell
        # behaviour gets its own isolated <script>: boot-splash + connection-status banner + rail-usage +
        # splitter + focus-ring + fleet-toggle + settings-fullscreen + mobile switcher + viewport-pin +
        # per-pane collapse handles + the build-staleness banner + the remote-drift push banner = 12
        # (the user 2026-06-23; + boot-splash + rail-usage 2026-06-26; + connection-status banner
        # 2026-06-27; + the visible-viewport pin; + the remote-drift banner 2026-07-04; + the head's
        # ios-standalone viewport flip and the push bell 2026-08-07, plans/ios-app.md; + the shared
        # Escape-closes-the-topmost-modal block 2026-08-09; + the release-update banner 2026-08-09).
        html = km._landing()
        # +1 2026-08-28: the theme reader right after <body>; +1 2026-09-06: the notification-tap landing
        # script (_LANDING_REVEAL_JS) — its own script so a throw in the bell's cannot strand a tap;
        # +1 2026-09-08: the reload core (T265, _reload_core) ahead of the build-staleness banner script, which
        # registers as its refused fallback — its own script so a banner throw cannot take the reload with it
        # +1: the bottom bar's API health cell (_LANDING_APIH_JS), after the usage script whose backdrop it shares
        self.assertEqual(html.count("<script>"), 20)

    def test_bottom_bar_is_text_only_and_compact(self):
        html = km._landing()
        self.assertNotIn("class=ic", html)                       # no icon spans — text labels only
        self.assertIn(">Chat</button>", html)                    # plain text label, no icon child
        self.assertIn("#mtabs{display:flex;position:fixed", html)  # glued to the viewport bottom; height still = its text + padding (no fixed height)

    def test_bottom_bar_is_fixed_to_viewport_so_no_dead_space_below_it(self):
        # The recurring bug (the user, through 2026-06-19): the bar was flex-placed at the bottom of a body
        # whose height is only a viewport ESTIMATE (100dvh, then --app-h); when the estimate under-shot the
        # painted area on Android Chrome, a dead slab appeared BELOW the Chat/Feed/Timeline labels. Gluing
        # the bar to the visible viewport bottom (position:fixed;bottom:0) makes "below the bar" impossible
        # by construction, whatever the height math does. .col reserves --mtabs-h so it can't cover content.
        html = km._landing()
        self.assertIn("#mtabs{display:flex;position:fixed;left:0;right:0;bottom:0", html)
        self.assertIn("padding-bottom:var(--mtabs-h", html)
        self.assertNotIn("#mtabs{flex:", html)                   # the bar is NOT itself a flex child anymore
        # the reservation is measured from the live bar, so it tracks the gesture-area inset exactly
        self.assertIn("setProperty('--mtabs-h'", km._LANDING_MOBILE_JS)
        self.assertIn("offsetHeight", km._LANDING_MOBILE_JS)

    def test_landing_disables_browser_pinch_zoom(self):
        # the top document governs pinch-zoom for the whole visual viewport (incl. the timeline iframe), so
        # it must disable page zoom or iOS page-zooms on a timeline pinch instead of running the gesture.
        html = km._landing()
        self.assertIn("user-scalable=no", html)
        self.assertIn("maximum-scale=1", html)

    def test_landing_avoids_viewport_fit_cover(self):
        # regression (the user 2026-06-17): viewport-fit=cover made Android Chrome report a non-zero
        # env(safe-area-inset-bottom) even though the viewport already sits above the nav bar, so #mtabs's
        # safe-area padding-bottom rendered as a dead slab below the Chat/Feed/Timeline labels (and cover
        # clipped the top under the status bar). The default viewport auto-insets clear of system UI and
        # zeroes env() on Chrome, so the STATIC meta must NOT request cover.
        #
        # Narrowed, not repealed, for the installable app (plans/ios-app.md; the user 2026-08-07): an iOS
        # home-screen app has no browser chrome keeping #mtabs off the home indicator, and iOS only
        # populates env() under cover — so the head script flips cover on AT RUNTIME, gated on
        # navigator.standalone, which is iOS-only and standalone-only. No Android browser can ever take
        # that branch, so the 2026-06-17 regression cannot recur through it.
        html = km._landing()
        self.assertIn("<meta name=viewport content='width=device-width,initial-scale=1,"
                      "maximum-scale=1,user-scalable=no,interactive-widget=resizes-content'>", html)   # the static meta: no cover
        self.assertEqual(html.count("viewport-fit=cover"), 1)         # exactly the runtime flip…
        self.assertIn("if(navigator.standalone)", html)               # …behind the iOS-standalone gate
        self.assertLess(html.index("if(navigator.standalone)"), html.index("viewport-fit=cover"))
        self.assertIn("100dvh", html)            # still address-bar-aware
        self.assertIn("user-scalable=no", html)  # pinch-zoom governance preserved alongside the change

    def test_keyboard_shrinks_content_and_never_strands_a_scroll(self):
        """The composer tap used to scroll the whole shell up behind the soft keyboard (the user
        2026-09-02): the viewport's default mode is resizes-visual — the keyboard PANS the visual
        viewport while innerHeight stands still, the UA slides the page up to reveal the input, and
        fit() re-lays the shrunken --app-h top-anchored into a window whose visible band starts a
        keyboard-height down; the composer sat off-screen until dragged back. Two halves, both
        pinned: interactive-widget=resizes-content makes engines that honor it (Android Chrome)
        SHRINK the layout viewport instead of panning, and fit() undoes the stray page offset iOS
        still forces (a UA input-reveal scroll bypasses overflow:hidden)."""
        self.assertIn("interactive-widget=resizes-content", km._landing())
        self.assertIn("if(window.scrollY||document.documentElement.scrollTop)window.scrollTo(0,0);",
                      km._LANDING_MOBILE_JS)

    def test_bottom_bar_has_no_safe_area_padding(self):
        # The user 2026-06-19 (Firefox/Android): the bar showed a dead slab below the labels in PORTRAIT
        # only. The Android nav bar is at the BOTTOM in portrait (env(safe-area-inset-bottom) > 0) and on
        # the SIDE in landscape (inset = 0), and Firefox — unlike Chrome — does NOT zero that inset without
        # viewport-fit=cover, so #mtabs's padding-bottom:env(safe-area-inset-bottom) rendered as a
        # portrait-only slab. Without cover the viewport already clears the nav bar, so the padding is
        # redundant on the BROWSER bar, which stays inset-free.
        #
        # The one sanctioned exception (plans/ios-app.md; the user 2026-08-07): installed on an iOS home
        # screen the browser chrome is gone and the bar must clear the home indicator, so a single rule
        # keyed on html.ios-standalone — a class set only under navigator.standalone, which no Android
        # browser exposes — reclaims the inset there and nowhere else.
        html = km._landing()
        self.assertEqual(html.count("env(safe-area-inset"), 1)        # exactly the standalone rule below
        self.assertIn("html.ios-standalone #mtabs{padding-bottom:env(safe-area-inset-bottom,0px)}", html)
        self.assertIn("#mtabs{display:flex;position:fixed;left:0;right:0;bottom:0", html)

    def test_the_shell_forwards_a_quote_seed_from_a_composerless_pane_into_the_chat(self):
        # A passage selected in the file viewer hosted by the FEED pane (the file browser's document)
        # has no composer in its own document; file-view.ts posts the editorSelection message up to the
        # shell, which forwards it whole into the chat iframe, whose existing handler seeds the labeled
        # quote chip for m.sid. The arm sits in the same listener as the browseFiles relay, and the chat
        # iframe carries the id it keys on.
        js = km._LANDING_SETTINGS_JS
        self.assertIn("if(m.type==='editorSelection'&&typeof m.text==='string'){var fc=document.getElementById('f-chat');", js)
        self.assertIn("try{fc&&fc.contentWindow&&fc.contentWindow.postMessage(m,'*');}catch(e){}}", js)
        self.assertIn("<iframe id=f-chat class=m-on src=/chat>", km._landing())


class TimelineTouchSurface(unittest.TestCase):
    def test_timeline_fits_svg_and_drops_overflow_scroller_on_touch(self):
        # regression: the view forces a >=640px SVG; on a ~390px phone the default overflow-x:auto turned
        # that into a native horizontal scroller that beat the one-finger pan gesture. On a touch device the
        # SVG must fit the screen (width:100%) with no overflow scroller, so the gesture owns horizontal pan.
        # The wrapper styles moved to ui/webview/timeline-pane.css (shared with the VS Code view); the
        # kernel reads that file live, so pin the served page rather than a constant.
        css = km._timeline_page()
        self.assertIn("@media (pointer:coarse)", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn("touch-action:pan-y", css)
        self.assertIn(".romp-tl-wrap svg{width:100%", css)


class ChatSessionPicker(unittest.TestCase):
    def test_chat_page_collapses_tabs_into_a_header_on_mobile(self):
        chat = km._chat_page()
        self.assertIn("#mhdr", chat)                        # the compact header replaces the tab strip
        self.assertIn("#tabbar #tabs{display:none}", chat)  # the wrapping multi-row tab strip is hidden
        self.assertIn("id='mcur'", km._CHAT_MOBILE_JS)      # current-session button that opens the list
        self.assertIn("id='mlist'", km._CHAT_MOBILE_JS)     # the dropdown list of sessions

    def test_desktop_hides_the_mobile_header_and_list(self):
        # regression: #mlist (a #tabbar sibling, not inside #mhdr) had no desktop rule, so on desktop it
        # rendered the session rows as plain text in the tab bar. Both must be hidden off-mobile.
        self.assertIn("#mhdr,#mlist{display:none}", km._CHAT_MOBILE_CSS)

    def test_picker_is_custom_colored_not_native_select(self):
        # a native <select> can't render the per-session identity colors, so the picker is our own element
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertNotIn("createElement('select')", js)   # not native
        self.assertIn("--chip-bg", js)                    # reads each session's identity color
        self.assertIn("#mcur.colored", css)               # the current button wears that color

    def test_a_remote_sessions_host_prefix_stays_quiet_metadata(self):
        # the user 2026-07-30, on a phone: "host:name" came through the picker painted whole in the
        # session's identity colour at full weight, where desktop renders the "host:" as quiet metadata
        # (host-prefix.ts: dim, italic, never bold, a step smaller). The cause was textContent flattening
        # the desktop label — which already carries a <span class="host-prefix"> — so the fix CLONES the
        # label's child nodes instead of re-deriving the split, keeping ONE definition of the treatment.
        js = km._CHAT_MOBILE_JS
        self.assertIn("function fillName(elm,s){", js)
        self.assertIn("s.lab.cloneNode(true).childNodes", js, "the nodes are cloned, not flattened")
        # ...and the clone's childNodes are SLICED before the walk: appending straight off that live
        # NodeList removes each node as it goes and skips every second one, which dropped the session
        # name and left a row reading just "host:" (caught in a browser, not by a source pin)
        self.assertIn("[].slice.call(s.lab.cloneNode(true).childNodes).forEach(", js)
        self.assertIn("lab:lab,", js, "read() carries the label element through")
        self.assertIn("fillName(nm,act);", js, "the current-session button too, not just the rows")
        self.assertNotIn("nm.textContent=act.name", js)
        self.assertNotIn("lbl.textContent=s.name", js)
        # the treatment itself is NOT re-declared here: the cloned span brings its class, and the chat
        # page loads the sheet that styles it (a second spelling would eventually disagree with desktop)
        self.assertNotIn("host-prefix", km._CHAT_MOBILE_CSS)
        self.assertIn("<link href=/dist/styles.css", km._chat_page())

    def test_picker_rows_match_desktop_colored_name_plus_status_dot(self):
        # the user 2026-07-22: on mobile the picker painted a per-session identity DOT (grey #666 when the
        # session had no color, and confusingly the identity color when it did) — desktop has no such dot.
        # Match desktop: the identity color tints the NAME text (inline, like the Fleet list / colored tab
        # label), and the dot MIRRORS the tab's own status dot — gold when working, GREEN when awaitingBg
        # (idle-waiting-on-bg-work, the .tab-dot.await), none otherwise.
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn("fillName(lbl,s);lbl.style.color=s.bg||'';", js)   # identity color on the NAME (in-place form also CLEARS a dropped color)
        self.assertIn("if(!wd){wd=document.createElement('span');wd.className='workdot';", js)   # in-place form: one dot node, created once, classes toggled (2026-08-19)  # gold dot when working
        # awaitingBg is read off the desktop tab's own green dot (no tab-working class on an awaiting tab)
        self.assertIn("awaitbg:!!t.querySelector('.tab-dot.await')", js)
        self.assertIn("wd.classList.toggle('await',!s.working&&!!s.awaitbg);", js)  # green dot when awaiting (in-place toggle form, 2026-08-19)
        self.assertNotIn(".mrow .dot{", css)              # the old identity/grey dot is gone
        self.assertNotIn("dot.style.background=s.bg", js)  # ...and nothing paints identity onto a dot
        # the dots are the SAME status colors desktop uses (styles.css --st-working-bg gold, --st-awaitbg-bg green)
        self.assertIn(".mrow .workdot{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:var(--st-working-bg,#e0b020)}", css)
        self.assertIn(".mrow .workdot.await{background:var(--st-awaitbg-bg,#54B204)}", css)
        self.assertNotIn("'• ')+s.name", js)              # the '• ' text-bullet prefix on rows is gone
        # the current-session header uses the same gold/green status dot, not the text bullet either
        self.assertIn("#mcur .wd{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:var(--st-working-bg,#e0b020)}", css)
        self.assertIn("#mcur .wd.await{background:var(--st-awaitbg-bg,#54B204)}", css)
        self.assertIn("wd.style.display=(act&&(act.working||act.awaitbg))?'':'none'", js)
        self.assertIn("wd.classList.toggle('await',!!(act&&act.awaitbg&&!act.working))", js)
        self.assertNotIn("(act.working?'• ':'')", js)

    def test_current_session_title_is_bold_color_on_the_grey_chip(self):
        # the user 2026-07-22: the mobile current-session title reads as the identity color in BOLD on the
        # SAME grey chip as the +/madd button, with a hairline color border, not the color as a fill.
        # (The chip grey rides --btn-bg since 2026-09-02 — dark value byte-identical to the old #2a2a2a
        # literal, and the light theme re-skins it; see test_kernel_mobile_picker's token test.)
        css = km._CHAT_MOBILE_CSS
        self.assertIn("#mcur.colored{background:var(--btn-bg,#2a2a2a);color:var(--cbg);border-color:var(--cbg)}", css)
        self.assertIn("#madd{flex:0 0 auto", css)                    # ...and the + button is that same grey
        self.assertIn("background:var(--btn-bg,#2a2a2a);color:#bbbbbb", css)   # (the shared chip grey)
        self.assertIn("white-space:nowrap;font-weight:700}", css)   # the .nm name span is bold

    def test_no_pane_focus_ring_on_mobile(self):
        # the user 2026-07-22: only one pane shows at a time on mobile, so the "which pane is focused" ring
        # is meaningless — it just draws a blue border around the whole view. It's suppressed in the mobile
        # media query while the desktop grid (many panes at once) keeps it.
        html = km._landing()
        self.assertIn(".pane.pane-focused::after{display:none}", html)   # killed on mobile
        # the desktop ring itself still exists (outside the media query)
        self.assertIn(".pane.pane-focused::after{content:'';", html)

    def test_picker_is_gated_on_touch_not_pane_width(self):
        # regression: the chat iframe is one of three desktop panes, so it's ALWAYS narrow. A bare
        # max-width breakpoint matched that narrow pane and swapped the mobile picker in on desktop,
        # replacing the real tab strip. The picker must trigger only on a touch device (pointer:coarse).
        css = km._CHAT_MOBILE_CSS
        self.assertIn("@media (pointer:coarse) and (max-width:1024px){", css)
        self.assertNotIn("(max-width:820px),", css)   # the width-only OR-clause that leaked onto desktop is gone

    def test_picker_routes_a_pick_and_wires_new_session(self):
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn(".tab[data-id", js)             # a row tap clicks the real tab (render.js focuses it)
        self.assertIn("MutationObserver", js)         # re-syncs as tabs change
        self.assertIn(".tab-add", js)                 # + → open / new session
        # the dead "Toggle summary" button (#mcoll → .tab-collapse) is GONE (the user 2026-07-22): the
        # ledger/summary strip it toggled was retired, so .tab-collapse never existed in the DOM produced
        # by render.ts and the button did nothing. Nothing left to rewire it to, so it was removed.
        self.assertNotIn("mcoll", js)
        self.assertNotIn(".tab-collapse", js)
        self.assertNotIn("#mcoll", css)

    def test_picker_rows_have_an_end_session_control(self):
        # the user 2026-07-22: the mobile picker had no way to end a session (desktop has the tab x). Add a
        # per-row x that clicks the hidden desktop tab's own .tab-close, reusing its confirm dialog
        # (Close tab / End session / Cancel) + the endSession/closeTab plumbing — no new backend.
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn("x.className='mclose'", js)
        self.assertIn("x.title='End session'", js)
        # it triggers the real tab's close x, and stops propagation so it doesn't also switch sessions
        self.assertIn("var rtc=realTab(id);var c=rtc&&rtc.querySelector('.tab-close');if(c)c.click();", js)   # delegated form (2026-08-19 click-safe rewrite)
        self.assertIn("e.stopPropagation();hide();", js)
        self.assertIn(".mrow .mclose{", css)


class RevealRouting(unittest.TestCase):
    def test_a_reveal_focuses_the_askers_chat_and_nudges_its_shell(self):
        # the chat focus + the shell's switch-to-Chat travel as a pair, both addressed to the asker's wid
        sent = []
        orig = km._send_to_view
        km._send_to_view = lambda app, msg, wid: sent.append((app, wid, msg))
        try:
            km._reveal_chat_for({"app": "feed", "wid": "win-A"}, {"type": "focus", "id": "s1"})
        finally:
            km._send_to_view = orig
        self.assertEqual([(a, w) for a, w, _ in sent], [("chat", "win-A"), ("shell", "win-A")],
                         "chat focus AND mobile-shell nudge, the asker's window only")
        chat_msg = next(m for a, _, m in sent if a == "chat")
        shell_msg = next(m for a, _, m in sent if a == "shell")
        self.assertEqual(chat_msg["id"], "s1")        # the original focus payload is preserved verbatim
        self.assertEqual(shell_msg, {"type": "reveal", "pane": "chat"})


# A node stand-in for the phone: the shell's mobile script runs against a stub window whose visual
# viewport the driver shrinks and grows by hand, so the fit's INPUTS are exact and its OUTPUT (the
# --app-h / --mtabs-h it publishes) is read back. Same shape as test_kernel_webpush's reveal harness.
_FIT_HARNESS = r"""
'use strict';
const PROPS = {}, SETS = [], RAF = [], WIN = {}, DOC = {}, VV = {}, CHAT = {}, LOADS = [];
const on = (book) => (k, f) => { (book[k] = book[k] || []).push(f); };
global.window = global;
global.innerHeight = 844; global.innerWidth = 390; global.scrollY = 0;
global.scrollTo = () => {};
global.matchMedia = () => ({ matches: true });                 // a coarse pointer: the phone
global.requestAnimationFrame = (f) => { RAF.push(f); return RAF.length; };
global.addEventListener = on(WIN);
global.visualViewport = { height: 844, scale: 1, addEventListener: on(VV) };
const pane = (id) => ({ id, classList: { toggle() {} }, contentDocument: {},
  contentWindow: { addEventListener: on(id === 'f-chat' ? CHAT : {}) },
  addEventListener: (k) => { if (k === 'load') LOADS.push(id); } });
const PANES = { 'f-chat': pane('f-chat'), 'f-fleet': pane('f-fleet'), 'f-feed': pane('f-feed'), 'f-timeline': pane('f-timeline') };
const BAR = { offsetHeight: 44, querySelectorAll: () => [] };
global.document = {
  visibilityState: 'visible',
  addEventListener: on(DOC),
  documentElement: { scrollTop: 0, style: { setProperty: (k, v) => { PROPS[k] = v; SETS.push(k); } } },
  body: { setAttribute() {} },
  getElementById: (id) => (id === 'mtabs' ? BAR : (PANES[id] || null)),
};
global.localStorage = { getItem: () => null, setItem() {} };
"""
_FIT_DRIVER = r"""
const fire = (book, k) => (book[k] || []).forEach((f) => f({}));
const flush = () => { RAF.splice(0).forEach((f) => f(0)); };            // one frame: run what this frame queued
const appH = () => PROPS['--app-h'], barH = () => PROPS['--mtabs-h'];
const fits = () => SETS.filter((k) => k === '--app-h').length;
const out = {};
out.bound = { win: Object.keys(WIN).sort(), doc: Object.keys(DOC).sort(), vv: Object.keys(VV).sort(),
  chat: Object.keys(CHAT).sort(), loads: LOADS.slice().sort() };
out.boot = { appH: appH(), barH: barH(), rafPending: RAF.length };
// the keyboard slides up: iOS shrinks the visual viewport while innerHeight stands still
visualViewport.height = 460; fire(VV, 'resize'); flush();
out.kbUp = { appH: appH(), barH: barH() };
// the keyboard goes away and NO viewport event arrives: the composer's blur is the only word
visualViewport.height = 844; fire(CHAT, 'focusout');
out.blurBeforeFrame = appH();
flush();
out.kbDown = { appH: appH(), barH: barH() };
// resume: backgrounded with the keyboard up; iOS dropped it while the page was frozen, no event delivered
visualViewport.height = 460; fire(VV, 'resize'); flush();
document.visibilityState = 'hidden'; fire(DOC, 'visibilitychange');
const beforeHidden = fits(); flush(); out.hiddenFits = fits() - beforeHidden;
visualViewport.height = 844; document.visibilityState = 'visible'; fire(DOC, 'visibilitychange'); flush();
out.resume = { appH: appH(), barH: barH() };
// resume where iOS reports the FINAL geometry a beat late: the fit at visible reads the stale height,
// and the visual-viewport resize that follows is what corrects it
visualViewport.height = 460; fire(VV, 'resize'); flush();
document.visibilityState = 'hidden'; fire(DOC, 'visibilitychange'); flush();
document.visibilityState = 'visible'; fire(DOC, 'visibilitychange'); flush();
out.resumeStale = appH();
visualViewport.height = 844; fire(VV, 'resize'); flush();
out.resumeSettled = appH();
// a burst of events fits ONCE, on the next frame
const before = fits();
fire(VV, 'resize'); fire(VV, 'scroll'); fire(WIN, 'resize'); fire(WIN, 'focus'); fire(WIN, 'pageshow'); fire(DOC, 'focusout');
out.burst = { beforeFlush: fits() - before, pendingRafs: RAF.length };
flush();
out.burst.afterFlush = fits() - before;
// every bound event refits on its own, reading the geometry fresh each time
const each = {};
for (const [book, k, tag] of [[WIN, 'focus', ''], [WIN, 'pageshow', ''], [WIN, 'orientationchange', ''], [WIN, 'resize', ''],
                              [VV, 'scroll', '@vv'], [VV, 'resize', '@vv'], [DOC, 'focusout', '@doc']]) {
  visualViewport.height = 700; fire(book, k); flush(); const a = appH();
  visualViewport.height = 844; fire(book, k); flush(); each[k + tag] = [a, appH()];
}
out.each = each;
// a page offset the UA forced (iOS's input reveal) is undone on the same frame
global.scrollY = 120; let scrolled = null; global.scrollTo = (x, y) => { scrolled = [x, y]; global.scrollY = 0; };
fire(VV, 'scroll'); flush(); out.scrollReset = scrolled;
console.log(JSON.stringify(out));
"""


class MobileFitExecutes(unittest.TestCase):
    """The installed iPhone app came back from the background with the chat pane filling only the
    top ~60% of the screen: the composer mid-screen, a keyboard-tall blank band under it, the tab
    bar at the very bottom (the user 2026-09-08). --app-h had been measured while the keyboard was
    up and nothing re-measured it: the keyboard fell while the page was frozen, so the visual
    viewport's resize (the only keyboard event the fit listened to) never arrived, and on iOS the
    same resize sometimes fails to fire for a keyboard the composer's blur dismissed. The fix binds
    the fit to every event that moves the real viewport (visibilitychange, window focus, the
    composer's focusout heard through the same-origin pane window, alongside the resize / scroll /
    orientationchange / pageshow it already had), coalesces a burst to one fit per animation frame,
    and always recomputes from scratch, so a viewport that grows back is never left short."""

    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_FIT_HARNESS + km._LANDING_MOBILE_JS + _FIT_DRIVER)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the mobile script threw: " + r.stderr[:800]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_fit_is_bound_to_every_event_that_moves_the_real_viewport(self):
        b = self.out["bound"]
        for ev in ("resize", "orientationchange", "pageshow", "focus"):
            self.assertIn(ev, b["win"], ev)
        for ev in ("visibilitychange", "focusout"):
            self.assertIn(ev, b["doc"], ev)
        self.assertEqual(b["vv"], ["resize", "scroll"])
        # the composer lives in the chat pane's document: its blur (keyboard dismissal) is heard
        # through the same-origin pane window, wired now and again on every (re)load
        self.assertEqual(b["chat"], ["focusout"])
        self.assertIn("f-chat", b["loads"])

    def test_boot_fits_at_once_from_the_visual_viewport(self):
        # the first paint is right without waiting a frame; the bar's reservation is measured too
        self.assertEqual(self.out["boot"], {"appH": "844px", "barH": "44px", "rafPending": 0})

    def test_the_keyboard_shrinks_the_shell_and_the_composers_blur_alone_grows_it_back(self):
        self.assertEqual(self.out["kbUp"], {"appH": "460px", "barH": "0px"})
        self.assertEqual(self.out["blurBeforeFrame"], "460px", "events schedule a frame; they do not fit inline")
        self.assertEqual(self.out["kbDown"], {"appH": "844px", "barH": "44px"})

    def test_coming_back_to_the_foreground_refits_a_keyboard_that_fell_while_hidden(self):
        self.assertEqual(self.out["hiddenFits"], 0, "going hidden is not new geometry")
        self.assertEqual(self.out["resume"], {"appH": "844px", "barH": "44px"})

    def test_a_resume_whose_final_geometry_lands_late_is_fitted_twice(self):
        # belt and braces: the fit at visible reads what iOS reports then; the visual-viewport resize
        # that follows a beat later is bound permanently, so it is the second fit — no timer
        self.assertEqual(self.out["resumeStale"], "460px")
        self.assertEqual(self.out["resumeSettled"], "844px")

    def test_a_burst_of_viewport_events_fits_once_per_frame(self):
        self.assertEqual(self.out["burst"], {"beforeFlush": 0, "pendingRafs": 1, "afterFlush": 1})

    def test_each_bound_event_refits_from_scratch_on_its_own(self):
        for ev, seen in self.out["each"].items():
            self.assertEqual(seen, ["700px", "844px"], ev)

    def test_a_ua_forced_page_offset_is_undone_on_the_same_frame(self):
        self.assertEqual(self.out["scrollReset"], [0, 0])


# A node stand-in for the installed phone app with a REAL class list: the shell's mobile script and
# its bell script run in the shell's own order against a stub DOM whose elements keep classes,
# attributes and listeners, so the bell's `.on` (the slash rule keys on its absence) is state read
# back after each transition — a tab tap, a reveal, the kernel's notifyAll frame, the popover's own
# switches — not a pin on the source text. Same shape as the fit harness above.
_BELL_HARNESS = r"""
'use strict';
class El {
  constructor(tag, attrs) { this.tag = tag; this.attrs = Object.assign({}, attrs || {}); this.children = []; this.parentNode = null;
    this.hidden = false; this.style = {}; this.textContent = ''; this.disabled = false; this.L = {}; this.contentDocument = null;
    const cls = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean));
    this.classList = { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
      toggle: (c, f) => { const on = f === undefined ? !cls.has(c) : !!f; if (on) cls.add(c); else cls.delete(c); return on; } }; }
  get id() { return this.attrs.id || ''; }
  append(...k) { k.forEach((c) => { c.parentNode = this; this.children.push(c); }); return this; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, f) { (this.L[k] = this.L[k] || []).push(f); }
  fire(k, ev) { const e = Object.assign({ target: this }, ev || {}); for (let n = this; n; n = n.parentNode) (n.L[k] || []).forEach((f) => f(e)); }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  matches(sel) {                                   // one compound selector: tag, #id, .class, [attr], [attr=val]
    const m = sel.match(/^([a-z]*)((?:[#.][\w-]+|\[[\w-]+(?:=[\w-]+)?\])*)$/); if (!m) throw new Error('selector ' + sel);
    if (m[1] && m[1] !== this.tag) return false;
    for (const p of m[2].match(/[#.][\w-]+|\[[^\]]+\]/g) || []) {
      if (p[0] === '#') { if (this.id !== p.slice(1)) return false; }
      else if (p[0] === '.') { if (!this.classList.contains(p.slice(1))) return false; }
      else { const [k, v] = p.slice(1, -1).split('='); if (!(k in this.attrs) || (v !== undefined && this.attrs[k] !== v)) return false; } }
    return true; }
  querySelectorAll(sel) { const parts = sel.split(',').map((s) => s.trim()); return this.all().filter((e) => parts.some((p) => e.matches(p))); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() { return { top: 800, right: 380, bottom: 844, left: 340 }; }
}
// the shell's markup, reduced to the nodes the two scripts touch
const root = new El('html'), body = new El('body'); root.append(body);
['f-chat', 'f-fleet', 'f-feed', 'f-timeline'].forEach((id) => { const p = new El('iframe', { id }); p.contentWindow = { addEventListener() {} }; body.append(p); });
const railBell = new El('div', { class: 'rail-act', id: 'rail-bell' }); railBell.hidden = true;
body.append(new El('div', { class: 'rail-acts' }).append(railBell));
const bar = new El('nav', { id: 'mtabs' }); bar.offsetHeight = 44;
['chat', 'fleet', 'feed', 'timeline'].forEach((k) => bar.append(new El('button', k === 'chat' ? { 'data-pane': k, class: 'on' } : { 'data-pane': k })));
bar.append(new El('span', { class: 'mtabs-div' }));
['usage', 'net', 'restart', 'errs'].forEach((a) => bar.append(new El('button', { class: 'mact', 'data-act': a })));
const mbell = new El('button', { class: 'mact', id: 'mbell' }); mbell.hidden = true; bar.append(mbell);
bar.append(new El('button', { class: 'mact', 'data-act': 'settings' }));
body.append(bar);
const back = new El('div', { id: 'rbell-back' }); back.hidden = true;
const pop = new El('div', { id: 'rbell-pop' }), rows = {};
['all', 'dev', 'turns'].forEach((a) => { rows[a] = new El('div', { class: 'rbp-row', 'data-act': a }).append(new El('span', { class: 'rbp-sw' })); pop.append(rows[a]); });
pop.append(new El('div', { id: 'rbp-dev-sub' }), new El('button', { id: 'rbp-test', 'data-act': 'test' }), new El('div', { id: 'rbp-test-out' }));
back.append(pop); body.append(back);
// the window: a coarse pointer, a live shell socket the driver feeds frames into
const WIN = {}, WSS = [], POSTS = [];
const on = (book) => (k, f) => { (book[k] = book[k] || []).push(f); };
global.window = global;
global.innerHeight = 844; global.innerWidth = 390; global.scrollY = 0; global.scrollTo = () => {};
global.matchMedia = () => ({ matches: true });
global.requestAnimationFrame = () => 1;
global.addEventListener = on(WIN);
global.visualViewport = { height: 844, scale: 1, addEventListener() {} };
global.document = { visibilityState: 'visible', addEventListener() {}, body,
  documentElement: { scrollTop: 0, style: { setProperty() {} } },
  getElementById: (id) => root.querySelector('#' + id),
  querySelectorAll: (s) => root.querySelectorAll(s), querySelector: (s) => root.querySelector(s) };
global.localStorage = { getItem: () => null, setItem() {} };
global.location = { protocol: 'https:', host: 'TESTHOST' };
global.WebSocket = class { constructor(u) { this.url = u; WSS.push(this); } send() {} };
// the Push API: this browser holds a subscription at boot; the popover's row can drop and re-take it
let SUB = null;
const mkSub = (n) => ({ endpoint: 'https://push.example/dev-' + n, unsubscribe() { SUB = null; return Promise.resolve(true); }, toJSON() { return { endpoint: this.endpoint }; } });
SUB = mkSub(1);
const REG = { pushManager: { getSubscription: () => Promise.resolve(SUB), subscribe: () => { SUB = mkSub(2); return Promise.resolve(SUB); } } };
Object.defineProperty(global, 'navigator', { configurable: true, value:   // node 22's own navigator is a getter-only global
  { serviceWorker: { getRegistration: () => Promise.resolve(REG), register: () => Promise.resolve(REG), ready: Promise.resolve(REG) } } });
global.PushManager = function () {};
global.Notification = { permission: 'granted', requestPermission: () => Promise.resolve('granted') };
const GETS = { '/notify-all': { on: true }, '/notify-turns': { on: false }, '/push/vapid-key': { key: 'BAAA' } };
global.fetch = (path, init) => { const post = !!(init && init.method === 'POST'); if (post) POSTS.push([path, JSON.parse(init.body)]);
  const b = post ? { ok: true } : (GETS[path] || {});
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(b), text: () => Promise.resolve(JSON.stringify(b)) }); };
"""
_BELL_DRIVER = r"""
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const sw = (k) => rows[k].querySelector('.rbp-sw').classList.contains('on');
const state = () => ({ mbell: mbell.classList.contains('on'), rail: railBell.classList.contains('on'),
  title: [mbell.getAttribute('title'), railBell.getAttribute('title')], rows: { all: sw('all'), dev: sw('dev') },
  tabs: bar.querySelectorAll('button[data-pane]').filter((b) => b.classList.contains('on')).map((b) => b.getAttribute('data-pane')) });
const ws = () => WSS[WSS.length - 1];
const frame = (m) => ws().onmessage({ data: JSON.stringify(m) });
const post = (m) => (WIN.message || []).forEach((f) => f({ data: m }));
(async () => {
  const out = {};
  (0, eval)(MOBILE_JS); (0, eval)(PUSH_JS);          // the shell's order: the tab bar's script parses first, the bell's after it
  out.revealed = { mbell: !mbell.hidden, rail: !railBell.hidden };
  await settle();
  out.boot = state();                                // master on + subscribed: lit, and no popover was opened
  bar.querySelector('button[data-pane=feed]').fire('click'); out.tabTap = state();
  post({ romp: 'reveal', pane: 'chat' });            out.revealMsg = state();
  frame({ type: 'reveal', pane: 'timeline' });       out.revealFrame = state();
  post({ romp: 'toggleFleet', to: 'fleet' });        out.listJump = state();   // the chat header's jump to the sessions list
  frame({ type: 'notifyAll', on: false }); out.masterOffFrame = state();
  frame({ type: 'notifyAll', on: true });  out.masterOnFrame = state();
  rows.dev.fire('click'); await settle(); out.devOff = state();          // the popover's This-device row: unsubscribes
  bar.querySelector('button[data-pane=chat]').fire('click'); out.devOffTabTap = state();
  rows.dev.fire('click'); await settle(); out.devOn = state();           // …and re-subscribes
  rows.all.fire('click'); await settle(); out.masterOffRow = state();    // the master row itself
  rows.all.fire('click'); await settle(); out.masterOnRow = state();
  bar.querySelector('button[data-pane=feed]').fire('click'); out.finalTabTap = state();
  out.posts = POSTS.map((p) => p[0]);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


class MobileBellExecutes(unittest.TestCase):
    """The installed iPhone app, light theme (the user 2026-09-08): the bell popover showed all three
    switches on — Notifications, This device, the turn-finished one — while the tab bar's bell wore
    the OFF slash. The bell script paints `.on` on both bells from the master + this device's
    subscription and was right at boot; then the tab bar's switcher took it away. `show(p)` ran over
    EVERY button in #mtabs — the pane tabs, the action buttons and the bell among them — toggling
    `.on` to `data-pane===p`, which for the bell (no data-pane) is always off. So every pane switch
    (a tab tap, a notification's reveal, the chat header's jump to the sessions list) slashed a subscribed bell until the next
    paint event, while the popover's rows, painted from the same state, still said on. The switcher
    now owns only the pane tabs (`button[data-pane]`); the bell's class is the bell script's alone."""

    @classmethod
    def setUpClass(cls):
        src = "const MOBILE_JS=%s;const PUSH_JS=%s;" % (json.dumps(km._LANDING_MOBILE_JS), json.dumps(km._LANDING_PUSH_JS))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_BELL_HARNESS + src + _BELL_DRIVER)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the shell scripts threw: " + r.stderr[:1200]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    LIT = "Notifications on — tap for options"

    def _lit(self, s, where):
        self.assertEqual((s["mbell"], s["rail"]), (True, True), where + ": both bells lit")
        self.assertEqual(s["title"], [self.LIT, self.LIT], where)

    def test_boot_lights_a_subscribed_device_without_opening_the_popover(self):
        self.assertEqual(self.out["revealed"], {"mbell": True, "rail": True}, "the Push API exists here: both bells show")
        s = self.out["boot"]
        self._lit(s, "boot")
        self.assertEqual(s["rows"], {"all": True, "dev": True})
        self.assertEqual(s["tabs"], ["chat"])

    def test_a_pane_switch_leaves_the_bell_alone(self):
        # the bug: a tab tap (and every other route into show()) stripped `.on` from the bell while
        # the popover's rows still said on — the glyph and the popover disagreed
        for where in ("tabTap", "revealMsg", "revealFrame", "listJump"):
            s = self.out[where]
            self._lit(s, where)
            self.assertEqual(s["rows"], {"all": True, "dev": True}, where)
        self.assertEqual(self.out["tabTap"]["tabs"], ["feed"], "the tap still switches the pane")
        self.assertEqual(self.out["revealMsg"]["tabs"], ["chat"])
        self.assertEqual(self.out["revealFrame"]["tabs"], ["timeline"])
        self.assertEqual(self.out["listJump"]["tabs"], ["fleet"])

    def test_the_kernels_master_frame_repaints_both_bells(self):
        off = self.out["masterOffFrame"]
        self.assertEqual((off["mbell"], off["rail"]), (False, False))
        self.assertEqual(off["title"], ["Notifications off for all devices"] * 2)
        self.assertEqual(off["rows"], {"all": False, "dev": True}, "the device row keeps its own truth")
        self._lit(self.out["masterOnFrame"], "masterOnFrame")

    def test_this_devices_switch_slashes_and_relights_both_bells_and_a_tab_tap_keeps_the_slash(self):
        off = self.out["devOff"]
        self.assertEqual((off["mbell"], off["rail"]), (False, False))
        self.assertEqual(off["title"], ["Notifications off on this device"] * 2)
        self.assertEqual(off["rows"], {"all": True, "dev": False})
        # a pane switch with the device off must not light it either: the switcher paints nothing on the bell
        tap = self.out["devOffTabTap"]
        self.assertEqual((tap["mbell"], tap["rail"], tap["tabs"]), (False, False, ["chat"]))
        self.assertEqual(tap["title"], ["Notifications off on this device"] * 2)
        self._lit(self.out["devOn"], "devOn")
        self.assertEqual(self.out["devOn"]["rows"], {"all": True, "dev": True})

    def test_the_master_row_and_a_final_tab_tap(self):
        off = self.out["masterOffRow"]
        self.assertEqual((off["mbell"], off["rail"]), (False, False))
        self.assertEqual(off["title"], ["Notifications off for all devices"] * 2)
        self._lit(self.out["masterOnRow"], "masterOnRow")
        self._lit(self.out["finalTabTap"], "finalTabTap")
        self.assertEqual(self.out["finalTabTap"]["tabs"], ["feed"])
        self.assertEqual(self.out["posts"], ["/push/unsubscribe", "/push/subscribe", "/notify-all", "/notify-all"])

    def test_the_switcher_selects_only_the_pane_tabs(self):
        js = km._LANDING_MOBILE_JS
        self.assertIn("var B=bar.querySelectorAll('button[data-pane]')", js)
        self.assertNotIn("bar.querySelectorAll('button'),", js)


if __name__ == "__main__":
    unittest.main()
