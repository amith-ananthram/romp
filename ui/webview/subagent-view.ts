// Subagent transcripts (plans/subagent-transcripts.md, 2026-09-05): the pure half of the viewer —
// tab ids, labels, the Agent head's preview rows, fold label and full steps list, and the two line icons. render.ts owns the DOM (the arrow on
// the Agent head and the bg-task row, the peek-tab viewer, the header) and the kernel protocol
// (openSubagent / closeSubagent ↔ {type:"subagent"} frames); this module is what the source pins and
// the executable tests exercise without a DOM.
//
// The viewer TAB ID is `<parentId>/agent/<agentId>` — the parent's id (host-prefixed under federation,
// "host:sid") plus a colon-free suffix. host-prefix.ts reads the FIRST colon as the host marker, so a
// `sub:<sid>:<agentId>` shape would have named a phantom host "sub" everywhere hostOf() is consulted
// (the offline mark, the strip's host dimming, outbound routing); a suffix keeps hostOf(subId) ===
// hostOf(parentId) for free. The suffix never reaches the kernel as a session id (openSubagent carries
// the parent id + agentId), and a bare "/agent/" cannot occur in a uuid or a host name.

import { agentRowOf, waitsNote, type AwaitRow } from "./spin-caption";

export const SUB_SEP = "/agent/";

export function subTabId(parentId: string, agentId: string): string { return parentId + SUB_SEP + agentId; }
export function isSubId(id: string | null | undefined): boolean { return typeof id === "string" && id.includes(SUB_SEP); }
export function subParts(id: string): { parentId: string; agentId: string } | null {
  if (typeof id !== "string") return null;
  const i = id.indexOf(SUB_SEP);
  if (i <= 0) return null;
  const agentId = id.slice(i + SUB_SEP.length);
  return agentId ? { parentId: id.slice(0, i), agentId } : null;
}

export interface SubMeta { agentType?: string; description?: string; spawnDepth?: number | null; toolUseId?: string; }
// One tool call the agent made (the kernel's agentSteps row): the tool name and its one-line gist in the
// head vocabulary. The kernel ships every call so far, oldest first, capped at the NEWEST 200
// (SUBAGENT_STEPS_CAP) with stepsTotal saying the true count.
export interface AgentGistRow { tool: string; desc: string; ts?: string; }
// The running preview's clock: the call count and the agent's first/last record stamps. Ships only
// while the agent runs; the preview's three rows come from the steps (no `recent` on the wire since
// 2026-09-05 — the fold lists the same steps, so one field feeds both).
export interface AgentGist { calls: number; since?: string | null; last?: string | null; }

// The tab label: the sidecar's description (what the parent asked for), clipped to a tab's worth; else
// the agent type; else the bare word. Never the agent id — a hex string says nothing at a glance.
export const SUB_LABEL_MAX = 28;
export function subLabel(meta: SubMeta | null | undefined): string {
  const d = (meta?.description || "").trim();
  if (d) return d.length > SUB_LABEL_MAX ? d.slice(0, SUB_LABEL_MAX - 1).trimEnd() + "…" : d;
  const t = (meta?.agentType || "").trim();
  return t || "subagent";
}

// Elapsed since an ISO stamp, in the statusline timer's own vocabulary ("40s", "2m 5s", "1h 3m") —
// the same shape elapsedMs() prints beside the working chip, so the preview's clock reads like the
// pane's other clocks. "" when the stamp is unreadable.
export function elapsedSince(iso: string | null | undefined, nowMs: number): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!isFinite(t)) return "";
  const s = Math.max(0, Math.floor((nowMs - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// The level-0 preview's rows: the agent's LAST THREE tool calls in the head vocabulary (`<tool> <desc>`),
// newest LAST (the kernel ships the steps oldest→newest), with the trailing count/elapsed on the last
// row — "· 12 tool calls · 40s". Only while the kernel ships the clock (the agent is running); the
// caller shows nothing otherwise. Pure, so the shape is testable without a DOM.
export const PREVIEW_ROWS = 3;
export interface GistLine { tool: string; desc: string; meta: string; }
function stepLine(r: AgentGistRow, meta: string): GistLine {
  return { tool: String(r.tool || "tool"), desc: String(r.desc || ""), meta };
}
function clockMeta(g: AgentGist | null | undefined, nowMs: number, withCount: boolean): string {
  if (!g) return "";
  const n = Math.max(0, g.calls | 0);
  const parts: string[] = [];
  if (withCount && n) parts.push(`${n} tool call${n === 1 ? "" : "s"}`);
  const el = elapsedSince(g.since, nowMs);
  if (el) parts.push(el);
  return parts.length ? "· " + parts.join(" · ") : "";
}
export function gistLines(steps: AgentGistRow[] | null | undefined, g: AgentGist | null | undefined, nowMs: number): GistLine[] {
  if (!g || !Array.isArray(steps) || !steps.length) return [];
  const rows = steps.slice(-PREVIEW_ROWS);
  const meta = clockMeta(g, nowMs, true);
  return rows.map((r, i) => stepLine(r, i === rows.length - 1 ? meta : ""));
}

// The fold's FULL list: every shipped step, same vocabulary and order; while the agent runs (a clock
// ships) the last row trails the elapsed alone — the count already sits in the fold label. A finished
// agent's rows carry no meta.
export function stepLines(steps: AgentGistRow[] | null | undefined, g: AgentGist | null | undefined, nowMs: number): GistLine[] {
  if (!Array.isArray(steps) || !steps.length) return [];
  const meta = clockMeta(g, nowMs, false);
  return steps.map((r, i) => stepLine(r, i === steps.length - 1 ? meta : ""));
}

// The one line above the list when the kernel's cap cut the oldest steps: "57 earlier calls not shown".
// "" when every call is on the wire.
export function stepsNote(shown: number, total: number | null | undefined): string {
  const n = Math.max(0, (total || 0) - shown);
  return n ? `${n} earlier call${n === 1 ? "" : "s"} not shown` : "";
}

// The Agent head's fold label — what ONE click reveals, in the order it appears: the prompt, the tool
// calls (omitted at zero), and — once the agent has finished — the report with its line count.
//   running:  "prompt · 12 tool calls"
//   finished: "prompt · 12 tool calls · report · 3 lines"
export function agentFoldLabel(o: { stepsTotal?: number | null; reportLines?: number | null }): string {
  const parts = ["prompt"];
  const n = Math.max(0, o.stepsTotal || 0);
  if (n) parts.push(`${n} tool call${n === 1 ? "" : "s"}`);
  if (o.reportLines != null) parts.push(`report · ${o.reportLines} line${o.reportLines === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

// The viewer header's one line: "subagent of <parent> · <agentType> · running|finished". The parent
// name is rendered by the caller as a link (hostNameNodes), so this returns the pieces, not a string.
export function subHeadParts(meta: SubMeta | null | undefined, running: boolean): { type: string; state: "running" | "finished" } {
  return { type: (meta?.agentType || "").trim() || "agent", state: running ? "running" : "finished" };
}

// The header's "· waiting on <what>" tail (2026-09-10): what this agent is itself waiting on, read from the
// PARENT session's awaited rows — the same nested `waits` the parent's box draws under the agent's row —
// so the viewer and the box can never disagree. Only while the agent runs: a finished agent waits on
// nothing, whatever a stale row says. "" when the parent lists no waits for it.
export function subWaitTail(running: boolean, parentItems: readonly AwaitRow[] | null | undefined, agentId: string | null | undefined): string {
  if (!running) return "";
  const note = waitsNote(agentRowOf(parentItems, agentId));
  return note ? "waiting on " + note : "";
}

// The two line icons, in the house style every glyph in render.ts wears (16-unit viewBox, currentColor
// stroke 1.4, round caps/joins): the OPEN arrow (a corner box with an arrow leaving it — "open this
// elsewhere") and the PIN (a pushpin: head, collar, needle). Trusted constant markup.
const ICON_OPEN = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
  + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M7 3.5H4a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V9"/>'
  + '<path d="M9.5 3h3.5v3.5"/><path d="M13 3 7.5 8.5"/></svg>';
const ICON_PIN = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
  + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M6 2.5h4v4.2l1.8 2.3H4.2L6 6.7z"/><path d="M8 9v4.5"/></svg>';
export function openIconSvg(): string { return ICON_OPEN; }
export function pinIconSvg(): string { return ICON_PIN; }
