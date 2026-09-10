# iOS: the dashboard as a home-screen app

Status: ALL THREE proposals IMPLEMENTED, landing in the commit that adds this file. Proposals 1
and 2 came first (manifest + icons + Apple metas + iOS-standalone safe-area, and Web Push end to
end — `/sw.js`, `/push/*`, VAPID, the `_push_notify` sink beside `_system_notify`, the tab-bar
bell), live-verified on a real iPhone on 2026-08-07; then 3 plus tap-to-open on 2026-08-08, after
the first real push opened the app on a session OTHER than the one that fired it — the bug that
motivated the routing metadata below. A push now carries the firing card's sid and the
needs-you count as routing metadata — and, since 2026-09-06, a `data` block `{sid, host, kind,
cardId, url}` plus a per-session `tag` (`_push_payload`). The notification tap lands on that
session: the SW closes the notification, focuses the window the user last had in front and posts
`{romp:'notificationClick', …}` to it (a refused `focus()` falls through to `openWindow` on the
deep link, all inside `waitUntil`); with no window it opens `url` (`/?push-reveal=<sid>
[&push-card=<id>]`). Either way the shell asks the kernel for the focus (`POST /reveal`, wid-aimed,
parked until that window's chat `ready` when the pane is not up yet — an exact event, no delay
heuristics; a dead sid gets the revive prompt, never a silent miss) and, for a card, posts
`revealCard` into the feed once it has announced its first payload; the URL params are stripped
with `replaceState`. The app icon wears the count (`navigator.setAppBadge` — the SW paints it while
the app is closed, the shell trues it up over its WS on connect and on every change).
Implementation notes that amend this sketch:
- A tap on a Home Screen app alive in the BACKGROUND reaches none of the roads above (2026-09-09, on
  a real iPhone): iOS lists no window client for it, so the worker takes `openWindow`; iOS brings the
  existing page forward without a load (no link) and without a client to message, then ends the
  worker. The worker therefore writes every session-addressed tap to the Cache API first (one entry,
  `/__romp/tap` in the `romp-tap` cache) and the shell reads it on boot, `visibilitychange`→visible,
  `pageshow` and window `focus`, landing it once by id (`via: 'store'`), deleting the entry and acking
  the worker. A boot on the deep link drops the stored tap rather than landing it twice.
- Later that day, the app WARM: three taps, three 201s from the push service, and no trace of the click
  handler at all (no message, no link, an empty store). Two things now settle whether the worker ran:
  the worker leaves a FINGERPRINT beside the tap (`/__romp/sw`: a version baked at serve time — the
  kernel's sha + dist token, the string the shell page carries too — plus install/activate/last-push/
  last-click stamps and a click count), which the shell folds into every `tap-resume` row (`sw-stale`
  on a mismatch; `registration.update()` at boot and on every visible). (An OFFER chip — "Open <name>
  · from the notification", sourced from a `/__romp/shown` record the push wrote — shipped the same
  day and was removed the same day: it named the wrong session, and the user wants no chip and no
  prompt, ever.)
- 2026-09-10, the finding read right: the worker's acks DO reach the kernel, but a live Home Screen app
  gets NEITHER `notificationclick` NOR `notificationclose` from iOS (a killed app gets the click and the
  deep link), so nothing the worker or the screen could say was ever the tap — a gone notification
  cannot tell a tap from a swipe-dismiss. The stored tap, the replay and the fingerprint are removed.
  For a KILLED app the tap is the OS's own callback: an Apple endpoint (`web.push.apple.com`) is sent a Declarative Web Push
  message — `{"web_push": 8030, "notification": {title, body, navigate, tag, data[, silent]},
  "mutable": true[, "app_badge": n]}` (`_push_declarative`; members verified against the W3C Push API
  draft and WebKit's `NotificationJSONParser.cpp`) — whose `navigate` is the deep link made absolute
  with the page origin the bell records at subscribe (`/?push-reveal=<sid>[&push-card=<id>]&push-pid=
  <pid>`). iOS displays it and, on a tap, navigates the app there; the shell lands the link at boot AND
  on pageshow/popstate (`via: 'link'`) and settles the row (`POST /push/landed`). `mutable` hands the
  worker a `push` event carrying the parsed Notification (`event.notification`), which it acks `shown`
  and does not show again. Non-Apple endpoints keep the imperative payload and the worker's
  `notificationclick` (`via: 'sw'`, or the kernel's clicked row via `GET /push/pending` — `via: 'ack'`).
  The kernel push LEDGER stays (`STATE/push-ledger.json`: `pid` per push per device; `shown`/`clicked`
  acks by pid; `landed` by the page). `tests/test_notification_tap_resume_browser.py` runs the roads
  in a real browser.
- 2026-09-10, later, the vanish road BACK by the user's call: on the device the declarative message
  landed a tap only for a KILLED app; a LIVE app (background or foreground) is merely foregrounded —
  no navigation, no click, no event of any kind. So the shell again holds the kernel's unsettled
  `shown` rows against `registration.getNotifications()` on boot/visible/pageshow/focus: exactly one
  shown push whose notification is gone lands (`via: 'vanish'`), silently; two or more gone are
  settled without landing (`POST /push/dropped`); a newer same-session notification on the screen
  supersedes an older row (`POST /push/superseded`, and the kernel does the same at the `shown` ack);
  all displayed, sent-only, or an unreadable screen decide nothing. No chip, no prompt, no `closed`
  stage (iOS never reports one), no timers. THE ACCEPTED TRADE-OFF: a swiped-away notification leaves
  the same evidence as a tapped one and lands on the next foregrounding; the user weighed that and
  decided a working background tap is worth an occasional wrong landing after a swipe. The foreground
  case stays non-switching (no wake event); a killed app still uses the native `navigate`.
- The shell background is `#1e1e1e`, not the `#101418` guessed below (that is the login page);
  the manifest and theme-color use `#1e1e1e`.
- The manifest and the three icon PNGs are served auth-EXEMPT: browsers fetch a manifest (and
  its icon list) with credentials omitted, so behind the token gate the install sheet gets a 403.
  Fixed three-name allowlist; the rest of `/media/` stays gated.
- Safe-area insets are gated to iOS standalone only (`navigator.standalone` sets
  `html.ios-standalone` and flips `viewport-fit=cover` at runtime), because the browser-mode
  cover/env() combinations regressed Android Chrome and Firefox twice in 2026-06 — see the
  comments and tests around `#mtabs`.
- Web Push's crypto rides the python `cryptography` package as the kernel's one soft dependency:
  `/push/subscribe` answers a plain 500 naming it when missing (fail loudly), and CI installs it
  so the tests run rather than skip.

The user's ask (2026-08-07): they run the dashboard on an iPhone in a browser over their tailnet
and want it to feel like a "main app" — its own icon, launched full-screen, present in the app
switcher — rather than a tab. This doc records what that takes and stacks the related
iOS-specific enhancements so they get decided together.

## Verified findings (2026-08-07, read of the tree at 7cff0391)

1. **The pages already speak mobile, but not "installable".** Every kernel-served page carries a
   `viewport` meta and the shell has real touch work (single full-screen pane + bottom tab bar on
   narrow viewports, `100dvh` pinning, pinch handling, a mobile actions row). What is MISSING is
   everything Safari/Chrome consult for home-screen installs: there is no web app manifest, no
   `apple-mobile-web-app-capable` / status-bar meta, and no `apple-touch-icon` — the only icon is
   the SVG favicon (`/media/romp-swirl-glyph.svg`), which iOS ignores for touch icons (it wants
   PNG at fixed sizes). Net: "Add to Home Screen" works today but yields a bookmark that opens as
   a browser tab, chrome and all.
2. **Auth already fits the install flow, with one wrinkle.** The dashboard rides the serve token:
   first visit seeds a year-long `romp_token` cookie (`SameSite=Strict; HttpOnly`), and an
   unauthenticated GET of `/` renders a self-contained paste-the-token login page. A home-screen
   web app on iOS gets its OWN cookie container — the browser's cookie does not carry over — so
   the first launch from the icon lands on that login page once, then holds its own year cookie.
   That is the security model working as designed; the plan changes nothing about it, but the
   login page is the first thing the installed app shows, which is worth knowing before anyone
   calls it a bug.
3. **Notifications never reach the phone today.** The bell system (session `notify` flag, per-card
   bells in `notify-cards.json`) fires `_system_notify` — `osascript` on macOS, `notify-send`
   elsewhere — i.e. OS notifications on the KERNEL's machine. The detection is already exactly
   the right event (a fresh feed build diffed against the previous one, armed cards entering
   needs-you/completed); only the delivery is desk-bound.
4. **iOS gates Web Push on installation.** Since iOS 16.4, web apps can receive push
   notifications — but ONLY once added to the home screen (and served over HTTPS, which the
   tailnet proxy already provides). So proposal 1 is also the prerequisite for proposal 2.

## Proposal 1 — installable app: manifest + Apple meta + touch icons (small)

- A `/manifest.webmanifest` route on the kernel: name/short name, `start_url: /`,
  `display: standalone`, `background_color`/`theme_color` matching the shell (`#101418`), icons.
- PNG renders of the swirl glyph at 180 (apple-touch-icon), 192 and 512 px, served under
  `/media/`, checked in as assets (eyeball them before release like the other `docs/assets`
  media — they are images, outside the text-only privacy scans).
- `<link rel=manifest>`, `<link rel=apple-touch-icon>`, `apple-mobile-web-app-capable` and
  status-bar-style metas on the LANDING SHELL only — the iframes (feed/chat/timeline) are panes
  inside it, never install targets.
- Safe-area insets (`env(safe-area-inset-*)`) on the shell's bottom tab bar / rail, since
  standalone mode removes the browser chrome that used to keep it off the home indicator.
- No service worker in this step: romp is useless offline by nature (everything is a live kernel
  feed), and a cache-first worker would fight the existing stale-bundle detection. Installability
  on iOS does not require one.

Interaction with the pending clickjacking hardening (HANDOFF: add
`frame-ancestors 'self'` / `X-Frame-Options: SAMEORIGIN`): no conflict — a home-screen app is a
top-level load, not a frame, and the shell's own same-origin iframes stay allowed. Landing both
in the same season means one live-client verification pass covers the two.

## Proposal 2 — needs-you pushes to the phone (medium, after 1)

Deliver the bell events to installed home-screen apps via Web Push, so "blocked on you" reaches
the person wherever they are — the philosophy's "interrupt only when the human is the
bottleneck", extended to the pocket. Sketch:

- A minimal service worker whose ONLY job is `push`/`notificationclick` (no caching, sidestepping
  the stale-bundle concern above).
- A `/push/subscribe` route storing subscriptions under `$STATE`, keyed per device; kernel-side
  VAPID keys minted once at install.
- `_feed_notifications` gains a second sink next to `_system_notify`: same armed-card events,
  same silent first-build baseline, pushed to every stored subscription; dead subscriptions
  (`410 Gone`) pruned on send.
- Scope note: this is the first outbound network delivery the kernel makes (push endpoints are
  Apple/Google servers). Payloads should carry the card TITLE and nothing more, and the feature
  stays opt-in per device by construction (a push subscription only exists if that device
  subscribed).

## Proposal 3 — small standalone-mode polish (cheap, with 1)

- App-icon badge (`navigator.setAppBadge`) with the needs-you count — the glanceable "does
  anything need me" signal, no notification required.
- `display-mode: standalone` media query to hide the "open in app"-style hints that make no sense
  once installed, if any accrue.

## Open questions

- Icon: is a plain raster of the swirl glyph on the shell background good enough, or does the
  user want distinct artwork for the home screen?
- Should the login page detect standalone mode and say "paste the token once; this container
  keeps its own cookie" so the first launch reads as expected rather than as being signed out?
- Web Push through a tailnet-only kernel still works (the PHONE's subscription rides Apple's push
  service, not the tailnet), but the kernel then needs outbound HTTPS — fine on the current box;
  worth stating as a requirement.
