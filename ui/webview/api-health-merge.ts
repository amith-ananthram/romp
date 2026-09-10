// The API-health signal on the shell's rail, across EVERY connected kernel (T301, the user 2026-09-10): the
// merge of the per-host frames into the one dot, the plain-words reading of one kernel's /api-health document,
// and the merge of several such documents. Pure (no DOM), so the rules are unit-tested; the shell's inline
// script (kernel.py _LANDING_APIH_JS) reaches them through window.__rompApiHealthMerge (api-health-global.ts).
//
// The federation rule holds throughout: per-host MAPS in, per-host lines out; nothing here adds a count from
// one kernel to a count from another or compares two kernels' clocks. "Worst state wins" is a comparison of
// STATE WORDS, which are the same vocabulary on every kernel.
//
// The dot follows the FRAMES alone (review find, 2026-09-10): each kernel's frame carries `quiet` (no API event
// in its longest window) and `errs` (failed attempts in it), rebuilt every cycle and pushed on change, so the dot
// changes on the kernel's events and never on a history reading the browser took at some earlier hover. The
// readings only word each machine's line in the popup.
//
// T316 (the user 2026-09-10, the design follow-up): a machine's line is its COUNTS over the popup's window, each
// class in its own colour (successes in the accent, 429 in the blocked red, 5xx in the 5xx magenta, no connection
// and other statuses in the label gray), never a verdict word; the window is the ledger's 24 hours when the kernel
// serves one, else the document's longest window; the histograms are stacked bars from the ledger's tiers.

/** One kernel's apiHealth shell frame, local half (the fields the merge reads). */
export interface HostFrame {
  state: "ok" | "degraded" | "paused" | string;
  cls?: string;          // "429" | "529" | "offline" | "errors" | ""
  text?: string;         // the kernel's own headline
  waiting?: number;
  reason?: string;       // a pause's reason: limit | spend | manual
  since?: number;
  quiet?: boolean;       // that kernel saw no API event in its longest window (T301): gray
  errs?: number;         // failed attempts that kernel counted in its longest window (T301): red while any
  stale?: boolean;       // the tunnel to that host is not up, or its frame could not be read: the frame is the last one heard
  fault?: string;        // why the last read of that host's frame was refused ("HTTP 403"), when it was
  host?: string;         // the kernel's own name to its peers (T316): the local machine's line names it
}

/** The dot's three states: the accent when everything is fine, red when errors are being met, gray when quiet. */
export type Dot = "fine" | "errors" | "quiet";

/** One coloured piece of a machine's line: the class names the colour token the shell paints it with. */
export interface Seg { text: string; kind: "plain" | "ok" | "r429" | "r5xx" | "none" }

export interface MachineLine {
  host: string;          // "" for the local machine
  name: string;          // what the line calls it: the kernel's own name, or "this machine" when the frame has none
  dot: Dot;
  parts: Seg[];          // the words after the name, coloured by class; empty until the frame or a reading says something
  text: string;          // the same line as plain text: "TESTHOST: 67 successful requests · 12 429s"
  stale: boolean;        // named, not counted: a machine not reachable right now has no say in the dot
}

export interface Merged {
  dot: Dot;
  worst: string;                 // the host that set the dot ("" for the local machine)
  machines: MachineLine[];       // one line per machine, local first, then by name
  n: number;                     // machines counted
}

/** Rank of a frame's state for "worst wins": paused and degraded are both errors; ok is fine. */
function frameRank(f: HostFrame): number {
  return f.state === "paused" || f.state === "degraded" ? 2 : f.state === "ok" ? 1 : 0;
}

/** A frame's dot, from the frame alone: errors when its kernel says a session is waiting on the API (degraded),
 *  is paused, or counted a failed attempt in its longest window (`errs`); quiet when it saw no event in that window;
 *  else fine. A frame with neither flag (an older kernel) is fine or errors on its state word. */
export function frameDot(f: HostFrame): Dot {
  if (f.state === "paused" || f.state === "degraded") return "errors";
  if ((f.errs || 0) > 0) return "errors";
  if (f.quiet === true) return "quiet";
  return "fine";
}

const CLS_WORDS: Record<string, string> = {
  "429": "rate limited", "529": "overloaded", "offline": "offline", "errors": "errors",
};

function plural(n: number, w: string): string { return n + " " + w + (n === 1 ? "" : "s"); }

/** The counts of one window as coloured pieces: successes first, then each failure class present, in its colour;
 *  no traffic at all is the one plain phrase. Never a verdict word. */
export function countsParts(c: Counts): Seg[] {
  if (c.attempts <= 0) return [{ text: "no API traffic", kind: "plain" }];
  const out: Seg[] = [];
  if (c.ok) out.push({ text: plural(c.ok, "successful request"), kind: "ok" });
  if (c.r429) out.push({ text: c.r429 + " 429" + (c.r429 === 1 ? "" : "s"), kind: "r429" });
  if (c.r5xx) out.push({ text: c.r5xx + " 5xx", kind: "r5xx" });
  if (c.none) out.push({ text: c.none + " no connection", kind: "none" });
  if (c.other) out.push({ text: plural(c.other, "other error"), kind: "none" });
  return out;
}

/** The words after a machine's name: the kernel's own headline when its frame carries one (a pause, sessions
 *  waiting on the API), else the counts its history read (its reading), else what the frame alone says (quiet, a
 *  count of failed attempts) until the reading lands. */
export function stateParts(f: HostFrame, reading?: Reading | null): Seg[] {
  if (f.state === "paused") {
    const why = f.reason === "limit" ? "paused until the usage limit resets" : f.reason === "spend" ? "paused at the spend limit" : "paused by you";
    return [{ text: why + (f.waiting ? " · " + f.waiting + " waiting" : ""), kind: "plain" }];
  }
  if (f.state === "degraded") {
    return [{ text: (CLS_WORDS[f.cls || ""] || "errors") + (f.waiting ? " · " + f.waiting + " waiting" : ""), kind: "plain" }];
  }
  if (reading) return countsParts(reading.counts);
  if ((f.errs || 0) > 0) return [{ text: plural(f.errs as number, "failed attempt") + " in the last 15 min", kind: "plain" }];
  if (f.quiet === true) return [{ text: "no API traffic", kind: "plain" }];
  return [];
}

function joinParts(parts: Seg[]): string { return parts.map((p) => p.text).join(" · "); }

/** One machine's line. A machine not reachable right now is named with what was last heard from it (and why the
 *  read failed, when a read was refused), and reads as such rather than as its old state. The local machine is
 *  named by its kernel's own name (`selfName`, the frame's `host`), "this machine" only when none is known. */
export function machineLine(host: string, f: HostFrame, reading?: Reading | null, selfName?: string): { name: string; parts: Seg[]; text: string } {
  const name = host || selfName || f.host || "this machine";
  const words = f.state ? stateParts(f, reading) : [];
  let parts: Seg[];
  if (f.fault) parts = [{ text: "could not read its API health (" + f.fault + ")" + (words.length ? ", last seen " + joinParts(words) : ""), kind: "plain" }];
  else if (f.stale) parts = [{ text: "not reachable, last seen " + (words.length ? joinParts(words) : "fine"), kind: "plain" }];
  else parts = words;
  return { name, parts, text: name + (parts.length ? ": " + joinParts(parts) : "") };
}

/** The plain-text form of a machine's line (tests, the cell's description). */
export function machineText(host: string, f: HostFrame, reading?: Reading | null, selfName?: string): string {
  return machineLine(host, f, reading, selfName).text;
}

/**
 * The dot and the machine lines over the local frame, the per-host map the kernel's frame carries, and each
 * machine's history reading when read (`readings[host]`; absent or null = not read yet). The dot is the frames':
 * worst state wins, so one machine in errors (waiting sessions, a pause, or failed attempts in its window) makes
 * the dot red whatever the others say; every reachable machine quiet makes it gray; otherwise the accent. A
 * machine whose tunnel is down or whose frame could not be read is named and has no say (its frame is old news).
 * The readings word the lines only. Per-host in, per-host out.
 */
export function mergeFrames(local: HostFrame, hosts: Record<string, HostFrame> | undefined,
                            readings?: Record<string, Reading | null | undefined>): Merged {
  const names = Object.keys(hosts || {}).sort();
  const rows: Array<[string, HostFrame]> = [["", local]];
  for (const h of names) rows.push([h, (hosts as Record<string, HostFrame>)[h]]);
  let worstRank = -1, worst = "", allQuiet = true;
  const machines: MachineLine[] = rows.map(([host, f]) => {
    const rd = readings ? readings[host] || null : null;
    const away = !!(f.stale || f.fault);
    const d: Dot = away ? "quiet" : frameDot(f);
    if (!away) {
      if (f.quiet !== true) allQuiet = false;   // gray only when EVERY reachable machine says quiet; a frame without the flag is not assumed so
      const r = d === "errors" ? 2 : frameRank(f);
      if (r > worstRank) { worstRank = r; worst = host; }
    }
    const line = machineLine(host, f, rd, local.host);
    return { host, name: line.name, dot: d, parts: line.parts, text: line.text, stale: away };
  });
  let dot: Dot;
  if (worstRank >= 2) dot = "errors";
  else if (allQuiet) dot = "quiet";
  else dot = "fine";
  if (worstRank < 2) worst = "";
  return { dot, worst, machines, n: rows.length };
}

/** One tier of a bucket's ledger as the kernel serves it: dense arrays, oldest first, the last bin holding asOf. */
export interface LedgerTier { binS: number; from: number; ok: number[]; rateLimited: number[]; serverErrors: number[]; noStatus: number[]; other: number[] }
export type LedgerTierName = "minute" | "fiveMin" | "hour";

/** The bits of a /api-health document the reading needs. */
export interface HistoryDoc {
  asOf?: number;
  bootAt?: number;
  overall?: { state?: string; worstBucket?: string | null };
  buckets?: Record<string, {
    state?: string; stateSince?: number; why?: string;
    windows?: Record<string, { requests?: number; ok?: number; noStatus?: number; gaveUp?: number; rateLimited?: number; serverErrors?: number; overloaded?: number; otherErrors?: number; complete?: boolean }>;
    series?: { binS: number; from: number; ok: number[]; rateLimited: number[]; serverErrors: number[]; noStatus: number[]; other: number[] };
    ledger?: Partial<Record<LedgerTierName, LedgerTier>>;
  }>;
  config?: { windows?: number[]; minRequests?: number };
}

/** The counts of one window, every bucket of one kernel added (within-host arithmetic). */
export interface Counts { ok: number; r429: number; r5xx: number; none: number; other: number; attempts: number; failures: number }

export interface Reading {
  level: "errors" | "fine" | "quiet";
  state: string;             // the machine's own word, for the record (never shown as a verdict)
  counts: Counts;            // over the window
  windowS: number;           // the window the counts cover: the ledger's 24 hours, else the document's longest window
  traffic: boolean;          // any attempt at all over the window
  requests: number;          // attempts with a status (for the record)
  errors: number;            // failures over the window
  headline: string;          // the counts as one plain line: "67 successful requests · 12 429s"
}

const SEV: Record<string, number> = { unknown: 0, healthy: 1, recovering: 2, degraded: 3, thrashing: 4 };
export const DAY_S = 86400;

function sum(a: number[] | undefined): number { let t = 0; for (const v of a || []) t += v || 0; return t; }

/** The counts a document holds over the popup's window: the ledger's five-minute tier (24 hours) when the kernel
 *  serves one, else the longest state-machine window. Buckets of one kernel are added (one clock). */
export function documentCounts(d: HistoryDoc | null | undefined): { counts: Counts; windowS: number } {
  const buckets = (d && d.buckets) || {};
  const led = documentLedger(d, "fiveMin");
  let ok = 0, r429 = 0, r5xx = 0, none = 0, other = 0, windowS: number;
  if (led) {
    ok = sum(led.ok); r429 = sum(led.rateLimited); r5xx = sum(led.serverErrors); none = sum(led.noStatus); other = sum(led.other);
    windowS = led.binS * led.ok.length;
  } else {
    const wins = (d && d.config && d.config.windows) || [60, 300, 900];
    windowS = Math.max.apply(null, wins);
    for (const k of Object.keys(buckets)) {
      const w = ((buckets[k].windows || {})[String(windowS)]) || {};
      ok += w.ok || 0; r429 += w.rateLimited || 0; r5xx += (w.serverErrors || 0) + (w.overloaded || 0); none += w.noStatus || 0; other += w.otherErrors || 0;
    }
  }
  const failures = r429 + r5xx + none + other;
  return { counts: { ok, r429, r5xx, none, other, attempts: ok + failures, failures }, windowS };
}

/**
 * The reading rule (T301 part C, reworded for T316): the user reads what HAPPENED, as counts. Traffic with no
 * failures: the successes counted (fine). Failures: each class counted, in its colour (errors). No traffic: quiet.
 * The state machine's word stays in the document and appears nowhere the user reads.
 */
export function readHistory(d: HistoryDoc | null | undefined): Reading {
  const buckets = (d && d.buckets) || {};
  let worst = "unknown";
  for (const k of Object.keys(buckets)) {
    const st = buckets[k].state || "unknown";
    if ((SEV[st] || 0) > (SEV[worst] || 0)) worst = st;
  }
  const state = (d && d.overall && d.overall.state) || worst;
  const { counts, windowS } = documentCounts(d);
  const traffic = counts.attempts > 0;
  const level: Reading["level"] = !traffic ? "quiet" : counts.failures > 0 ? "errors" : "fine";
  return { level, state, counts, windowS, traffic, requests: counts.ok + counts.r429 + counts.r5xx + counts.other, errors: counts.failures,
           headline: joinParts(countsParts(counts)) };
}

/** One machine's reading, kept under its name; a failed read is its own line; a read still in flight is pending. */
export interface HostReading { host: string; reading: Reading | null; error: string | null; pending: boolean }

/** The histories merged for the popup: per-host readings (the map the frame merge takes for its lines), the worst
 *  level, and the traffic verdicts. No document's counts are added to another's: each machine keeps its own
 *  sentence. A `{pending: true}` entry (the shell's placeholder while that machine's read is in flight) and an
 *  `{error}` entry are no reading. */
export function mergeHistories(byHost: Record<string, HistoryDoc | { error: string } | { pending: true } | null | undefined>): {
  level: "errors" | "fine" | "quiet" | "unread"; rows: HostReading[]; traffic: Record<string, boolean | null>;
  readings: Record<string, Reading | null>;
} {
  const names = Object.keys(byHost).sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a < b ? -1 : 1));
  const rows: HostReading[] = [];
  const traffic: Record<string, boolean | null> = {};
  const readings: Record<string, Reading | null> = {};
  let level: "errors" | "fine" | "quiet" | "unread" = "unread";
  const rank = { unread: -1, quiet: 0, fine: 1, errors: 2 };
  for (const h of names) {
    const d = byHost[h];
    if (!d) { rows.push({ host: h, reading: null, error: null, pending: false }); traffic[h] = null; readings[h] = null; continue; }
    if ((d as { pending?: boolean }).pending) { rows.push({ host: h, reading: null, error: null, pending: true }); traffic[h] = null; readings[h] = null; continue; }
    if ((d as { error?: string }).error) { rows.push({ host: h, reading: null, error: String((d as { error: string }).error), pending: false }); traffic[h] = null; readings[h] = null; continue; }
    const r = readHistory(d as HistoryDoc);
    rows.push({ host: h, reading: r, error: null, pending: false });
    traffic[h] = r.traffic; readings[h] = r;
    if (rank[r.level] > rank[level]) level = r.level;
  }
  return { level, rows, traffic, readings };
}

/** The per-minute series of every bucket in one document, summed BIN BY BIN (one kernel's buckets share its
 *  clock, so this is within-host arithmetic, which the federation rule allows); null when no bucket has one. */
export function documentSeries(d: HistoryDoc | null | undefined): { binS: number; from: number; ok: number[]; rateLimited: number[]; serverErrors: number[]; noStatus: number[] } | null {
  const buckets = (d && d.buckets) || {};
  let out: { binS: number; from: number; ok: number[]; rateLimited: number[]; serverErrors: number[]; noStatus: number[] } | null = null;
  for (const k of Object.keys(buckets)) {
    const s = buckets[k].series;
    if (!s || !s.ok) continue;
    if (!out) { out = { binS: s.binS, from: s.from, ok: s.ok.slice(), rateLimited: s.rateLimited.slice(), serverErrors: s.serverErrors.slice(), noStatus: s.noStatus.slice() }; continue; }
    for (let i = 0; i < out.ok.length && i < s.ok.length; i++) {
      out.ok[i] += s.ok[i]; out.rateLimited[i] += s.rateLimited[i]; out.serverErrors[i] += s.serverErrors[i]; out.noStatus[i] += s.noStatus[i];
    }
  }
  return out;
}

const LEDGER_KEYS: Array<keyof Omit<LedgerTier, "binS" | "from">> = ["ok", "rateLimited", "serverErrors", "noStatus", "other"];

/** One tier of every bucket's ledger in one document, summed bin by bin (within-host arithmetic); null when no
 *  bucket carries the tier (an older kernel's document). */
export function documentLedger(d: HistoryDoc | null | undefined, tier: LedgerTierName): LedgerTier | null {
  const buckets = (d && d.buckets) || {};
  let out: LedgerTier | null = null;
  for (const k of Object.keys(buckets)) {
    const t = buckets[k].ledger && buckets[k].ledger![tier];
    if (!t || !t.ok) continue;
    if (!out) { out = { binS: t.binS, from: t.from, ok: t.ok.slice(), rateLimited: (t.rateLimited || []).slice(), serverErrors: (t.serverErrors || []).slice(), noStatus: (t.noStatus || []).slice(), other: (t.other || []).slice() }; continue; }
    for (const key of LEDGER_KEYS) {
      const a = out[key], b = t[key] || [];
      for (let i = 0; i < a.length && i < b.length; i++) a[i] += b[i] || 0;
    }
  }
  return out;
}

/** A tier re-binned `k` bins per bar, oldest first, the last bar the newest bins (a partial first bar is dropped),
 *  so 288 five-minute bins draw as 96 quarter-hours. */
export function rebin(t: LedgerTier, k: number): LedgerTier {
  if (k <= 1) return t;
  const n = Math.floor(t.ok.length / k), skip = t.ok.length - n * k;
  const out: LedgerTier = { binS: t.binS * k, from: t.from + skip * t.binS, ok: [], rateLimited: [], serverErrors: [], noStatus: [], other: [] };
  for (const key of LEDGER_KEYS) {
    const src = t[key] || [], dst: number[] = [];
    for (let i = 0; i < n; i++) { let s = 0; for (let j = 0; j < k; j++) s += src[skip + i * k + j] || 0; dst.push(s); }
    out[key] = dst;
  }
  return out;
}

/** A stamp's age in words, for the popup's "as of" (T316): now, 1 minute ago, 3 hours ago, 2 days ago. */
export function agoWords(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 45) return "now";
  const m = Math.round(s / 60);
  if (m < 60) return m + (m === 1 ? " minute ago" : " minutes ago");
  const h = Math.round(m / 60);
  if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
  const d = Math.round(h / 24);
  return d + (d === 1 ? " day ago" : " days ago");
}

/** A window's name, once, at the top: "last 24 hours", "last hour", "last 7 days", "last 15 min". */
export function windowWords(seconds: number): string {
  if (seconds >= 6 * DAY_S) return "last " + Math.round(seconds / DAY_S) + " days";
  if (seconds >= DAY_S) return "last 24 hours";
  if (seconds >= 3600) return seconds === 3600 ? "last hour" : "last " + Math.round(seconds / 3600) + " hours";
  return "last " + Math.round(seconds / 60) + " min";
}
