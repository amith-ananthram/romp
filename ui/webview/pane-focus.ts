// Where the chat pane's focus goes when a tab leaves the strip, and what the pane says while no tab is active
// (T357, the user 2026-09-11). Their report: a kernel restart while they were on a REMOTE session's tab dropped the
// remote tabs until their host reconnected, the pane jumped to a local session, and when the host came back it jumped
// again to the remote one; they ended up unsure which box they were typing to. Their rule: the pane never jumps to
// ANOTHER session on its own. The one departure that keeps the recency fallback is the user's own ✕ (they closed the
// tab: the box changing hands is the one case they already know, and the MRU-then-first-tab rule of T236 applies).
// Every other departure (a restart, a host relay down, a session gone) leaves the pane UNFOCUSED: no active tab, a
// blank body that names the session that vanished, the composer disabled with no session name, no session colour
// on the focus ring; and when THAT session's tab returns (the relay redial, the host re-attach) focus goes back to
// it, since the user chose it. Only a pick by the user moves focus to a different session.

/** Why a tab left the strip: the pane's own DismissWhy (render.ts), spelled here so this module stays import-free. */
export type Why = "close" | "end" | "hostDrop" | "omitted";

export type FocusAfterDismiss = { activeId: string | null; unfocused: boolean };

/** The active tab after `why` took the active one. `mru` is the recency stack with the dismissed id already gone,
 *  `order` the strip, `goingToo` the ids the same teardown takes next (never a fallback). */
export function focusAfterDismiss(why: Why, mru: readonly string[], order: readonly string[],
                                  goingToo: (id: string) => boolean): FocusAfterDismiss {
  if (why !== "close") return { activeId: null, unfocused: true };
  return { activeId: mru.find((x) => order.includes(x) && !goingToo(x)) || order.find((x) => !goingToo(x)) || null,
           unfocused: false };
}

/** The session that vanished from under the user, for the empty body's line: how it left (a dismissal's reason), or
 *  "awaited" (the tab this page showed before a reload, not listed yet: a kernel restart reloads the page, so no
 *  dismissal ran and the persisted choice is all the pane has), "gone" (a persisted id that can never be listed
 *  again as a tab of its own: a subagent viewer's tab, a provisional create), or "hidden" (the strip's `#only=` filter
 *  no longer shows the active tab; the peek covers a view's exclusion, not the filter's). */
export type Vanished = { name: string; why: Exclude<Why, "close"> | "awaited" | "gone" | "hidden"; dialing: boolean;
                         declined?: boolean };   // "hidden" by a DECLINED adoption (no tab was ever the user's): the head says so, name-free

/** The empty body's text in three pieces, so the pane can dress the name the way the strip does (host prefix,
 *  identity colour): `head` + `name` + `tail`, `name` null when no session vanished. */
export type EmptyStateParts = { head: string; name: string | null; tail: string };

export function emptyStateParts(v: Vanished | null, hasTabs: boolean): EmptyStateParts {
  if (!v) {
    return hasTabs ? { head: "No session selected. Pick a tab to start.", name: null, tail: "" }
                   : { head: "No session open — click + to add one.", name: null, tail: "" };
  }
  const tail = v.why === "hostDrop"
    ? (v.dialing ? "’s host disconnected; reconnecting… It comes back here when the host does."
                 : "’s host disconnected. It comes back here when the host does.")
    : v.why === "omitted" ? " is no longer listed by romp. It comes back here if it returns."
    : v.why === "awaited" ? (v.dialing ? " is not listed yet; its host is reconnecting… It comes back here when the host does."
                                       : " is not listed yet. It comes back here when its host does.")
    : v.why === "gone" ? " is no longer on the strip. Pick a tab."
    : " ended.";
  // the #only= filter hid the tab: a NAME-FREE line (the frame's purpose is a clean recording, and the pick
  // instruction is said once): the manager's call, 2026-09-11
  if (v.why === "hidden") {
    // a declined record (the first session to arrive was hidden; nothing was the user's yet) says so, since a visible
    // placeholder tab may well be on the strip (the review's low: "shows no session" read wrong beside a tab)
    if (v.declined) return { head: "The first session to arrive is hidden by this view. Pick a tab, or change the view.", name: null, tail: "" };
    return { head: "This tab view shows no session. Change the view, or pick a tab.", name: null, tail: "" };
  }
  // an EMPTY strip invites no pick (the review's low): the session named is all there is to say
  return { head: hasTabs ? "No session selected. Pick a tab to start. " : "No sessions yet. ", name: v.name, tail };
}
