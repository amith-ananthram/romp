// A SECTION AT A GLANCE: the pane the transcript normally fills shows one compact row per session in a
// tab-strip section (name, state pip, what it is doing now in the user's terms, when it last did anything,
// and whether it needs the user), so a group can be surveyed at a glance and any of its sessions opened
// from there. It shows when a section header is clicked (which also folds or opens the section) and stays
// until a session is picked; a section holding the tab being read folds like any other, its header
// standing in for the hidden tab (tab-groups.ts planStrip).
//
// PURE: the model is a function from what the chat client already holds per session (the session frame:
// name, color, status, the events tail; and its ledger: the same summary / current task / recent tops the
// tab hover tip renders, plus two fields the kernel puts on the ledger for this view: the postal working
// note, and whether the feed files one of the session's cards under needs-you) to plain rows, with NO
// clock in it: the last-activity time is an epoch the renderer formats, so a push that changed nothing
// yields the SAME object (snapshotModel returns `prev`) and the renderer rebuilds nothing. render.ts
// paints it; the shapes below are the minimal "Like" views of render.ts's types (the tab-state.ts idiom),
// so the rule runs in node tests without a DOM.
import { tabStateClass, type TabStateLike } from "./tab-state";
import { stripInline } from "./docreview";

export interface SnapStatusLike extends TabStateLike { sinceEpoch?: number | null }
export interface SnapEventLike { kind?: string; md?: string; text?: string; ts?: string; t?: number }
export interface SnapColor { bg: string; fg: string }
export interface SnapSessionLike {
  name?: string; color?: SnapColor | null; status?: SnapStatusLike | null;
  events?: ReadonlyArray<SnapEventLike> | null;
}
export interface SnapLedgerLike {
  summary?: string | null; workingNote?: string | null;
  /** the feed's verdict, from the kernel's last feed build: true when one of this session's cards is filed
   *  under needs-you there (the column the feed's Blocked list is), false when none is, null when no feed
   *  has been built since the kernel started (the first push cycle) */
  needsInput?: boolean | null;
  tree?: ReadonlyArray<{ text?: string; current?: boolean }> | null;
  recent?: ReadonlyArray<{ text?: string; t?: number }> | null;
}
export interface SnapSectionLike { name: string | null; color: string; ids: readonly string[] }
/** The strip's meta for a tab whose session frame has not landed (render.ts tabMeta): its name and color alone. */
export interface SnapMetaLike { name?: string; color?: SnapColor | null }

/** The pip a row wears: the tab's own colors by the tab's own rule (tab-state.ts), plus the two states the
 *  strip paints on the chip rather than the tab: `waiting` (idle, but background work it dispatched is
 *  still running) and `unknown` (no session frame yet). An idle or ready session wears none. */
export type SnapPip = "working" | "blocked" | "awaiting" | "retrying" | "compacting" | "waiting" | "unknown" | "";

export interface SnapRow {
  id: string;
  name: string;
  color: SnapColor | null;
  pip: SnapPip;
  /** the state in words: the row's spoken label and its title; "" for idle/ready */
  state: string;
  /** on YOU, by the feed's rule: a card of this session filed under needs-you in the kernel's last feed
   *  build (the same column the feed's Blocked list shows: a question the agent asked and stopped on, a
   *  live prompt, an API error only you can clear); plus the tab's own alarm-red cases, which the feed
   *  build can trail by one push */
  needsYou: boolean;
  /** waiting on something that is not you: dispatched background work */
  waiting: boolean;
  /** what it is doing now, in the user's terms: the judges' current task, else the archiver's headline,
   *  else the most recent top task; "" when nothing is known */
  now: string;
  /** the session's own note of what it is working on (the postal working note: its claim to a branch and
   *  files, written for peer sessions), one line; "" when it has published none. A quieter second line of
   *  the row, under the now line, never in its place. */
  note: string;
  /** when it last did anything (epoch s): the newest event in the tail, else the state's start */
  lastT: number | null;
  /** the last assistant message, for the hover */
  lastMsg: string;
  closed: boolean;
  /** no session frame yet (a placeholder tab): name and color from the strip's meta alone */
  loading: boolean;
}

export interface SnapModel { name: string; color: string; rows: SnapRow[] }

const NOW_MAX = 200;      // the now line: one row, the CSS ellipsis does the rest; the cap bounds the model
const MSG_MAX = 400;      // the hover excerpt

const oneLine = (s: unknown, max: number): string => {
  const t = String(s ?? "").replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
};

function eventEpoch(ev: SnapEventLike): number | null {
  if (ev.ts) { const ms = Date.parse(ev.ts); if (!isNaN(ms)) return Math.floor(ms / 1000); }
  if (ev.kind === "postal-service" && ev.t != null) return Math.floor(ev.t);
  return null;
}

/** THE now line, in the user's terms: the judges' current task, then the archiver's headline, then the
 *  most recent top task. The working note is NOT a rung here: it is the session's claim to a branch and
 *  files, written for peer sessions, so it rides the row as its own line (noteLine) and never stands in
 *  for what the session is accomplishing. */
export function nowLine(lg: SnapLedgerLike | null | undefined): string {
  if (!lg) return "";
  const cur = (lg.tree || []).find((n) => n.current && oneLine(n.text, NOW_MAX));
  if (cur) return oneLine(cur.text, NOW_MAX);
  if (lg.summary && oneLine(lg.summary, NOW_MAX)) return oneLine(lg.summary, NOW_MAX);
  const r = (lg.recent || []).find((x) => oneLine(x.text, NOW_MAX));
  return r ? oneLine(r.text, NOW_MAX) : "";
}

/** The row's second line: the postal working note, one line, "" when none. */
export function noteLine(lg: SnapLedgerLike | null | undefined): string {
  return lg ? oneLine(lg.workingNote, NOW_MAX) : "";
}

/** The newest event's time, else the state's start. The tail is in transcript order, so the walk is
 *  from the end; an event with no time (a live-stream atom) is skipped, not zero. sinceEpoch is in
 *  MILLISECONDS everywhere (the kernel's since_ms, the client's own Date.now() placeholders), while the
 *  row's lastT is epoch SECONDS like the event times: passed through unconverted it would read as a time
 *  far in the future and every empty-tail row would say "0s ago". */
export function lastActivity(s: SnapSessionLike): number | null {
  const evs = s.events || [];
  for (let i = evs.length - 1; i >= 0; i--) { const t = eventEpoch(evs[i]); if (t) return t; }
  return s.status?.sinceEpoch ? Math.floor(s.status.sinceEpoch / 1000) : null;
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " };

/** A markdown message as plain words on one line, for a title attribute (a native tooltip renders text,
 *  so the source's markers would show as typed): HTML comments go whole, fence lines go, each remaining
 *  line loses its block and inline markers (docreview.ts stripInline, the file viewer's rule), the lines
 *  join with spaces, and the join loses what the viewer keeps on purpose and the transcript never shows
 *  as typed: HTML tags (the chat renders a reply through marked and DOMPurify, so <details>, <summary>,
 *  <br>, <b> paint as structure, never as text; each becomes a space), the entities that stand for a
 *  character once the tags are gone (&amp; &lt; &gt; &quot; &#39; &nbsp;), and underscore emphasis at
 *  word edges (_x_, __x__). snake_case keeps its underscores, as in the viewer; code spans are not
 *  parsed, so a tag typed inside backticks goes with the rest. */
export function plainText(md: string, max: number): string {
  const lines = md.replace(/<!--[\s\S]*?-->/g, " ").replace(/\r\n?/g, "\n").split("\n").filter((l) => !/^\s*(```|~~~)/.test(l));
  const joined = lines.map(stripInline).join(" ")
    .replace(/<\/?[A-Za-z][^<>]*>/g, " ")
    .replace(/&(amp|lt|gt|quot|#39|apos|nbsp);/g, (_, e: string) => ENTITIES[e])
    .replace(/(^|[^\w])_{1,2}([^_\s](?:[^_]*?[^_\s])?)_{1,2}(?=[^\w]|$)/g, "$1$2");
  return oneLine(joined, max);
}

/** The last assistant message in the tail, one line of plain text, for the hover. */
export function lastMessage(s: SnapSessionLike): string {
  const evs = s.events || [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e.kind === "assistant") { const t = plainText(String(e.md ?? e.text ?? ""), MSG_MAX); if (t) return t; }
  }
  return "";
}

/** The pip and the state word for a status: the tab's rule for the colors, the statusline's words. */
export function rowState(st: SnapStatusLike | null | undefined): { pip: SnapPip; state: string; needsYou: boolean; waiting: boolean; closed: boolean } {
  if (!st) return { pip: "unknown", state: "", needsYou: false, waiting: false, closed: false };
  const cls = tabStateClass(st);
  const s = st.state || "";
  if (cls === "tab-blocked") return { pip: "blocked", state: "needs you: stopped on an API error", needsYou: true, waiting: false, closed: false };
  if (cls === "tab-awaiting") return { pip: "awaiting", state: "needs you: waiting on your answer", needsYou: true, waiting: false, closed: false };
  if (cls === "tab-retrying") return { pip: "retrying", state: "API error, retrying on its own", needsYou: false, waiting: false, closed: false };
  if (cls === "tab-compacting") return { pip: "compacting", state: s === "clearing" ? "clearing" : "compacting", needsYou: false, waiting: false, closed: false };
  if (cls === "tab-closed") return { pip: "", state: "closed", needsYou: false, waiting: false, closed: true };
  if (cls === "tab-working") return { pip: "working", state: "working", needsYou: false, waiting: false, closed: false };
  if (s === "awaitingBg") return { pip: "waiting", state: "waiting on background work", needsYou: false, waiting: true, closed: false };
  if (s === "interrupting") return { pip: "", state: "interrupting", needsYou: false, waiting: false, closed: false };
  if (s === "opening") return { pip: "unknown", state: "opening", needsYou: false, waiting: false, closed: false };
  return { pip: "", state: "", needsYou: false, waiting: false, closed: false };
}

/** The state word for a session the feed files under needs-you while the tab's own rule sees nothing (an
 *  idle session that asked a question and stopped, the common case): the feed's own word for that column,
 *  and nothing more. The column also holds cards that stop nothing (a peer's held message waiting for your
 *  approval), so a claim like "stopped until you answer" would be false for some rows the word reaches;
 *  "needs you" is true of every card there. */
export const FEED_BLOCK_STATE = "needs you";

/** A member's name as its tab shows it; "(unnamed)" for a blank one (the tab-state.ts rule). */
const memberName = (s: { name?: string } | null | undefined): string => String(s?.name || "").trim() || "(unnamed)";

/** One row. `s` is the session frame (null for a placeholder tab, whose frame has not landed); `meta` the
 *  strip's meta for it (name and color), read only when the frame is absent, so a loading row still wears
 *  the tab's name and color. */
export function snapshotRow(id: string, s: SnapSessionLike | null | undefined, lg: SnapLedgerLike | null | undefined,
                            meta?: SnapMetaLike | null): SnapRow {
  const st = rowState(s?.status);
  const src: SnapMetaLike | null | undefined = s ?? meta;
  // NEEDS YOU is the feed's call: the tab's rule (tab-state.ts) knows only the live states the chip carries
  // (a permission or picker prompt, an on-you API error), so a judge-filed block on a session that went idle
  // after asking would show a plain idle row here while the feed shows a red card. lg.needsInput is that
  // column, per session, from the kernel's last feed build (build_session); the tab's own cases stay as a
  // floor because the feed build trails the chip by one push.
  const feedBlock = lg?.needsInput === true;
  return {
    id,
    name: memberName(src),
    color: src?.color && src.color.bg && src.color.fg ? { bg: src.color.bg, fg: src.color.fg } : null,
    pip: s ? st.pip : "unknown",
    state: st.state || (feedBlock && !st.closed ? FEED_BLOCK_STATE : ""),
    needsYou: feedBlock || st.needsYou,
    waiting: st.waiting,
    now: nowLine(lg),
    note: noteLine(lg),
    lastT: s ? lastActivity(s) : null,
    lastMsg: s ? lastMessage(s) : "",
    closed: st.closed,
    loading: !s,
  };
}

const sameRow = (a: SnapRow, b: SnapRow): boolean =>
  a.id === b.id && a.name === b.name && a.pip === b.pip && a.state === b.state
  && a.needsYou === b.needsYou && a.waiting === b.waiting && a.now === b.now && a.note === b.note
  && a.lastT === b.lastT && a.lastMsg === b.lastMsg && a.closed === b.closed && a.loading === b.loading
  && (a.color === b.color || (!!a.color && !!b.color && a.color.bg === b.color.bg && a.color.fg === b.color.fg));

export function sameModel(a: SnapModel | null | undefined, b: SnapModel): boolean {
  return !!a && a.name === b.name && a.color === b.color && a.rows.length === b.rows.length
    && a.rows.every((r, i) => sameRow(r, b.rows[i]));
}

/** The rows of one section: one per member in strip order. `session`/`ledger`/`meta` look up what the
 *  client holds (a null session = a placeholder tab, whose row takes its name and color from `meta`).
 *  Returns `prev` ITSELF when nothing a row shows has changed, so the caller can skip the rebuild (the
 *  same-object contract the tests pin). */
export function snapshotModel(sec: SnapSectionLike, session: (id: string) => SnapSessionLike | null | undefined,
                              ledger: (id: string) => SnapLedgerLike | null | undefined, prev: SnapModel | null,
                              meta?: (id: string) => SnapMetaLike | null | undefined): SnapModel {
  const next: SnapModel = { name: sec.name ?? "", color: sec.color || "",
                            rows: sec.ids.map((id) => snapshotRow(id, session(id), ledger(id), meta ? meta(id) : null)) };
  return prev && sameModel(prev, next) ? prev : next;
}

/** The heading's words: the section's name and its count, and the spoken label for the region. */
export function snapshotHeading(name: string, n: number): { count: string; label: string } {
  const count = `${n} session${n === 1 ? "" : "s"}`;
  return { count, label: `${name}: ${count}; click one to open it` };
}

/** A row's spoken label (name, needs you, state, what it is doing, its own note) and its hover title.
 *  NEEDS YOU is spoken whenever the row wears it: the tab's own state words carry it in their text; every
 *  other state word (working, closed, compacting) gets the word in front of it, where the painted chip sits
 *  beside the pip. The button's aria-label replaces its content for a reader, so a word only the chip
 *  carried would never be spoken. */
export function rowWords(r: SnapRow): { label: string; title: string } {
  const parts = [r.name];
  const stateWord = r.loading ? "opening" : r.state;
  if (r.needsYou && !stateWord.startsWith(FEED_BLOCK_STATE)) parts.push(FEED_BLOCK_STATE);
  if (stateWord) parts.push(stateWord);
  if (r.now) parts.push(r.now);
  if (r.note) parts.push(`its note: ${r.note}`);
  const title = (r.lastMsg ? `Last message: ${r.lastMsg}` : r.loading ? "opening…" : "No messages yet.") + "\nClick to open this session.";
  return { label: parts.join("; "), title };
}
