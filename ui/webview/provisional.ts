// A new session shows its chat box IMMEDIATELY, and starts behind it (the user 2026-07-30).
//
// Creating a session used to raise a modal "Opening session…" over the whole pane, and you waited: the
// kernel resolves the directory, connects the session, and the first transcript poll comes
// back — seconds, sometimes many. Nothing could be typed in that gap, and the only thing on screen was a
// dialog with three dots. So the tab appears at once in the OPENING state with a live composer; anything
// typed is held and flushed the moment the real session lands, and a create that FAILS says so in a
// dialog instead of the cue quietly timing out after thirty seconds.
//
// The provisional id must NOT contain a colon. Federation routes on `id`/`sid` and reads the part before
// a colon as a HOST, so a "pending:" form would send every op on this tab to a machine named "pending" —
// the same trap that swallowed follow-ups on remote cards. A dash keeps it unroutable and obviously not
// a uuid.

export const PROVISIONAL_PREFIX = "new-";

/** Mint an id for a not-yet-created session. `seed` makes it unique; any colon in it is stripped. */
export function mintProvisionalId(seed: string): string {
  return PROVISIONAL_PREFIX + String(seed).replace(/[^a-zA-Z0-9]/g, "").slice(0, 24);
}

export function isProvisionalId(id: string | null | undefined): boolean {
  return typeof id === "string" && id.startsWith(PROVISIONAL_PREFIX);
}

/**
 * The name a created session will ARRIVE under, which is what the provisional tab is matched against: a
 * remote session's tab is host-prefixed, a local one is bare.
 */
export function provisionalName(host: string, name: string): string {
  return host ? host + ":" + name : name;
}

/**
 * Does this freshly-arrived session adopt the provisional tab? Only a session we have never seen, whose
 * name is exactly the one we asked for. Matching on the NAME is the only join available — the id is
 * minted by the kernel and cannot be known in advance — so a session arriving under a different name is
 * somebody else's, and adopting it would hand the queued text to the wrong conversation.
 */
export function adoptsProvisional(
  existed: boolean, arrivedName: string, pendingName: string | null,
): boolean {
  return !existed && !!pendingName && arrivedName === pendingName;
}

/**
 * Did a kernel `focus` RESOLVE the create in flight to a session that already runs? A create naming a
 * running session is answered by focusing that session, never by minting one (and, when the request
 * carried tags, by a warn that they were not applied — sent AFTER the focus). No session frame will
 * ever adopt the tab then: adoptsProvisional wants a session this client has never seen, and the
 * running one has been here all along. So the focus is the verdict — when it lands on a session we
 * hold under exactly the name we asked for while a create is pending, the tab is done. The
 * provisional id itself is excluded for form's sake (the kernel never learns it, so it cannot focus it).
 */
export function focusResolvesProvisional(
  focusedId: string, focusedName: string | undefined, pendingName: string | null, provisionalId: string | null,
): boolean {
  return !!provisionalId && focusedId !== provisionalId && !!pendingName && focusedName === pendingName;
}
