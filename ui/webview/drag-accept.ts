// The tab strip's dragenter acceptance (2026-09-11, found reproducing the user's drop misplacement: some drops
// snapped the tab home instead). Chromium fires `dragenter` INSTEAD of `dragover` on the tick where the element
// under the pointer changes, and reads that tick's drop operation from the dragenter: an uncancelled dragenter
// means "no drop here" until the next tick's dragover accepts again. The live reorder is what changes the element
// under a STILL pointer: its insert slides the dragged tab (or a neighbour's label or ✕) under the cursor, so the
// tick after every hop is a dragenter tick, and a release right after the hop — the drop's own update is that tick
// — got no drop at all: dragend fired as a cancel, the strip re-rendered from the untouched order, and the tab
// went back to where the drag began. The dragover handler (render.ts) accepts every tick it sees ("the whole strip
// is a valid drop target"); this gives dragenter the same answer while a tab or group drag is in flight, and none
// otherwise (a file dragged over the strip is not ours to accept). Its own module so the rule executes in node
// (drag-accept.test.ts, on node's own EventTarget); render.ts installs it on #tabs once, beside the dragover.
export function acceptDragEnter(strip: EventTarget, dragActive: () => boolean): void {
  strip.addEventListener("dragenter", (e) => { if (dragActive()) e.preventDefault(); });
}
