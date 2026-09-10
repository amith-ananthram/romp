// A federated (remote) session surfaces everywhere as "host:name" — federation.js prefixes BOTH the
// display name and the sid with "host:" as messages arrive, and a LOCAL sid is a bare uuid that never
// contains a colon. So the sid is the designed marker: when it carries a host prefix and the name
// starts with the same prefix, the "host:" part is METADATA, not part of the name — and it renders as
// such (quiet gray, never bold, italic, a step smaller) instead of wearing the session's identity
// color at full weight (the user 2026-07-11). One helper + one .host-prefix class for every surface.
/** The host a federation-prefixed id belongs to, or "" (local) for a bare id. Session ids are UUIDs
 *  (no colon), so a colon unambiguously marks the host prefix. Lives HERE — the side-effect-free
 *  helper module — because importing federation.ts BOOTS a FederationManager (its module tail
 *  bootstraps one on load, by design: it is its own script, loaded once per page). preview.ts
 *  importing it for these two helpers bundled a SECOND manager into the feed/chat pages, and the
 *  twin — hearing only the remote sockets, never the shim's local frames — emitted remote-only merged
 *  feeds in alternation with the real manager's complete ones: every LOCAL card blinked out and
 *  right back, remote cards persisting (the user 2026-07-31, screen recording). Import federation.ts
 *  from nothing; federation-single-instance.test.ts enforces it. */
export function hostOf(id: string): string {
  if (typeof id !== "string") return "";
  const i = id.indexOf(":");
  return i > 0 ? id.slice(0, i) : "";
}

/** The bare session id with any `host:` prefix removed. */
export function bareId(id: string): string {
  if (typeof id !== "string") return id;
  const i = id.indexOf(":");
  return i > 0 ? id.slice(i + 1) : id;
}

export function hostPrefix(name: string | null | undefined, sid: string | null | undefined): { host: string; rest: string } | null {
  if (!sid || !name) return null;
  const i = sid.indexOf(":");
  if (i <= 0) return null;
  const pre = sid.slice(0, i + 1);            // "host:" — exactly what federation prepended
  if (!name.startsWith(pre) || name.length <= pre.length) return null;
  return { host: pre, rest: name.slice(pre.length) };
}

/** Child nodes for a session-name element: [<span.host-prefix>host:</span>, "name"] for a remote
 *  session, or just the plain text for a local one. Use with `elm.replaceChildren(...)`.
 *
 *  Passing the sid alone marks the host automatically when its link is down (the user 2026-07-29, who
 *  read a remote's transcripts for a while before noticing nothing was connected). The mark goes on the
 *  "host:" token, not the session name: the LINK is what is gone, not the session — and a struck WHOLE
 *  name already means a dead session, so the two can never be confused. A CSS cue and a title, never a
 *  glyph. The cue itself is the STALE treatment, not strikethrough (2026-07-30): what this says is "the
 *  last state romp got", which is what .rnet-stale already says in the network panel. */
export function hostNameNodes(name: string, sid: string | null | undefined): Node[] {
  const p = hostPrefix(name, sid);
  if (!p) return [document.createTextNode(name)];
  const h = document.createElement("span");
  const off = hostIsDown(sid);
  h.className = off ? "host-prefix off" : "host-prefix";
  h.textContent = p.host;
  if (off) h.title = hostDownNote(sid);
  return [h, document.createTextNode(p.rest)];
}

/** Is this (prefixed) sid's host unreachable right now? Reads the federation manager's published set,
 *  which the KERNEL's own tunnel health fills. False wherever no manager is loaded (a single-kernel
 *  page, the Obsidian panel), so a local session never wears the mark. */
export function hostIsDown(sid: string | null | undefined): boolean {
  const i = typeof sid === "string" ? sid.indexOf(":") : -1;
  if (i <= 0) return false;
  try {
    const fed = (globalThis as any).__rompFed;
    return !!fed && typeof fed.down === "function" && fed.down().indexOf((sid as string).slice(0, i)) >= 0;
  } catch { return false; }
}

/** The host-down notice is LIVE (the romp swirl after "Reconnecting" spins) only while romp is actually
 *  trying to reach the host: the kernel's /tunnels row says `dialing` (an ssh dial spawned and not yet
 *  confirmed, or the supervisor's health request to the host in flight; kernel.py _row_dialing), or this
 *  page's relay socket to the host is in its CONNECTING state (readyState 0, from `new WebSocket` until
 *  the open or close event). Between attempts (the row waiting out its backoff, the socket closed and
 *  waiting on the redial timer) and while open it is still. Pure over the two states federation
 *  publishes, so the spinner is keyed on the dial EVENTS, never on a timer (the user 2026-09-10, who
 *  wanted to see romp actually trying, not a spinner that spins whatever happens). The kernel's row is
 *  the one that matters for a DOWN host: the browser does not dial a host the kernel reports down. */
export function hostDialLive(rowDialing: boolean | null | undefined, readyState: number | null | undefined): boolean {
  return !!rowDialing || readyState === 0;
}

/** Is a dial to this (prefixed) sid's host in flight right now? Reads the federation manager's published
 *  state (`dialing`, hostDialLive over the host's socket); false wherever no manager is loaded, for a
 *  local session, and with a manager too old to publish it. */
export function hostIsDialing(sid: string | null | undefined): boolean {
  const i = typeof sid === "string" ? sid.indexOf(":") : -1;
  if (i <= 0) return false;
  try {
    const fed = (globalThis as any).__rompFed;
    return !!fed && typeof fed.dialing === "function" && !!fed.dialing((sid as string).slice(0, i));
  } catch { return false; }
}

/** The tooltip a marked host wears: what is wrong, when it was last reached, and that romp is still on it. */
export function hostDownNote(sid: string | null | undefined): string {
  const i = typeof sid === "string" ? sid.indexOf(":") : -1;
  if (i <= 0) return "";
  const host = (sid as string).slice(0, i);
  let seen = 0;
  try {
    const fed = (globalThis as any).__rompFed;
    seen = (fed && typeof fed.lastSeen === "function" && fed.lastSeen(host)) || 0;
  } catch { seen = 0; }
  const when = seen ? ", last reached " + new Date(seen * 1000).toLocaleTimeString() : "";
  return host + " is disconnected" + when + ". This is the last state romp got from it, and romp is "
    + "still trying to reconnect.";
}

/** Same rendering when the host rides its OWN field instead of a sid prefix — the feed card's
 *  "↪ from" chip, whose peerSid stays a bare uuid (the sender may live on a third host neither
 *  the viewer nor the card's kernel can address). No host → plain text, identical to a local name. */
export function hostPartsNodes(host: string | null | undefined, name: string): Node[] {
  if (!host) return [document.createTextNode(name)];
  const h = document.createElement("span");
  h.className = "host-prefix";
  h.textContent = host + ":";
  return [h, document.createTextNode(name)];
}
