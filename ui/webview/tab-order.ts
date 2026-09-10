// The chat tab strip's order is the kernel's order, VERBATIM. The kernel (bin/romp-kernel `_ordered`) is the
// single source of truth and is a pure positional list — no activity / mtime / idle / status input, so it
// never reshuffles on its own. The client must not re-derive it: a tab moves ONLY when the user drags it
// (rewrites the list), a new session arrives (appends at the end), or a tab closes (drops out). NOTHING else.
//
// This is the whole ordering model, extracted so it's unit-testable. The old client kept a PARALLEL order
// (an `effIdx` + `firstSeen` tiebreaker re-sort run on every status push) that diverged from the kernel and
// made tabs jump around on ordinary activity — the bug the kernel's own tests could never catch, because
// they tested the (stable) kernel, not this client layer (the user 2026-06-27, who just wanted it stable —
// additions by subtraction).

/**
 * The render order after a kernel `tabOrder` push: adopt the kernel's order verbatim, but keep any tab the
 * client already knows that the push doesn't carry yet (a `session` push that beat its `tabOrder` push, or
 * the optimistic create placeholder) — appended at the end — so a just-arrived tab never vanishes; the next
 * push reconciles it into place. Deduped; non-string entries dropped.
 *
 * The keep applies ONLY to ids the kernel has never listed. A tab the kernel HAS carried in a tabOrder push
 * is kernel-owned, and a later push omitting it IS the removal event — it must drop out here, never ride the
 * keep. Before this, the keep was unconditional and the one-shot `closed` frame was the only remover, so a
 * client whose socket was down at the kill (a frozen webview force-dropped at the send-queue cap, a sleep, a
 * network blip) missed that single frame and the dead session's tab survived every later push — frozen on
 * its last live status, fully clickable, indistinguishable from a running session (the 2026-08-11 ghost: a
 * session the kernel had ended stayed on the strip looking alive). JS state survives reconnects by design,
 * so only this per-push reconcile can heal it.
 *
 * @param kernelOrder the authoritative SID order from the kernel's tabOrder push
 * @param local       the client's current order (preserves transient, not-yet-pushed tabs)
 * @param known       whether the client actually has a tab for this id (a session arrived / is a placeholder)
 * @param kernelSeen  whether ANY kernel tabOrder push has ever carried this id (kernel-owned → no keep)
 */
/**
 * A live session the kernel affirms (frame `live`) but that this tabOrder push's `order` OMITS is a
 * TRANSIENT read failure, never a close (T258, the user 2026-09-08: a live session whose transcript file was
 * briefly unreadable dropped off the kernel's order and the pane tore its tab down through the T236 omission
 * path). The kernel-side fix keeps such a session in `order`, so this is defense in depth: return the kernel
 * order with every live-but-omitted local id spliced back in at the slot it holds locally (after its nearest
 * surviving predecessor), so the tab neither tears down nor jumps while the next frame re-lists it in place.
 * `live` empty (an older kernel that sends no marker) → the kernel order verbatim, exactly as before.
 */
export function retainLiveOmitted(
  kernelOrder: readonly string[],
  local: readonly string[],
  live: ReadonlySet<string>,
): string[] {
  if (!live.size) return kernelOrder.filter((id): id is string => typeof id === "string");
  const inKernel = new Set(kernelOrder.filter((id): id is string => typeof id === "string"));
  const out = kernelOrder.filter((id): id is string => typeof id === "string");
  for (let i = 0; i < local.length; i++) {
    const id = local[i];
    if (typeof id !== "string" || inKernel.has(id) || !live.has(id) || out.includes(id)) continue;
    let at = 0;                                   // insert after the nearest preceding local id that survived
    for (let j = i - 1; j >= 0; j--) { const k = out.indexOf(local[j]); if (k >= 0) { at = k + 1; break; } }
    out.splice(at, 0, id);
  }
  return out;
}

export function reconcileTabOrder(
  kernelOrder: readonly string[],
  local: readonly string[],
  known: (id: string) => boolean,
  kernelSeen: (id: string) => boolean = () => false,
): string[] {
  const kernel = kernelOrder.filter((id): id is string => typeof id === "string");
  const inKernel = new Set(kernel);
  const extras = local.filter((id) => typeof id === "string" && !inKernel.has(id) && known(id) && !kernelSeen(id));
  const out: string[] = [];
  const seen = new Set<string>();
  for (const id of [...kernel, ...extras]) {
    if (!seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

/** A session frame's id joins the order ONCE. The kernel sends a new session's tabOrder frame before its session
 *  frame (tabs-first), so the id is usually in the order already when the session frame arrives; pushing it again put
 *  a duplicate there that renderTabs hid but a drag's commit wrote into the browser's arrangement (review find,
 *  2026-09-10). `existed` is the session map's word; the order's own is asked too. Returns whether it was added. */
export function adoptArrival(order: string[], id: string, existed: boolean): boolean {
  if (existed || order.includes(id)) return false;
  order.push(id);
  return true;
}
