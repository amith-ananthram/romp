# Federated push: one subscription buzzes for every connected kernel

Status: IMPLEMENTED, landing in the commit that adds this file.

The ask (the user 2026-08-08, minutes after installing the home-screen app): with kernels
linked, having to think about WHICH kernel a notification comes from is exactly the mechanics
romp exists to hide — a device subscribed at one kernel should get the bell events of every
kernel connected to it. Concretely: the phone is installed against the always-on box, the
laptop's sessions do the day's work, and the laptop's "needs you" must still buzz the pocket.

**The invariant: push scope = dashboard scope.** The dashboard you subscribed from shows every
attached host's sessions merged together; the bell you tapped there means "notify me about all
of this", not "about the slice this kernel happens to own".

## Why the gap existed

The merged multi-host view is assembled in the BROWSER (`federation.js`: one socket per kernel,
ids prefixed `host:`); the serving kernel splices relay connections and reads nothing. So the
kernel that holds a push subscription never sees another host's cards, and its bell detector
(`_feed_notifications`, diffing fresh feed builds) fires for local sessions only.

## Design: forward at the event source, mirror at the subscriber

When a kernel's bell fires (the same armed-card feed-build transition that drives every other
sink), `_push_forward` hands the fired events — `[{title, body, sid}]` — to every attached
TRUSTED peer via `POST /push/relay`; the receiving kernel mirrors them out through its own
`_push_notify` to the devices subscribed to it.

Decisions, and the reasons they went this way:

- **The existing pair channel, no new legs.** Every attached pair can already `_peer_call` each
  other: the tunnel plus the serve token exchanged at attach/check-in. Both sides run the same
  code, so events flow in both directions of an attachment automatically.
- **A relayed event is terminal.** `/push/relay` delivers to local devices and never forwards
  onward, so attachment cycles cannot echo an event; no event ids or seen-sets are needed.
- **Trust is judged by origin at delivery time, receiver-side** — the same by-origin judgment
  the postal bus makes for inbound mail. Only a host the user marked `trusted` may buzz their
  devices; `directed`/`isolated`/unknown drops WITH ITS REASON on stderr (fail loudly), naming
  the network panel's per-host selector as the remedy. The sender also forwards only to trusted
  peers: your events leave your kernel only toward boxes you control.
- **The origin is worn on the sid, the way every federated surface routes** (`host-prefix.ts`):
  the mirrored event's sid gains `origin:` so a tap routes through the merged dashboard's own
  tabs. The title passes through exactly as the origin composed it (`Romp needs you: web`,
  `Romp: web` — `_notify_title`): the session name alone, since 2026-09-09. Until then the relay
  grafted the origin onto the title too (`romp: boxa:web`); the user found the host noise in
  the words, when the tap already carries the routing in `data`.
- **Mirrored events omit `badge`** and the service worker applies `setAppBadge` only to a
  numeric value: the origin kernel's needs-you count is not the receiving kernel's count, and
  repainting 0 would clear a real local badge.
- **`_reveal_msg` trusts the browser for prefixed sids.** A `host:sid` focus is handed to the
  chat pane as-is (its tabs route it); the local session-list liveness check would otherwise
  mint a wrong `confirmRevive` from the wrong kernel's world.

## Verified findings that shaped it

1. `_remotes` rows carry `local_port` + `token` for BOTH attach styles (ssh-attach fetches the
   token; check-in hands it over in `_checkin_payload`) — so the authenticated kernel-to-kernel
   channel needed no new trust surface.
2. The chat pane's `focus` handler routes by tab id and already expects jumps "incl. from a
   remote kernel"; remote tabs exist under their prefixed ids once federation merges them.

## Known follow-ups, deliberately not in this landing

- **Cold-start precision for remote sids.** A tap that boots the app delivers its parked reveal
  on the chat pane's `ready` — possibly before federation has merged the origin host's tabs, in
  which case the focus lands on the dashboard, not the session. Event-based fix when it earns
  its keep: park the prefixed focus browser-side until that host's tabs arrive.
- **A merged badge count.** The icon count is the local kernel's needs-you number; a truly
  federated count is a design question (sum across hosts? per-host chips?) for later.
- **Visibility for tier-dropped relays.** stderr satisfies fail-loudly for an operator; if
  dropped relays turn out to matter in practice, the bus's held-mail quarantine-card treatment
  is the pattern to copy.
