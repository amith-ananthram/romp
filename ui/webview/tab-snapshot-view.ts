// A SECTION AT A GLANCE, VIEW SIDE: the rules render.ts applies to the pane that are not about what a row
// SAYS (tab-snapshot.ts owns the model and its words) but about how the pane behaves under the strip's
// events: which row click may open a session, how Escape yields to the layers that own their own Escape,
// and how the rows update in place. PURE, shaped on the minimal "Like" views of render.ts's state (the
// tab-state.ts idiom), so each rule executes in node tests without a DOM (tab-snapshot-view.test.ts);
// render.ts wires them.

/** Whether a row's click may open its session: the session the row showed must still be on the strip.
 *  Click safety keeps the pressed row in the DOM until the release (tabPointerHeld), so a session dismissed
 *  between mousedown and mouseup (the kernel's closed frame, a tabOrder push without it) is still under the
 *  click; opening that id would put up a loader for a session that never arrives, because the strip's meta
 *  lingers until the next tabOrder frame. A row that had a frame needs its session; a placeholder row (no
 *  frame yet, `loading`) needs its meta, or the frame that landed meanwhile; a tab the user closed
 *  (closingTabs) is neither. `row` is the model row the click landed on; none (never expected) defers to
 *  the session. */
export function rowStillOpen(row: { loading: boolean } | undefined, hasSession: boolean, hasMeta: boolean, closing: boolean): boolean {
  if (closing) return false;
  if (hasSession) return true;
  return !!row?.loading && hasMeta;
}

/** What installSnapshotEscape needs of the page: `showing` (the view is up), `typing` (the target keeps its
 *  own Escape: a field, an editable), `layerOpen` (a layer of this page owns Escape right now: a menu, the
 *  picker or a confirm overlay, a panel, a preview, a comment thread) and `leave` (the exit). */
export interface EscapeHooks { showing(): boolean; typing(target: EventTarget | null): boolean; layerOpen(): boolean; leave(): void }
export interface EscapeTarget { addEventListener(type: "keydown", fn: (e: KeyboardEvent) => void, capture?: boolean): void }

/** ESCAPE LEAVES THE VIEW, and yields to every layer that owns its own Escape, in two phases on one window.
 *  ARMED at the window's capture phase, so this page's layers are still on the page to be seen (a menu
 *  closes itself on Escape and is gone by the bubble); an Escape a listener registered before this one
 *  already marked (the tab menu's closer) is not ours. DECIDED at the window's bubble phase, after the
 *  SHELL's Escape chain has run: its panels (the log, usage and network panels) live in the shell document,
 *  out of this page's sight, and its chain sits on this frame's DOCUMENT at capture (kernel.py
 *  _LANDING_ESC_JS), so it runs after the arm and before the decision, and marks and stops the Escape it
 *  consumed, which then never reaches the decision. A one-phase capture handler would swap the pane on an
 *  Escape aimed at the log panel. The arm is the event itself, checked by identity at the bubble, so an
 *  Escape stopped between the phases leaves nothing a later key can inherit. */
export function installSnapshotEscape(win: EscapeTarget, hooks: EscapeHooks): void {
  let armed: KeyboardEvent | null = null;
  win.addEventListener("keydown", (e) => {
    armed = null;
    if (e.key !== "Escape" || !hooks.showing() || e.defaultPrevented) return;
    if (hooks.typing(e.target) || hooks.layerOpen()) return;
    armed = e;
  }, true);
  win.addEventListener("keydown", (e) => {
    if (e !== armed) return;
    armed = null;
    if (e.defaultPrevented || !hooks.showing()) return;
    e.preventDefault();
    hooks.leave();
  }, false);
}

/** The least of a list node the keyed row update needs: the DOM's HTMLElement, or a test's stand-in. */
export interface RowList<N> { readonly children: ArrayLike<N>; insertBefore(node: N, ref: N | null): unknown; removeChild(node: N): unknown }

/** THE ROWS UPDATE IN PLACE, KEYED BY SESSION ID. sameRow (tab-snapshot.ts) folds lastT and lastMsg, so the
 *  model changes on nearly every push while a member works; a wholesale repaint would destroy the button a
 *  keyboard user had Tabbed onto within seconds (focus to body, Enter dead) and dismiss a hover's title.
 *  Focus and the title belong to the NODE, so the node is what a rebuild keeps: a row whose key stands is
 *  patched (`patch`), a row that came is made (`make`) and put in its place, a row that went is removed, a
 *  reordered row moved (insertBefore, which on a real DOM detaches and re-attaches the node: the caller
 *  re-focuses a moved row). A node with no key, or a second node under a key (never expected), is removed,
 *  never reused. Moves are per row, not a longest-common-subsequence: a section holds a handful of rows,
 *  and a reorder is rare. Returns the counts, for the tests. */
export function reconcileRows<R extends { id: string }, N>(
  list: RowList<N>, rows: readonly R[], keyOf: (node: N) => string | null | undefined,
  make: (row: R) => N, patch: (node: N, row: R) => void,
): { kept: number; made: number; moved: number; removed: number } {
  const want = new Set(rows.map((r) => r.id));
  const standing = new Map<string, N>();
  let removed = 0;
  for (const n of Array.from(list.children)) {
    const k = keyOf(n);
    if (k && want.has(k) && !standing.has(k)) standing.set(k, n);
    else { list.removeChild(n); removed++; }
  }
  let kept = 0, made = 0, moved = 0;
  rows.forEach((row, i) => {
    let node = standing.get(row.id);
    if (node) { patch(node, row); kept++; } else { node = make(row); made++; }
    const at = list.children[i] ?? null;
    if (at === node) return;
    list.insertBefore(node, at);
    if (standing.has(row.id)) moved++;
  });
  return { kept, made, moved, removed };
}
