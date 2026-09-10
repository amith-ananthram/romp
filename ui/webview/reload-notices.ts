// The warning toasts a page is showing when the reload core takes it (kernel.py _RELOAD_CORE_JS, window.__rompReload
// on every kernel-served page). A warnToast is a DOM element that lives 12 s. The core's restart reload waits for the
// chat pane's pending ships (T272: __rompPaneBusy answers 'upload' or 'held-send') and fires on the next task after the
// pane says the hold is over (render.ts endReloadHoldIfIdle, __rompReload.ended()). The last pending ship's retirement
// is that ending event, and three notices are raised in the same task as it, one task before the owed reload fires:
// the kernel's nack of the ship (the file was not saved, so it was not attached; a message held on it was NOT sent),
// raised just after endReloadHoldIfIdle; the dismissal of the last pending chip (the held message was NOT sent) and
// the ack of the last ship landing on another tab (the held message was not sent), each raised just before it. Nobody
// read them, and the fresh page's loss toast reads shipsInFlight, which the retirement had already emptied. A failed
// attachment and an unsent message went unannounced.
//
// So render.ts keeps the texts of the toasts on screen in THIS tab's sessionStorage (like the scroll record: the
// persisted webview state is localStorage on the served page, shared by every dashboard tab of the origin, and one
// tab's nack must not replay in another) on the core's synchronous pre-reload hook alone (persistNoticesForReload,
// from persistForReload, the function window.__rompPersistForReload names) and the fresh page shows them once, after
// the loss toast. pagehide keeps the scroll record alone: a navigation of the user's own replays nothing, the way the
// loss toast fires once and not on every load. A refusal that reports a state rather than an event is left out (the
// staged sends' "Can't send yet": the session's host is unreachable, or its tab is still being created): the fresh
// page shows that state for itself, so a replay would be redundant at best and stale at worst, and render.ts
// ephemeralWarnToast marks it data-ephemeral for the reading to skip. The record is text only, on purpose: a toast
// has no action beyond its dismissal today, and a future toast with one would replay as its words alone. Pure and
// DOM-free so node --test executes both readings (reload-notices.test.ts); the served scenario is
// tests/test_ship_reship.py NackNoticeSurvivesReload.

/** This tab's sessionStorage key for the record (beside the scroll record's romp:reloadScroll). */
export const RELOAD_NOTICES_KEY = "romp:reloadNotices";

/** The toast container as the reading needs it: anything with querySelectorAll (a DOM element in the page). */
export interface NoticeBox { querySelectorAll(selectors: string): ArrayLike<{ textContent: string | null }>; }

/** The store as the record needs it: sessionStorage, or a stand-in in tests. */
export interface NoticeStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** The texts of the warning toasts on screen (#warn-toasts .warn-toast-msg, in DOM order), blanks dropped and the
 *  toasts marked data-ephemeral skipped (a refusal about a state the fresh page shows for itself; see the note above);
 *  none when the container was never created. */
export function liveNotices(box: NoticeBox | null | undefined): string[] {
  if (!box) return [];
  return Array.from(box.querySelectorAll(".warn-toast:not([data-ephemeral]) .warn-toast-msg"),
                    (n) => (n.textContent || "").trim()).filter((t) => !!t);
}

/** Keep `notices` for the page that follows. Nothing to say clears any record left behind (a reload the browser
 *  refused leaves its record in place; the next core reload with nothing on screen clears it rather than replaying it)
 *  rather than writing an empty one. A store that refuses (no storage in this context, quota) is left alone. */
export function keepReloadNotices(store: NoticeStore | null | undefined, notices: string[]): void {
  if (!store) return;
  try {
    if (notices.length) store.setItem(RELOAD_NOTICES_KEY, JSON.stringify(notices));
    else store.removeItem(RELOAD_NOTICES_KEY);
  } catch { /* ignore */ }
}

/** Take the kept notices out of the store, once: the non-empty strings of the record (anything else reads as none),
 *  with the key removed whenever it is present, so a later load says nothing. */
export function takeReloadNotices(store: NoticeStore | null | undefined): string[] {
  if (!store) return [];
  let raw: string | null = null;
  try {
    raw = store.getItem(RELOAD_NOTICES_KEY);
    if (raw != null) store.removeItem(RELOAD_NOTICES_KEY);
  } catch { return []; }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string" && !!x) : [];
  } catch { return []; }
}
