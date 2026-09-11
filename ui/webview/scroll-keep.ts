// Where a full show of the ACTIVE view lands, and how the per-view saved spot is kept (T249, the user
// 2026-09-07: the chat pane snapped their scroll back to an earlier position a few seconds after they
// scrolled, on a session served through the relay from an attached kernel).
//
// landActive ends every show that no anchor scrolled with ONE rule: an unshown or follow-mode view lands at
// the bottom, any other view lands on its saved `scrollTop`. Until now that saved spot was written only by a
// tab switch (the tab being LEFT), the jump-to-bottom button, the tab-strip/ledger resize compensation and
// the nav trail — never by the reader's own scrolling. So the spot named where the tab was when it was
// last left, and every full show of an already-shown tab (a fork/first-build frame, a settings rerender, a
// revive failure, a dismissal's fallback) landed the reader back there: they had scrolled to the bottom,
// a frame arrived, and the view jumped up to a bubble they had read minutes earlier.
//
// Two rules, both pure so node --test executes them:
//  1. the saved spot FOLLOWS the reader — the #content scroll listener records the active shown view's
//     position and follow-mode on every scroll (followReader), so landActive's rule lands where they are —
//     except while a DEFERRED build is pending (review find, 2026-09-08): showActive reveals the entering
//     view a frame before its heavy build, the browser clamps scrollTop to that stale or empty DOM, and the
//     clamp's scroll event arrives before the frame callback that lands the view; recorded, it sent a
//     compact-mode tab switch and a settings rerender to the bottom instead of the saved spot. The pending
//     build is the event that marks the transient, so `building` suppresses the record until it lands;
//  2. a show of a view that is ALREADY on screen keeps the reader's place across its rebuild the way the
//     live append does (keepPlaceAcrossShow → captureScrollAnchor before, restoreScrollAnchor after): a
//     rebuild can move content above the viewport, and only an anchor keeps the line being read still.
//     A tab SWITCH is not that: the entering view is not displayed yet, so its explicit spot semantics
//     (the leaving-tab save, the nav trail's remembered spot, the jump button) are untouched. A caller that
//     must EMPTY the view before showing it (rerenderAll, on a settings change) captures the anchor first
//     and hands it to showActive, which by then has no DOM to capture from.
// Event-based throughout: the scroll event and the show itself; no timer, no age threshold.

export interface KeepView { scrollTop: number; stick: boolean; shown: boolean; }

/** The reader scrolled the active view: its saved spot and follow-mode follow them. A view not yet shown
 *  keeps its defaults (its first land goes to the bottom regardless), a hidden pane never scrolls, and while
 *  a deferred build is pending (`building`) the position is the reveal's clamp, not the reader's — the
 *  build's own land fires the next scroll event, and that one is recorded. */
export function followReader(v: KeepView | null | undefined, scrollTop: number, near: boolean, building = false): void {
  if (!v || !v.shown || building) return;
  v.scrollTop = scrollTop;
  v.stick = near;
}

/** landActive's landing when no anchor scrolled: "bottom" for an unshown or follow-mode view, else the saved spot. */
export function landSpot(v: KeepView): number | "bottom" {
  return (!v.shown || v.stick) ? "bottom" : v.scrollTop;
}

/** Follow mode at a RE-SHOW of the view already on screen (T262, the user 2026-09-08/09: the chat snapped up
 *  the moment they scrolled to the bottom, in every column, 300 to 1900 px at a time). The journal named the
 *  writer every time: `land-saved`, with the reader at the true bottom (scrollTop + clientHeight = scrollHeight)
 *  and the RECORDED follow flag off. The recorded flag can lag the reader: a scroll that lands while a deferred
 *  build is pending is deliberately not recorded (the reveal's clamp, see followReader), so a wheel gesture that
 *  ended on the bottom in that window left `stick` false and `scrollTop` a screen or more above — and the next
 *  full show landed on that stale spot. The DOM is the truth at the re-show: a reader at the true bottom follows
 *  it, whatever the flag last said. Only ever turns following ON; off the bottom the recorded flag stands, so a
 *  reader who scrolled up keeps their place through the rebuild as before. */
export function reshowStick(recorded: boolean, atBottomNow: boolean): boolean {
  return recorded || atBottomNow;
}

/** Must this show keep the reader's place across the rebuild? Only when the view is the one on screen
 *  already (displayed, in a visible pane), has been shown before, and nothing is navigating (no pending
 *  anchor or moment: a deep link lands where it says). A tab switch fails `displayed`; a first show fails
 *  `shown`; a hidden pane has nothing to keep. */
export function keepPlaceAcrossShow(v: KeepView, displayed: boolean, visible: boolean, navigating: boolean): boolean {
  return v.shown && displayed && visible && !navigating;
}

/** The follow-mode tolerance (T262c, the user 2026-09-08): a reader within this many pixels of the bottom IS at
 *  the bottom — fractional scroll positions on a scaled display leave up to a pixel of slack at the true end —
 *  and one pixel further they have LEFT it: follow mode is off and the go-to-bottom chip shows. render.ts's
 *  atBottom reads this for appendActive's stick, followReader's record, the leaving-tab save, the rebuild's
 *  at-bottom capture, the resize compensations and the chip. The 80 px band that used to drive all of those
 *  survives only for the user's own send revealing itself (render.ts nearBottomForSend). */
export const AT_BOTTOM_PX = 2;
export function atBottomDist(dist: number): boolean {
  return dist <= AT_BOTTOM_PX;
}

/** A box BELOW the transcript changed height (T262e, the user 2026-09-08: the awaiting/background-task box appeared
 *  over the last lines and the pane fell into scrolled-up mode by itself; the composer auto-growing does the same).
 *  A box below the scroller changes only the scroller's clientHeight: the browser keeps scrollTop and fires no
 *  scroll event, so a reader at the bottom is silently the box's height above it, and the next append reads
 *  atBottom false and leaves them there. The rule is the OPPOSITE of the boxes-above compensation: the reader's
 *  RECORDED follow mode (`stick`, still the pre-growth truth because nothing scrolled) decides — on, and any
 *  height change, they are written to the new bottom (a shrink is a no-op write: the clamp is already there);
 *  off, nothing moves — their top line never did. Pure, so node executes it. */
export function followBoxBelow(stick: boolean, dh: number): boolean {
  return stick && dh !== 0;
}

/** Follow-the-tail after an append, for a reader whose view is in follow mode (T262, the user 2026-09-08: "jumped
 *  up slightly on my scroll" in busy sessions). A tab within the old 80 px band used to be pinned to the bottom on
 *  EVERY frame the pane received — including a status-only tail that changed no content — so a reader wheeling
 *  up from the tail of a working session was snapped back within the first 80 px, again and again (the harness
 *  reproduced it: a 60 px stop moved 60 px to the bottom in a quiet window with the content height unchanged).
 *  The bottom is followed only when there is something new to follow: the content's height changed, or the
 *  reader was already at the very bottom (where the pin is a no-op). T262c then found the height-changed branch
 *  was the remaining snapback — a reader 30 px up in a streaming session was re-pinned by every append — and
 *  moved follow mode itself onto the true bottom (atBottomDist), so a stick now implies `distBefore` within the
 *  tolerance and this answers true for it; the height branch stays as the executable statement of the rule
 *  should a wider stick ever return. Pure, so node executes it. `distBefore` = scrollHeight − scrollTop −
 *  clientHeight before the rebuild. */
export function followTail(distBefore: number, heightBefore: number, heightAfter: number): boolean {
  if (atBottomDist(distBefore)) return true;
  return heightAfter !== heightBefore;
}

/** The transcript's bottom moved UP under a follow-mode reader (T262f, the user 2026-09-08: the pane unreadable near
 *  the bottom; their laptop's breadcrumbs showed the view moving up by one fixed amount with no pane write between
 *  the rows). An element at the END of #content losing height — the live-ask card cleared, a queued group emptying,
 *  the offline foot going — makes the browser clamp scrollTop to the new maximum: an unwritten move the follow-mode
 *  latch never saw. The rule mirrors followBoxBelow: the view's RECORDED follow mode (`stick`, still the pre-change
 *  truth) decides, and only a SHRINK qualifies — growth at the tail is the append path's (append-stick) or the
 *  live-ask reveal's. On: the reader is written to the new bottom (where the clamp left them, so nothing moves
 *  twice, but the move is the pane's own, attributed in the journal, and the latch re-reads from a real scroll
 *  event). Off: nothing — the clamp cannot reach a reader more than the shrink above the bottom. Pure. */
export function followTailShrink(stick: boolean, dh: number): boolean {
  return stick && dh < 0;
}
