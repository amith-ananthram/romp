// The chat pane's PLACEHOLDER for a session with no events (T355, the user 2026-09-11): what the pane shows in place of
// turns, decided from the session's state, and rebuilt when that DECISION changes. The pane once rebuilt the placeholder
// only when its single child was not a placeholder at all, so the loader painted at a viewer's open outlived every
// later state: the kernel's error sentence, the stall past the wait, and "written nothing yet" never replaced it, and a
// workflow agent's transcript read "opening…" for good whatever the kernel answered. The kind rides the element as a
// data attribute; the same kind twice is left alone (no churn on repeated pushes that stay empty).
export type PlaceholderKind = "revive-failed" | "sub-error" | "sub-stall" | "sub-loading" | "sub-empty" | "start-failed" | "starting" | "empty";

export interface PlaceholderState {
  sub?: { error: string | null; loaded: boolean; stalled?: boolean } | null;
  failedRevive?: string | null;      // the couldn't-revive notice for this tab, when one stands
  provisional?: boolean;             // a tab whose session is still being created
  provisionalFailed?: boolean;       // …whose create failed
}

export function placeholderKind(st: PlaceholderState): PlaceholderKind {
  if (st.failedRevive) return "revive-failed";
  if (st.sub) {
    if (st.sub.error) return "sub-error";
    if (!st.sub.loaded && st.sub.stalled) return "sub-stall";
    if (!st.sub.loaded) return "sub-loading";
    return "sub-empty";
  }
  if (st.provisional && st.provisionalFailed) return "start-failed";
  if (st.provisional) return "starting";
  return "empty";
}

/** Whether the pane's single child already is the placeholder of `kind` (then nothing is rebuilt). */
export function placeholderStands(only: { classList?: { contains(c: string): boolean }; dataset?: Record<string, string | undefined> } | null, kind: PlaceholderKind): boolean {
  return !!only && !!only.classList?.contains("tx-empty") && only.dataset?.ph === kind;
}

export interface PlaceholderCtx {
  el: (tag: string, cls: string) => any;              // the pane's element maker (render.ts el)
  loader: (text: string) => any;                      // the romp loader's inner element (rompLoaderInner)
  button: () => any;                                  // document.createElement("button")
  br: () => any;                                      // document.createElement("br")
  swirl?: () => any;                                  // the starting tab's swirl image, when the pane has one
  text: { error?: string | null; failedRevive?: string | null; stall: string; sessionName: string };
  onRetry: () => void;
}

/** Fill a fresh placeholder element for `kind`; returns it, stamped with the kind. */
export function fillPlaceholder(ph: any, kind: PlaceholderKind, ctx: PlaceholderCtx): any {
  ph.dataset.ph = kind;
  switch (kind) {
    case "revive-failed":
      ph.textContent = ctx.text.failedRevive || "";
      ph.classList.add("tx-revive-failed");
      break;
    case "sub-error":
      // the kernel could not open the agent's file: its sentence, loud, in the pane (never a blank)
      ph.textContent = ctx.text.error || "";
      ph.classList.add("tx-revive-failed");
      break;
    case "sub-stall": {
      // the ask went unanswered past the wait: say so and offer the retry, never a loader for good
      ph.textContent = ctx.text.stall;
      ph.classList.add("tx-revive-failed");
      const retry = ctx.button(); retry.className = "picker-action confirm-btn"; retry.type = "button";
      retry.textContent = "Retry"; retry.onclick = () => ctx.onRetry();
      ph.appendChild(ctx.br()); ph.appendChild(retry);
      break;
    }
    case "sub-loading":
      // the viewer's first frame is in flight → the romp loader holds the pane (the wait-state rule)
      ph.classList.add("tx-starting");
      ph.appendChild(ctx.loader("opening the agent's transcript…"));
      break;
    case "sub-empty":
      ph.textContent = "This agent has written nothing yet.";
      break;
    case "start-failed":
      ph.textContent = "This session couldn't start. What you typed is kept in the box below; ✕ on the tab discards both.";
      break;
    case "starting": {
      // a PROVISIONAL tab is not empty, it is STARTING — so it wears the romp loader (the repo's rule for any wait), not
      // the placeholder that tells you to send something; the composer below it is live either way
      ph.classList.add("tx-starting");
      if (ctx.swirl) ph.appendChild(ctx.swirl());
      const wm = ctx.el("div", "tx-starting-msg");
      wm.textContent = "Starting " + ctx.text.sessionName + "… you can type now; romp sends it when it's up.";
      ph.appendChild(wm);
      break;
    }
    default:
      ph.textContent = "No messages yet.";
  }
  return ph;
}
