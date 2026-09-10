// @-mention autocomplete for the composer (the user 2026-09-07): typing "@ro" in the message box lists
// the live sessions whose names match, and a pick puts the plain name in the text the way an agent
// addresses a peer with its mail tools ("@web ", or "@host:web " for a session on another kernel, the
// form postal takes to break a name tie; see mentionToken for whose kernel the host is relative to).
// This module is the PURE half, so it runs for real under node --test: the trigger rule, the matcher,
// the insertion arithmetic, the keyboard model and the transcript segmenter. render.ts owns the DOM:
// the card, its rows, the composer wiring, the chip.
import { hostOf, hostPrefix } from "./host-prefix";

export interface MentionCandidate {
  id: string;        // the session id as the webview holds it ("host:uuid" for a session on another kernel)
  name: string;      // the display name as the webview holds it ("host:name" for a session on another kernel)
  color?: { bg: string; fg: string } | null;
}

/** The "@query" token the caret ends: where its "@" sits and the text after it. */
export interface MentionQuery { start: number; query: string; }

/** Rows the card shows at most. Twelve fit its 40vh without a scroll on any laptop; past that the
 *  final row says how many matches were left out (mentionMoreNote), so a cap is never a silent drop. */
export const MENTION_MAX_ROWS = 12;

/** The "@query" token the caret sits at the end of, or null. The "@" must OPEN a word: at the start
 *  of the text or after whitespace, so an email address (a@b.example) or a path (/tmp/@x) never
 *  triggers. The query is the run of characters after the "@" up to the caret, with no whitespace and
 *  no second "@", at least one character long; and the caret must END the word, so a caret placed back
 *  inside "@ro|mp" opens nothing. */
export function mentionQuery(text: string, caret: number): MentionQuery | null {
  if (typeof text !== "string" || caret < 0 || caret > text.length) return null;
  if (caret < text.length && !/\s/.test(text[caret])) return null;
  const m = /(^|\s)@([^\s@]+)$/.exec(text.slice(0, caret));
  if (!m) return null;
  return { start: caret - m[2].length - 1, query: m[2] };
}

/** The name without the "host:" the viewer prefixed onto a session from another kernel (host-prefix.ts). */
export function mentionBareName(c: MentionCandidate): string {
  const p = hostPrefix(c.name, c.id);
  return p ? p.rest : c.name;
}

/** Every match in the roster, ranked, case-insensitively: a prefix of the name first, then a prefix
 *  of the whole "host:name" (a remote session found by its host), then a substring anywhere;
 *  alphabetical by bare name within a rank, and a local session ahead of a remote namesake. The
 *  session being written to is left out (a message to it never needs its own name). An empty query
 *  matches nothing: the card opens on "@" plus a character, never on the bare "@". Uncapped: the card
 *  shows the first MENTION_MAX_ROWS (matchMentions) and says how many more there were (mentionMoreNote). */
export function rankMentions(query: string, roster: readonly MentionCandidate[], selfId: string | null | undefined): MentionCandidate[] {
  const q = (query || "").toLowerCase();
  if (!q) return [];
  const scored: { c: MentionCandidate; rank: number; key: string; remote: number }[] = [];
  for (const c of roster) {
    if (!c || !c.name || (selfId && c.id === selfId)) continue;
    const bareName = mentionBareName(c);
    const bare = bareName.toLowerCase();
    const full = c.name.toLowerCase();
    const rank = bare.startsWith(q) ? 3 : full.startsWith(q) ? 2 : full.includes(q) ? 1 : 0;
    if (rank) scored.push({ c, rank, key: bare, remote: bareName === c.name ? 0 : 1 });
  }
  scored.sort((a, b) => b.rank - a.rank || a.key.localeCompare(b.key) || a.remote - b.remote || a.c.name.localeCompare(b.c.name));
  return scored.map((x) => x.c);
}

/** The rows the card shows: the best MENTION_MAX_ROWS of rankMentions. */
export function matchMentions(query: string, roster: readonly MentionCandidate[], selfId: string | null | undefined): MentionCandidate[] {
  return rankMentions(query, roster, selfId).slice(0, MENTION_MAX_ROWS);
}

/** The card's last row when the cap dropped matches: "N more, keep typing", plain and not selectable,
 *  so the user sees that a narrower query is the way to the rest; null when every match is shown.
 *  `total` is rankMentions' length. */
export function mentionMoreNote(total: number): string | null {
  const more = total - MENTION_MAX_ROWS;
  return more > 0 ? more + " more, keep typing" : null;
}

/** The text a pick inserts: "@" + the name as the RECEIVING agent's mail tools take it, then one
 *  space so typing continues. Plain text, no markup: the agent reads the name literally.
 *
 *  Postal resolves "host:name" on the kernel the agent runs on: the host must be that kernel's own
 *  postal name or the label under which IT peers with the named kernel (postal_service.py,
 *  resolve_recipient and peer_route). So the right form depends on who is being written to:
 *  - a session on THIS kernel: the display name the webview holds is exactly right. Bare for a local
 *    session; "host:name" for a remote one, since this kernel registers that peer under the same host
 *    label the frame prefixes (kernel.py _notify_bus_peer).
 *  - a session on ANOTHER kernel: the frame carries none of that kernel's labels. The viewer's label
 *    for it need not be its own hostname, and its labels for the viewer's kernel or a third host are
 *    unknowable here. Every name goes in bare: the recipient's own siblings resolve as locals there,
 *    and a name that is ambiguous there is refused by postal with the candidates listed as host:name,
 *    which the agent can pick from (docs/guide.md says so).
 *  `recipientId` is the session being written to as the webview holds it ("host:uuid" for a remote);
 *  omitted, or a local session, and the display name stands. */
export function mentionToken(c: MentionCandidate, recipientId?: string | null): string {
  return "@" + (hostOf(recipientId || "") ? mentionBareName(c) : c.name) + " ";
}

/** Replace the typed "@query" (from its "@" to the caret) with the token; the caret lands after it.
 *  The caret must still END that token, so text.slice(at.start, caret) is "@" + at.query. When it does
 *  not (the card stayed open across a caret move the input handler never saw: Ctrl+A, PageUp, a drag
 *  that ended outside the composer), the splice would repeat the draft between the two positions ("ask
 *  @ro" with the caret at 0 would come out as "ask @romp ask @ro"), so the insert is REFUSED: the text
 *  and the caret come back unchanged, and the caller closes the card. */
export function insertMention(text: string, at: MentionQuery, caret: number, token: string): { text: string; caret: number } {
  if (caret < at.start || caret > text.length || text.slice(at.start, caret) !== "@" + at.query) return { text, caret };
  const head = text.slice(0, at.start);
  return { text: head + token + text.slice(caret), caret: head.length + token.length };
}

export type MentionKeyAction =
  | { kind: "move"; sel: number }
  | { kind: "pick"; sel: number }
  | { kind: "close" }
  | null;

/** What a key does while the card is open, given the highlighted row and the row count. Up and down
 *  move the highlight and wrap; Enter and Tab pick the highlighted row; Escape closes without
 *  inserting. Null means the key is not the card's: the composer handles it as usual. With the card
 *  closed nothing is consumed, so Enter keeps sending; and a modifier (Shift+Enter for a newline,
 *  Cmd/Ctrl+Enter to stage) keeps its own meaning even while the card is up. */
export function mentionKeyAction(key: string, open: boolean, sel: number, count: number, modifier = false): MentionKeyAction {
  if (!open || count <= 0 || modifier) return null;
  if (key === "ArrowDown") return { kind: "move", sel: (sel + 1) % count };
  if (key === "ArrowUp") return { kind: "move", sel: (sel - 1 + count) % count };
  if (key === "Enter" || key === "Tab") return { kind: "pick", sel: Math.min(Math.max(sel, 0), count - 1) };
  if (key === "Escape") return { kind: "close" };
  return null;
}

export interface MentionSegment<T> { text: string; hit?: T; }

/** Split a run of plain text at the "@name" tokens that name a live session: `lookup` answers the
 *  session for a word, or null. The same word rule as the trigger: the "@" opens the word and the word
 *  runs to whitespace or the end with no second "@" in it, so an email address, a path and a
 *  "@name@handle" (a fediverse-style handle, or a typo; the trigger never opened a card for it) all
 *  stay text. Trailing sentence punctuation is left outside the token ("ask @web." names web). A word
 *  that names nothing stays text. The segments concatenate back to the input exactly. */
export function mentionSegments<T>(text: string, lookup: (word: string) => T | null | undefined): MentionSegment<T>[] {
  const out: MentionSegment<T>[] = [];
  const re = /(^|\s)@([^\s@]+)(?=\s|$)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const word = m[2].replace(/[.,;:!?)\]}'"]+$/, "");
    const hit = word ? lookup(word) : null;
    if (!hit) continue;
    const start = m.index + m[1].length;
    if (start > last) out.push({ text: text.slice(last, start) });
    out.push({ text: "@" + word, hit });
    last = start + 1 + word.length;
    re.lastIndex = last;   // rescan from the token's end, so stripped punctuation stays plain text
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
