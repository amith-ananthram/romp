// The NAME rules of the dialogs that name a new session-side thing on a session's behalf — the comment, the
// break-out and the fork (T289, the user 2026-09-09: a comment on a remote session was refused as a
// create-by-name). Side-effect-free, like host-prefix.ts, so both the dashboard bundle and the node tests
// can load it.
//
// A remote session surfaces on this viewer as "host:name": federation prefixes the display name the way
// it prefixes the sid, and the "host:" part is THIS viewer's metadata, never part of the name the owning
// kernel knows (host-prefix.ts, the rename dialog's rule since 2026-08-02). The dialog's prefill used the
// decorated string sanitized to "host-name-comment-N", and sent it as the thread's name — so the owning
// kernel stored a name wearing the viewer's host label, and the next dialog, prefilled with the same
// count (the viewer's count regresses when a thread is deleted, and lags when a create's ack is lost),
// collided with it and was refused. Two rules fix both halves:
//   - the prefill is built from the BARE name (defaultCommentName / defaultBreakoutName);
//   - an UNTOUCHED prefill is sent as "" (nameToSend): the kernel's default is the kernel's to pick — it
//     counts its own threads and skips every taken name, which a viewer-side count cannot do.
import { hostPrefix } from "./host-prefix";

/** The session name the OWNING kernel knows: the display name with this viewer's "host:" label removed.
 *  The sid is the marker (a bare uuid never carries a colon), so a local session's name is untouched even
 *  when it happens to contain a colon-like shape. */
export function bareSessionName(name: string | null | undefined, sid: string | null | undefined): string {
  const nm = name || "";
  const p = hostPrefix(nm, sid);
  return p ? p.rest : nm;
}

function stem(name: string | null | undefined, sid: string | null | undefined): string {
  return (bareSessionName(name, sid) || "session").replace(/[^A-Za-z0-9._-]/g, "-");
}

/** The comment dialog's prefill: <bare>-comment-<known+1>. A HINT of what the kernel will pick, shown so
 *  the user can edit it; sent only when they did (nameToSend). */
export function defaultCommentName(name: string | null | undefined, sid: string | null | undefined, knownThreads: number): string {
  return stem(name, sid) + "-comment-" + (Math.max(0, knownThreads | 0) + 1);
}

/** The break-out dialog's prefill: <bare>-thread. */
export function defaultBreakoutName(name: string | null | undefined, sid: string | null | undefined): string {
  return stem(name, sid) + "-thread";
}

/** The fork dialog's prefill: <bare>-fork. The fork's name is required, so this one is always sent — which
 *  is exactly why a labelled stem there always reached the owning kernel (review, 2026-09-09). */
export function defaultForkName(name: string | null | undefined, sid: string | null | undefined): string {
  return stem(name, sid) + "-fork";
}

/** What the dialog sends as the name: the user's typed name, or "" when the box still holds its prefill
 *  (or nothing at all) — an empty name asks the owning kernel for its own default. */
export function nameToSend(value: string | null | undefined, prefill: string): string {
  const v = (value || "").trim();
  return v === prefill.trim() ? "" : v;
}
