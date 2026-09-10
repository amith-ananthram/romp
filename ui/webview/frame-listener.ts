// A pane's one frame listener, installed on both delivery paths.
//
// Every pane handles the frames it receives in ONE window "message" handler: the kernel's frames (through the
// shim and federation.js on a kernel page, the extension host's pipe in VS Code) and the shell's `romp:` posts.
// federation.js emits its MERGED frames (`feed`, `tabOrder`, `data`, `bars`) by direct call to the handlers
// registered with it and dispatches on window only when none is registered (federation.ts emit): a "message"
// listener in another JavaScript world (a browser extension's content script) that reads event.data forces a
// structured clone of the frame on every window dispatch, tens of milliseconds for a large board. So the pane
// installs the SAME handler on window and in the registry; federation picks one path per frame, so each frame
// arrives exactly once, and the perf brackets (perf-telemetry.ts) nest the same way on either path.
//
// Kept import-free: federation.ts must never be imported by a pane bundle (federation-single-instance.test.ts),
// so the registry is reached through the window slot federation.js publishes before the bundle loads. Without
// the slot (a VS Code webview, an older federation.js) the window listener carries every frame, as before.

/** Install `handler` as the pane's frame listener on window and, when the page's federation manager is present,
 *  in its direct-delivery registry. Returns the handler. */
export function listenForFrames(handler: (e: MessageEvent) => void): (e: MessageEvent) => void {
  window.addEventListener("message", handler);
  // no registry on this page (a VS Code webview, an older federation.js): the window path carries every frame
  const fed = (window as any).__rompFed;
  if (fed && typeof fed.onFrame === "function") fed.onFrame(handler);
  return handler;
}

// ── a kernel-served page whose federation manager never came up (2026-09-10) ──────────────────────────────────
// The pane shim publishes window.__rompLocalSend and window.__rompApp before any bundle loads (kernel.py, the shim),
// and federation.js — loaded ahead of the pane bundle as a classic script — publishes window.__rompFed in start().
// A classic script that fails to fetch or to evaluate stops nothing else: the pane bundle still runs, the shim's
// deliver() falls to its window-dispatch branch, and the pane consumes the kernel's RAW frames — no arrangement
// (view-order.ts) ever applied, so the strip shows the kernel's seed order, and a drag would then write that seed
// over the browser's arrangement for every other pane (the 2026-09-10 incident: two chat columns on different
// orders, one of them reshuffling on every push, pins helpless). Both fallbacks were designed for pages that never
// have a manager (a VS Code webview, the timeline) and so degraded silently, against the fail-loudly rule. This is
// the pure decision the pane bundle makes at boot: shim present, manager absent → the manager is MISSING, not
// merely not part of this page.
export function federationMissing(w: { __rompLocalSend?: unknown; __rompFed?: unknown }): boolean {
  return typeof w.__rompLocalSend === "function" && !w.__rompFed;
}

/** What the browser recorded about fetching federation.js — the one measurement that separates a failed fetch
 *  (status 0 or an empty transfer, a long duration: a network gap, a dist swap mid-read) from a bundle that
 *  arrived and failed to evaluate (a 200 with a normal body, the slot still unset). Picked from
 *  performance.getEntriesByType("resource"); null when the browser recorded nothing for it. */
export function federationLoadEntry(entries: readonly { name: string; duration?: number; transferSize?: number; encodedBodySize?: number; responseStatus?: number }[]):
    { duration: number; transferSize: number; encodedBodySize: number; responseStatus: number } | null {
  const e = entries.find((x) => typeof x.name === "string" && /\/dist\/federation\.js(\?|$)/.test(x.name));
  if (!e) return null;
  return { duration: Math.round(e.duration || 0), transferSize: e.transferSize || 0, encodedBodySize: e.encodedBodySize || 0,
           responseStatus: typeof e.responseStatus === "number" ? e.responseStatus : -1 };
}

/** The retry marker's key for THIS document. sessionStorage is per tab and shared by the shell page and every
 *  same-origin iframe in it, so a bare key would let two chat columns (/chat and /chat?col=2, the split) steal each
 *  other's one retry and file each other's load entries; the path and query name a column, and stay the same across
 *  its own reloads. */
export function fedRetryKey(loc: { pathname: string; search: string }): string {
  return "romp:fed-retry:" + loc.pathname + loc.search;
}
