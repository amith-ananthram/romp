// Pure transcript-compaction logic (no DOM), so it can be unit-tested. Compact mode (the user
// 2026-06-14): hide "thinking" blocks entirely, and collapse each maximal run of TWO OR MORE consecutive
// tool uses into ONE summary line. A LONE tool stays inline — it renders first-class (its own tool line +
// expandable fold), since there's nothing to collapse and "Bash(…)" reads cleaner than "1 Bash" (the user
// 2026-06-22). Thinking is dropped FIRST, so tools separated only by thinking still count as consecutive;
// tools separated by visible content (an assistant reply, a prompt, …) do not. The rail timestamp logic
// runs over the RESULT of this, so the stamps reflect the compacted stream.
// The same shape folds LOW-STAKES NOTICES (2026-09-08, generalising the T131 retry-run fold): a run of ≥2
// consecutive foldable notices — a recovery, an effort change, a model swap, a reload, an interrupt + its
// settle, a background report, a system reminder, a romp notice — collapses into one `noticegroup` head.
// Which events are foldable is the caller's call (render.ts isFoldableNotice, passed per event): peers,
// API errors, compaction/clear boundaries, asks, to-dos, dividers and every bubble stay standalone.

/** The events compact mode may sweep into a noticegroup: the low-stakes, self-similar rows (a recovery, an effort
 *  change, a model swap, a reload, an interrupt marker and its settle, an injected notice: a system reminder or a
 *  romp notice on a user row). Peers, API errors, boundaries, asks, to-dos and every real bubble stay standalone.
 *  Pure over the event's shape so render.ts (the fold) and reveal-progress.ts (what counts as a message) share ONE
 *  reading (T336 review: the progress count took injected notices for messages). */
export function isFoldableNoticeShape(ev: { kind: string; interruptMarker?: unknown; interruptSettle?: unknown; rompSystem?: unknown; md?: unknown; source?: unknown; human?: unknown; undelivered?: unknown }): boolean {
  if (ev.kind === "retried" || ev.kind === "effortApplied" || ev.kind === "modelFallback" || ev.kind === "reconnecting") return true;
  if (ev.kind === "user") return !!(ev.interruptMarker || (ev.rompSystem && ev.md) || (ev.source && !ev.human && !ev.undelivered));
  if (ev.kind === "assistant") return !!ev.interruptSettle;
  return false;
}

export type DisplayItem =
  | { kind: "event"; index: number }            // a pass-through event, by its index in the source array
  | { kind: "toolgroup"; indices: number[] }    // a collapsed run of ≥2 consecutive tool uses (a lone tool is an "event")
  | { kind: "noticegroup"; indices: number[] }; // a collapsed run of ≥2 consecutive foldable notices (was the "retried"-only retrygroup)

// Tools that are an EXCEPTION to collapsing: they render FIRST-CLASS even in compact mode, never swept
// into a toolgroup (the user 2026-06-17). AskUserQuestion is the "↳ You answered Claude's question" box —
// a reply to a popup, not bookkeeping — so it must stay visible, not buried under a collapsed tool run.
export const STANDALONE_TOOLS = new Set<string>(["AskUserQuestion"]);

// Given the per-event `kind` strings (and, for tool events, the tool `names` so the standalone-tool
// exception can be applied; and `notices[i]` = whether event i is a foldable notice), produce the
// compacted display list. `names[i]` is the tool name for a "tool" event, undefined otherwise. Without
// `notices`, only a bare "retried" run folds (the pre-2026-09-08 behaviour, kept for callers that pass none).
export function compactDisplay(kinds: readonly string[], names?: readonly (string | undefined)[], notices?: readonly boolean[]): DisplayItem[] {
  const out: DisplayItem[] = [];
  let run: number[] | null = null;
  let noticeRun: number[] | null = null;          // consecutive foldable notices
  // a LONE tool passes through as a normal event (its first-class inline tool line + fold); only a run of
  // TWO OR MORE collapses into a summary toolgroup (the user 2026-06-22)
  const flush = () => {
    if (!run) return;
    out.push(run.length === 1 ? { kind: "event", index: run[0] } : { kind: "toolgroup", indices: run });
    run = null;
  };
  // same shape for notice runs (the user 2026-08-27, seventeen consecutive recovery rows): a lone notice
  // stays a first-class row; a run of ≥2 collapses into one expandable head
  const flushNotices = () => {
    if (!noticeRun) return;
    out.push(noticeRun.length === 1 ? { kind: "event", index: noticeRun[0] } : { kind: "noticegroup", indices: noticeRun });
    noticeRun = null;
  };
  const foldable = (i: number) => notices ? !!notices[i] : kinds[i] === "retried";
  for (let i = 0; i < kinds.length; i++) {
    const k = kinds[i];
    if (k === "thinking") continue;                 // hidden — and does NOT break a tool run
    // A standalone tool (AskUserQuestion) is NOT collapsed: it breaks the run and passes through as its
    // own event, so renderTool → renderAsk draws the first-class box instead of "+1" inside a group.
    if (k === "tool" && !STANDALONE_TOOLS.has(names?.[i] ?? "")) { flushNotices(); (run ||= []).push(i); continue; }
    if (foldable(i)) { flush(); (noticeRun ||= []).push(i); continue; }
    flush();
    flushNotices();
    out.push({ kind: "event", index: i });
  }
  flush();
  flushNotices();
  return out;
}

// One pluralized count of a tool kind, e.g. { label: "Edits", count: 3 }. The label keeps the tool's
// own Capitalized name (so it reads AS a tool — the user 2026-06-14, matching the bold .tool-name in
// the non-compact view); only the Edit variants merge under "Edit".
export interface ToolCount { label: string; count: number; }

const LABEL: Record<string, string> = { Edit: "Edit", MultiEdit: "Edit", NotebookEdit: "Edit" };
function toolLabel(name: string): string { return LABEL[name] || name; }   // else the tool's own name
function plural(word: string, n: number): string {
  if (n === 1) return word;
  return word + (/(s|sh|ch|x|z)$/i.test(word) ? "es" : "s");
}

// Counts per tool kind from a run of tool NAMES: merge to a display label, order by count (desc; ties
// keep first-appearance via stable sort), pluralize by count.
export function toolCounts(names: readonly string[]): ToolCount[] {
  const counts = new Map<string, number>();
  for (const nm of names) { const w = toolLabel(nm); counts.set(w, (counts.get(w) || 0) + 1); }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([w, n]) => ({ label: plural(w, n), count: n }));
}

// "3 Edits, 2 Reads, 1 Bash" — the plain-text form (used for titles / tests). The rendered line styles
// each tool label in bold (see render.ts renderToolGroup), but the words are identical.
export function summarizeTools(names: readonly string[]): string {
  return toolCounts(names).map((c) => `${c.count} ${c.label}`).join(", ");
}
