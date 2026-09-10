// STAGED messages (the user 2026-08-15): compose against a highlight, hold it, keep reading, then release
// the whole run together. ⌘/Ctrl+⏎ stages the composer's text WITH its citation chips; a plain send
// releases the stack in stage order as one message with the typed message last; the strip's Send now
// releases the stack alone. Deliberately NOT "queued": queued is romp's injection-side wait (sent,
// pending injection into the session); staged is user-side, not sent at all, just held where it was
// written so each message keeps the context it was written against.
//
// This is the PURE stack (per-tab isolation, order, flush-clears, discard, persistence round-trip), the
// outgoing BODY (quoteReplyBody, stagedRunBody) and the release's post list (stagedPosts), so
// staged-messages.test.ts EXECUTES the rules instead of regexing render.ts (the repo's
// extract-for-execution idiom). The DOM strip and the send routing live in render.ts.

export interface StagedMsg { text: string; cites: unknown[] }

/** The outgoing body for QUOTE citations (the user 2026-07-13): the highlighted text rides ahead of the
 *  typed message as a markdown quote block, so the agent knows exactly which part is being replied to.
 *  Also what the chip's audit preview shows: one function, no drift. Stacked chips (the user 2026-08-04)
 *  become one section each, in the order they sit in the strip. `src` (the VS Code editor flavor,
 *  2026-07-13) names where a highlight came from, a workspace-relative file:lines, so that section's
 *  lead-in points the agent at the code, not the conversation. No quote at all is the text alone. */
export function quoteReplyBody(cites: { quote?: string; src?: string | null }[], text: string): string {
  const sections = cites.map((c) => {
    const q = (c.quote || "").split("\n").map((l) => "> " + l).join("\n");
    const lead = c.src ? "Replying to this highlighted code (" + c.src + "):" : "Replying to this part of the conversation:";
    return lead + "\n" + q;
  });
  const quoted = sections.join("\n\n");
  return quoted && text ? quoted + "\n\n" + text : quoted || text;
}

/** ONE body for a staged run: each item in stage order as its own section, the quote block(s) it was
 *  written against and then its comment, byte for byte what the item sent when each item was its own
 *  message; sections separated by a blank line; the typed message, when the send carries one, last. An
 *  item with nothing to say (no quote, no text) adds nothing; no items and nothing typed is "". A goal
 *  citation is not a quote and plays no part here: the kernel wraps a goal follow-up itself (render.ts
 *  routes those). */
export function stagedRunBody(items: readonly StagedMsg[], typed?: { text: string; cites?: unknown[] } | null): string {
  const parts: string[] = [];
  for (const it of typed ? [...items, typed] : items) {
    const quotes = (it.cites || []).filter((c): c is { quote: string; src?: string | null } =>
      !!c && typeof (c as { quote?: unknown }).quote === "string" && !!(c as { quote?: string }).quote);
    const s = quoteReplyBody(quotes, it.text || "");
    if (s) parts.push(s);
  }
  return parts.join("\n\n");
}

/** What one send posts: the words, the citation chips they ride with (a goal chip makes the post a
 *  follow-up on that card; quote chips wrap client-side) and the image paths the echo renders as
 *  thumbnails. The typed message a release carries has this shape, and so does each post stagedPosts
 *  returns. */
export interface Post { text: string; cites?: unknown[]; imgPaths?: string[] }

// a goal citation names the card the words follow up on; the kernel wraps one goal per message
const isGoalCite = (c: unknown): boolean =>
  !!c && typeof (c as { itemId?: unknown }).itemId === "string" && !!(c as { itemId?: string }).itemId;

/** The kernel's own shape test for a slash command, mirrored (kernel/kernel.py _SLASH_CMD_RE: a slash, a
 *  name, then whitespace or the end, at the head of the trimmed text). Shape-matched, not checked against
 *  a command list, as there: the CLI owns what executes; this only decides that the text must reach it
 *  alone. The name's tail is Unicode letters and digits, underscore, colon and hyphen, because Python's
 *  \w is Unicode where JavaScript's is ASCII, and a user-defined command may carry an accented letter. */
const SLASH_COMMAND_RE = /^\/[A-Za-z0-9][\p{L}\p{N}_:-]*(\s|$)/u;
export function isSlashCommand(text: string): boolean { return SLASH_COMMAND_RE.test((text || "").trim()); }

/** The posts a release makes, in order (render.ts routes each through routeUserMessage). The rule is ONE
 *  message for the run, with two kinds of item that go on their own, at their place in stage order, so
 *  nothing arrives out of the order it was staged in: a GOAL follow-up (the kernel wraps one goal per
 *  message, askFollowUp) and a SLASH COMMAND (the kernel fires a command only at the head of its own
 *  text: after another section it is prose the agent reads; ahead of one it takes the sections after it as
 *  its argument). Each goes exactly as it went when every item was its own message, its own cites with
 *  it: a bare command reaches the CLI as itself and fires; a command staged with a quote chip is wrapped
 *  by routeUserMessage as any quoted message is, and one with a goal chip by the kernel's follow-up
 *  wrapper, so either reads as prose, exactly as it did before, and going alone costs it nothing. The
 *  items between them share one body. The typed message closes the last run (a typed goal cite
 *  wraps that run; the typed images ride it) unless it is itself a command, which goes last on its own;
 *  with no run to close, the typed message posts as itself, cites and images intact, exactly as a send
 *  with nothing staged. Nothing staged and nothing typed posts nothing. */
export function stagedPosts(items: readonly StagedMsg[], typed?: Post | null): Post[] {
  const posts: Post[] = [];
  let run: StagedMsg[] = [];
  const close = (last?: Post | null) => {
    if (!run.length) return;
    const goal = last ? (last.cites || []).find(isGoalCite) : undefined;
    posts.push({ text: stagedRunBody(run, last), cites: goal ? [goal] : undefined, imgPaths: last?.imgPaths });
    run = [];
  };
  for (const it of items) {
    if ((it.cites || []).some(isGoalCite) || isSlashCommand(it.text)) { close(); posts.push({ text: it.text, cites: it.cites }); }
    else run.push(it);
  }
  if (typed && run.length && !isSlashCommand(typed.text)) close(typed);
  else { close(); if (typed) posts.push({ text: typed.text, cites: typed.cites, imgPaths: typed.imgPaths }); }
  return posts;
}

export class StagedStack {
  private m = new Map<string, StagedMsg[]>();

  list(sid: string): readonly StagedMsg[] { return this.m.get(sid) || []; }
  count(sid: string): number { return (this.m.get(sid) || []).length; }

  push(sid: string, msg: StagedMsg): void {
    const l = this.m.get(sid) || [];
    l.push(msg);
    this.m.set(sid, l);
  }

  removeAt(sid: string, i: number): void {
    const l = this.m.get(sid);
    if (!l) return;
    l.splice(i, 1);
    if (!l.length) this.m.delete(sid);
  }

  /** The flush: every staged message for this tab, in stage order, and the stack is now empty —
   *  release is one-shot, never a re-send. */
  takeAll(sid: string): StagedMsg[] {
    const l = this.m.get(sid) || [];
    this.m.delete(sid);
    return l;
  }

  /** Persistence shape (rides the drafts store): sid → messages. */
  entries(): Record<string, StagedMsg[]> {
    return Object.fromEntries(this.m);
  }

  /** Hydrate from a persisted shape; junk entries are dropped, never a crash. */
  restore(saved: unknown): void {
    if (!saved || typeof saved !== "object") return;
    for (const [sid, v] of Object.entries(saved as Record<string, unknown>)) {
      const list: StagedMsg[] = [];
      for (const m of Array.isArray(v) ? v : []) {
        if (m && typeof (m as any).text === "string" && (m as any).text)
          list.push({ text: (m as any).text, cites: Array.isArray((m as any).cites) ? (m as any).cites : [] });
      }
      if (list.length) this.m.set(sid, list);
    }
  }
}
