// The Files pane's recent-files list (files.ts): the pure half, importable by the tests. When nothing is
// open the pane lists the files most recently open there as re-open links, so a thread dropped yesterday
// costs one click to pick up. Stored per browser (localStorage), most recent first, one row per path and
// session, capped; the identity a row carries is whatever the shell's relay handed over when the file was
// opened, so the row's session chip re-renders without a session list of the pane's own.
export interface RecentIdentity { name: string; color: { bg: string; fg: string } | null }
export interface RecentFile { path: string; sid: string | null; identity: RecentIdentity | null; t: number }
export const RECENT_KEY = "romp:files-recent";
export const RECENT_MAX = 8;

/** A relayed or stored identity, validated to the chip's shape. Anything else is no identity: the chip is
 *  looked up, never invented (file-view.ts's rule), and a resolver miss falls to the kernel's stub there. */
export function asIdentity(x: unknown): RecentIdentity | null {
  if (!x || typeof x !== "object") return null;
  const o = x as { name?: unknown; color?: unknown };
  if (typeof o.name !== "string" || !o.name) return null;
  const c = o.color as { bg?: unknown; fg?: unknown } | null | undefined;
  const color = c && typeof c === "object" && typeof c.bg === "string" && typeof c.fg === "string" ? { bg: c.bg, fg: c.fg } : null;
  return { name: o.name, color };
}

/** The stored list, tolerant of junk: a corrupt entry costs the list, never the pane. */
export function parseRecent(raw: string | null): RecentFile[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    if (!Array.isArray(v)) return [];
    const out: RecentFile[] = [];
    for (const e of v) {
      if (!e || typeof e !== "object" || typeof e.path !== "string" || !e.path) continue;
      out.push({ path: e.path, sid: typeof e.sid === "string" && e.sid ? e.sid : null,
                 identity: asIdentity(e.identity), t: typeof e.t === "number" ? e.t : 0 });
    }
    return out.slice(0, RECENT_MAX);
  } catch { return []; }
}

/** Most recent first, one row per path and session (a re-open moves the row up and refreshes its identity), capped. */
export function rememberRecent(list: RecentFile[], entry: RecentFile, max = RECENT_MAX): RecentFile[] {
  const rest = list.filter((r) => !(r.path === entry.path && r.sid === entry.sid));
  return [entry, ...rest].slice(0, max);
}
