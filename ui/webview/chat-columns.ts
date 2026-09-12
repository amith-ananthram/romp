// CHAT COLUMNS, the page's half of the partition (the user 2026-09-11, who asked for columns that hold
// different sessions instead of each showing the whole board). The shell owns ONE fact: which sessions each
// later column holds, persisted under `romp-chat-cols` and read by every column page through the parent
// window's `__rompChatSets()` as `{"2": [sid…], "3": [sid…]}`. The first column has no entry: it holds every
// session no entry lists, so a session that arrives with no gesture (a peer's spawn, a remote host's tabs, a
// revive whose column has closed) lands there. Each page filters its strip by that fact through `tabInView`
// (render.ts), so the keyboard walk, the hidden-active re-point, the tag sections and the strip signature
// compose with it for free. Pure: no DOM, no window; render.ts hands it the search string and the sets.
export type ColSets = Record<string, string[]>;

/** The column this page is, from its own `location.search`: the pane shim's rule, byte for byte. The first
 *  column is `""` (`?col=1` folds to it, so its state blob keeps the unsuffixed key every older page had);
 *  every later column is its number as a string; a standalone page or the VS Code webview has no search. */
export function colFromSearch(search: string): string {
  let col = "";
  try { col = new URLSearchParams(search || "").get("col") || ""; } catch { col = ""; }
  return col === "1" ? "" : col;
}

/** The column whose entry lists `id`, else `""` (the first column, which derives). A doubly listed id (a
 *  store another dashboard wrote before this shell reconciled it; the shell's own writes never produce one
 *  and its `sets()` resolves one by row order) belongs to ONE column, the first key holding it, so no two
 *  columns ever both show a tab for it. */
export function ownerOf(sets: ColSets, id: string): string {
  for (const col of Object.keys(sets)) {
    const ids = sets[col];
    if (Array.isArray(ids) && ids.includes(id)) return col;
  }
  return "";
}

/** Whether column `col` holds `id` under `sets`. `null` sets means no partition at all (a standalone page,
 *  the VS Code webview, the phone, a shell without the split script): everything is held. A later column
 *  holds the ids its entry lists; the first column (`""`) holds every id no entry lists. */
export function columnHolds(sets: ColSets | null, col: string, id: string): boolean {
  if (sets === null) return true;
  return ownerOf(sets, id) === col;
}
