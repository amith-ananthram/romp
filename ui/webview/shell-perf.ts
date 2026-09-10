// The dashboard shell's performance collector. The shell is the top-level window that frames the panes: it
// receives no frames and loads only the small bundles the landing page names (age-color-global.ts,
// palette-main.ts), so nothing measured it. Chromium reports a long animation frame to the top-level document
// and never to the iframe whose script ran it, so when a pane script blocked the main thread for seconds, every
// pane's minute row arrived late and none carried a long frame; the shell, had it been listening, would have seen
// the frame with the pane script the browser named (`chat.js:paintAll@9000`). This bundle installs the collector
// every pane runs (perf-telemetry.ts) under app "shell", with no frame brackets at all: its minute row is the
// long frames it observed, heap and DOM, posted on the shell's own kernel socket (window.__rompShellSend, defined
// by the kernel's mobile-shell script) on the clientDiag channel the panes use, so `romp perf client` shows it as
// one more pane of its dashboard. Keys are scrubbed as on every pane (perf-telemetry.ts scriptKey,
// sanitizeInvoker): a script URL to its basename with no query, an inline shell script as `page:` (the shell
// page's URL carries the token; the collector strips query and fragment before comparing), an invoker with no
// element id or URL. A browser that reports neither long animation frames nor long tasks gives the collector
// nothing to observe, and an idle minute posts nothing, so the shell is silent there.
//
// Transport: window.__rompShellSend does not exist until the mobile-shell script runs, after this bundle, and
// answers false while the shell socket is not open (it redials on a close). A row it refuses is HELD, at most
// HELD_MAX of them with the oldest dropped first (the newest minute is the one worth having; the pane shim's cap
// on queued breadcrumbs is the same twenty), and the held rows go ahead of the next row that finds the socket
// open, in order. No timer of its own: the collector's minute flush is the retry, so a held row waits for the
// next minute with something to report, and a row still held when the page closes goes with it; one late row
// beats a lost one.
//
// Loaded by the landing page (kernel.py _landing) as its own dist entry (vscode-extension/esbuild.js). Nothing
// here throws into the page; a window without performance.now gets nothing (installPerfTelemetry returns null).
import { installPerfTelemetry, type PerfPost, type RompPerf } from "./perf-telemetry";

export const SHELL_APP = "shell";
export const HELD_MAX = 20;

type ShellSend = (m: Record<string, unknown>) => boolean;

/** The shell's clientDiag transport. `send` is looked up at every call (the socket script runs after this
 *  bundle); a row the send refuses, or that arrives before the send exists, is held; held rows drain in order
 *  ahead of the row in hand, and a refusal mid-drain stops the drain with the order kept. */
export function shellPost(send: () => ShellSend | undefined): PerfPost {
  const held: Record<string, unknown>[] = [];
  const hold = (m: Record<string, unknown>): void => {
    if (held.length >= HELD_MAX) held.shift();
    held.push(m);
  };
  const deliver = (s: ShellSend, m: Record<string, unknown>): boolean => {
    try { return !!s(m); } catch (e) { return false; }   // a throwing send is a closed socket, not a lost row
  };
  return (m) => {
    const s = send();
    if (typeof s !== "function") { hold(m); return; }
    while (held.length) {
      if (!deliver(s, held[0])) { hold(m); return; }
      held.shift();
    }
    if (!deliver(s, m)) hold(m);
  };
}

/** Install the shell's collector on this window; null where nothing can be measured. */
export function installShellPerf(): RompPerf | null {
  if (typeof window === "undefined") return null;
  const w: any = window;
  return installPerfTelemetry(SHELL_APP, { post: shellPost(() => w.__rompShellSend) });
}

installShellPerf();
