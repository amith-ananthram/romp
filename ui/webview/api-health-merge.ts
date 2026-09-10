// The API-health signal on the shell's rail, across EVERY connected kernel (T301, the user 2026-09-10): the
// merge of the per-host frames into the one dot, the plain-words reading of one kernel's /api-health document,
// and the merge of several such documents. Pure (no DOM), so the rules are unit-tested; the shell's inline
// script (kernel.py _LANDING_APIH_JS) reaches them through window.__rompApiHealthMerge (api-health-global.ts).
//
// The federation rule holds throughout: per-host MAPS in, per-host lines out; nothing here adds a count from
// one kernel to a count from another or compares two kernels' clocks. "Worst state wins" is a comparison of
// STATE WORDS, which are the same vocabulary on every kernel.

/** One kernel's apiHealth shell frame, local half (the fields the merge reads). */
export interface HostFrame {
  state: "ok" | "degraded" | "paused" | string;
  cls?: string;          // "429" | "529" | "offline" | "errors" | ""
  text?: string;         // the kernel's own headline
  waiting?: number;
  reason?: string;       // a pause's reason: limit | spend | manual
  since?: number;
  quiet?: boolean;       // that kernel saw no API event in its longest window (T301): gray before any history is read
  stale?: boolean;       // the tunnel to that host is not up: the frame is the last one heard
}

/** The dot's three states: the accent when everything is fine, red when errors are being met, gray when quiet. */
export type Dot = "fine" | "errors" | "quiet";

export interface MachineLine {
  host: string;
  dot: Dot;
  text: string;          // one plain line for the popup: "TESTHOST: rate limited · 2 waiting"
  stale: boolean;
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

/** A frame's dot with its history reading in hand: errors when the kernel's frame says so OR the reading found
 *  errors in the window; quiet when the reading found no traffic; else fine. A frame alone (no reading yet) is
 *  errors or fine on its own words. */
export function frameDot(f: HostFrame, reading?: Reading | null): Dot {
  if (f.state === "paused" || f.state === "degraded") return "errors";
  if (reading && reading.level === "errors") return "errors";
  if (reading ? !reading.traffic : f.quiet === true) return "quiet";
  return "fine";
}

const CLS_WORDS: Record<string, string> = {
  "429": "rate limited", "529": "overloaded", "offline": "offline", "errors": "errors",
};

/** One machine's line for the popup, in plain words: the kernel's own headline when its frame carries one (a pause,
 *  sessions waiting on the API), else what the history reading says happened. */
export function machineText(host: string, f: HostFrame, reading?: Reading | null): string {
  const name = host || "this machine";
  if (f.state === "paused") {
    const why = f.reason === "limit" ? "paused until the usage limit resets" : f.reason === "spend" ? "paused at the spend limit" : "paused by you";
    return name + ": " + why + (f.waiting ? " · " + f.waiting + " waiting" : "");
  }
  if (f.state === "degraded") {
    return name + ": " + (CLS_WORDS[f.cls || ""] || "errors") + (f.waiting ? " · " + f.waiting + " waiting" : "");
  }
  if (reading && reading.level === "errors") return name + ": " + reading.headline.replace(/\.$/, "");
  if (reading ? !reading.traffic : f.quiet === true) return name + ": no API traffic";
  if (reading) return name + ": fine · " + plural(reading.requests, "request") + " in the last 15 min, all succeeded";
  return name + ": fine";
}

/**
 * The dot and the machine lines over the local frame, the per-host map the kernel's frame carries, and each
 * machine's history reading when read (`readings[host]`; absent or null = not read yet: the frame's word stands
 * alone). Worst state wins: one machine in errors (its frame, or its window) makes the dot red whatever the others
 * say; every machine known quiet (its reading, or its frame's flag) makes it gray; otherwise the accent. Per-host
 * in, per-host out.
 */
export function mergeFrames(local: HostFrame, hosts: Record<string, HostFrame> | undefined,
                            readings?: Record<string, Reading | null | undefined>): Merged {
  const names = Object.keys(hosts || {}).sort();
  const rows: Array<[string, HostFrame]> = [["", local]];
  for (const h of names) rows.push([h, (hosts as Record<string, HostFrame>)[h]]);
  let worstRank = -1, worst = "", allQuiet = true;
  const machines: MachineLine[] = rows.map(([host, f]) => {
    const rd = readings ? readings[host] || null : null;
    const d = frameDot(f, rd);
    const traffic = rd ? rd.traffic : (f.quiet === true ? false : null);
    if (traffic !== false) allQuiet = false;   // gray only when EVERY machine is known quiet; an unread peer is not assumed so
    const r = d === "errors" ? 2 : frameRank(f);
    if (r > worstRank) { worstRank = r; worst = host; }
    return { host, dot: d, text: machineText(host, f, rd), stale: !!f.stale };
  });
  let dot: Dot;
  if (worstRank >= 2) dot = "errors";
  else if (allQuiet) dot = "quiet";
  else dot = "fine";
  if (worstRank < 2) worst = "";
  return { dot, worst, machines, n: rows.length };
}

/** The bits of a /api-health document the reading needs. */
export interface HistoryDoc {
  asOf?: number;
  bootAt?: number;
  overall?: { state?: string; worstBucket?: string | null };
  buckets?: Record<string, {
    state?: string; stateSince?: number; why?: string;
    windows?: Record<string, { requests?: number; ok?: number; noStatus?: number; gaveUp?: number; rateLimited?: number; serverErrors?: number; overloaded?: number; otherErrors?: number; complete?: boolean }>;
    series?: { binS: number; from: number; ok: number[]; rateLimited: number[]; serverErrors: number[]; noStatus: number[]; other: number[] };
  }>;
  config?: { windows?: number[]; minRequests?: number };
}

export interface Reading {
  level: "errors" | "fine" | "quiet";
  state: string;             // the machine's own word, for the record (never the headline when it is "unknown")
  headline: string;          // what the user reads first
  sub: string | null;        // the trend caveat, when the machine has too little to call one
  requests: number;          // attempts with a status over the longest window, every bucket
  errors: number;            // failed attempts over it (429 + 5xx + other + no status)
  traffic: boolean;          // any attempt at all over the longest window
  windowS: number;
}

const SEV: Record<string, number> = { unknown: 0, healthy: 1, recovering: 2, degraded: 3, thrashing: 4 };

function minutes(s: number): string { return s % 60 === 0 ? (s / 60) + " min" : s + " s"; }
function plural(n: number, w: string): string { return n + " " + w + (n === 1 ? "" : "s"); }

/**
 * The reading rule (part C): the machine's `unknown` is a documented signal and stays in the document, but the
 * user reads what HAPPENED. Traffic and no errors: "N requests in the last 15 min, all succeeded" (fine; when the
 * machine still says unknown, the sub-line says too few to call a trend). Errors: the failures counted, in the
 * machine's word when it has one (thrashing / degraded / recovering), else plainly. No traffic: quiet. The word
 * "unknown" is never the headline.
 */
export function readHistory(d: HistoryDoc | null | undefined): Reading {
  const wins = (d && d.config && d.config.windows) || [60, 300, 900];
  const slow = Math.max.apply(null, wins);
  const buckets = (d && d.buckets) || {};
  let requests = 0, ok = 0, noStatus = 0, gaveUp = 0, r429 = 0, r5xx = 0, other = 0;
  let worst = "unknown";
  for (const k of Object.keys(buckets)) {
    const b = buckets[k];
    const w = (b.windows || {})[String(slow)] || {};
    requests += w.requests || 0; ok += w.ok || 0; noStatus += w.noStatus || 0; gaveUp += w.gaveUp || 0;
    r429 += w.rateLimited || 0; r5xx += (w.serverErrors || 0) + (w.overloaded || 0); other += w.otherErrors || 0;
    if ((SEV[b.state || "unknown"] || 0) > (SEV[worst] || 0)) worst = b.state || worst;
  }
  const errors = r429 + r5xx + other + noStatus;
  const attempts = requests + noStatus;
  const traffic = attempts > 0;
  const state = (d && d.overall && d.overall.state) || worst;
  const win = minutes(slow);
  if (!traffic) {
    return { level: "quiet", state, headline: "No API traffic in the last " + win + ".", sub: null, requests, errors, traffic, windowS: slow };
  }
  if (errors === 0) {
    const sub = state === "unknown" || SEV[state] === undefined ? "Too few requests to call a trend yet." : null;
    return { level: "fine", state, headline: plural(requests, "request") + " in the last " + win + ", all succeeded.", sub, requests, errors, traffic, windowS: slow };
  }
  const parts: string[] = [];
  if (r429) parts.push(plural(r429, "rate-limited attempt"));
  if (r5xx) parts.push(plural(r5xx, "server error"));
  if (noStatus) parts.push(plural(noStatus, "attempt") + " with no connection");
  if (other) parts.push(plural(other, "other error"));
  const word = state === "thrashing" ? "Rate-limit storm" : state === "degraded" ? "The API is failing" : state === "recovering" ? "Recovering" : "Errors";
  const head = word + ": " + parts.join(", ") + " among " + plural(attempts, "attempt") + " in the last " + win + (gaveUp ? "; " + plural(gaveUp, "turn") + " gave up" : "") + ".";
  return { level: state === "recovering" && errors === 0 ? "fine" : "errors", state, headline: head, sub: null, requests, errors, traffic, windowS: slow };
}

/** One machine's reading, kept under its name; a failed read is its own line. */
export interface HostReading { host: string; reading: Reading | null; error: string | null }

/** The histories merged for the popup: per-host readings (the map the frame merge takes), the worst level, and
 *  the traffic verdicts. No document's counts are added to another's: each machine keeps its own sentence. */
export function mergeHistories(byHost: Record<string, HistoryDoc | { error: string } | null | undefined>): {
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
    if (!d) { rows.push({ host: h, reading: null, error: null }); traffic[h] = null; readings[h] = null; continue; }
    if ((d as { error?: string }).error) { rows.push({ host: h, reading: null, error: String((d as { error: string }).error) }); traffic[h] = null; readings[h] = null; continue; }
    const r = readHistory(d as HistoryDoc);
    rows.push({ host: h, reading: r, error: null });
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
