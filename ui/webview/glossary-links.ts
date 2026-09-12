// The glossary's client half (T351 stage 2, the user 2026-09-11): the team's coinages, linked where they are written.
// The kernel ships one INDEX per session (glossary-frame: the author's group file, parsed and bounded by bytes); this
// module builds the matcher from it and links every whole-word occurrence of a term or its `also` forms (plurals
// too) in the prose of a message, skipping code, links, headings, math, and the popover cards. The term card is
// filled from the same index with no fetch (termContent, the file preview popover's contract). Pure: no DOM writes
// beyond what the caller hands in through `make`. Two limits, by design: a term split across text nodes by an inline
// element (`fo*ld*`, a link inside the words) is not matched, since each text node is scanned alone; and a term inside
// a path-shaped or host-shaped token (docs/widget/x.md, example.com/y) is never linked, so a path a session named but
// the kernel could not verify is not split by an underline (the review's medium).
import type { PreviewContent } from "./file-preview";

export type GlossaryLink = "all" | "first" | "off";
export interface GlossaryEntry {
  term: string; slug: string; definition: string; plainWords: string; also: string[]; scope: string;
  status: string; registered: { date: string; by: string }; link: GlossaryLink;
}
export interface GlossaryIndex { type?: string; id?: string; group: string; path: string; mtime?: string; skip: string[]; terms: GlossaryEntry[];
                                 truncated?: number; cutHeadings?: number; cutBytes?: number }   // truncated = the two cuts' sum (an older kernel carries it alone)

/** A form's plurals by the everyday rule: tessel → tessels; quill → quills; spar → spars; a trailing s, x, z, ch or sh
 *  → es (quillbox → quillboxes); a consonant + y → ies (sparfly → sparflies). A declared `also` alias always wins over
 *  a guessed plural. */
export function pluralForms(f: string): string[] {
  const out: string[] = [];
  if (/(s|x|z|ch|sh)$/i.test(f)) out.push(f + "es");
  else if (/[^aeiou]y$/i.test(f)) out.push(f.slice(0, -1) + "ies");
  else out.push(f + "s");
  return out;
}

/** The forms one entry links under: the term, its `also` aliases (spaces allowed: "tessel head" is one form) and their
 *  plurals, lowercased and deduped, minus the skip list (the Not-coinages words win over any heading or alias, in
 *  every file: lab_manager 2026-09-11). An entry with `link: off` links nothing. */
export function linkForms(e: GlossaryEntry, skip: Set<string>): string[] {
  if (e.link === "off") return [];
  // a form shorter than two characters links nothing, nor is it pluralised (the review: an empty heading's plural
  // was the letter s; the letter's own plural would be "ses")
  const base = [e.term, ...(e.also || [])].map((s) => s.trim().toLowerCase()).filter((b) => b.length >= 2);
  const all = new Set<string>();
  for (const b of base) { all.add(b); for (const p of pluralForms(b)) all.add(p); }
  return Array.from(all).filter((f) => f.length >= 2 && !skip.has(f));
}

/** The skip list by WORD: a listed word, its plurals and any alias equal to one of them never link (the review's low:
 *  "head" listed left "heads" linking). */
export function skipForms(skip: readonly string[]): Set<string> {
  const out = new Set<string>();
  for (const s of skip) { const w = s.trim().toLowerCase(); if (!w) continue; out.add(w); for (const p of pluralForms(w)) out.add(p); }
  return out;
}

export interface TermMatcher { re: RegExp; byForm: Map<string, GlossaryEntry>; index: GlossaryIndex }

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** One case-insensitive, whole-word (Unicode letters and digits bound a word) regex over every form, LONGEST first so
 *  "tessel head" is one term and never "tessel" plus a word; null when the index links nothing. */
export function buildMatcher(ix: GlossaryIndex): TermMatcher | null {
  const skip = skipForms(ix.skip || []);
  const byForm = new Map<string, GlossaryEntry>();
  // an exact TERM claims its form before any other entry's alias does ("tessel head" is its own entry even though
  // "tessel" lists it as an alias; the route reads the same way), so the terms pass first and the aliases after
  const terms = (ix.terms || []).filter((e) => e.link !== "off" && e.term.trim().length >= 2);
  for (const e of terms) {
    const t = e.term.trim().toLowerCase();
    for (const f of [t, ...pluralForms(t)]) if (f.length >= 2 && !skip.has(f) && !byForm.has(f)) byForm.set(f, e);
  }
  for (const e of terms) for (const f of linkForms(e, skip)) if (!byForm.has(f)) byForm.set(f, e);
  if (!byForm.size) return null;
  const forms = Array.from(byForm.keys()).sort((a, b) => b.length - a.length || a.localeCompare(b));
  // a word is letters, digits, the underscore and the HYPHEN (the team coins hyphenated terms, and "tessel-ish" is not
  // a use of "tessel"), on both sides
  const re = new RegExp("(?<![\\p{L}\\p{N}_-])(" + forms.map(escapeRe).join("|") + ")(?![\\p{L}\\p{N}_-])", "giu");
  return { re, byForm, index: ix };
}

export interface TermSpan { start: number; end: number; entry: GlossaryEntry }

/** The entries already linked under a root (their spans' `data-term` slugs), as the seen set a fresh walk starts
 *  from: a body linked on its own before it was placed inside a message root keeps its `first` pick, and the outer
 *  walk does not link that term a second time in the prose around it. */
export function seenFromLinked(slugs: Iterable<string>, m: TermMatcher): Set<GlossaryEntry> {
  const seen = new Set<GlossaryEntry>();
  const bySlug = new Map((m.index.terms || []).map((e) => [e.slug, e] as const));
  for (const s of slugs) { const e = bySlug.get(s); if (e) seen.add(e); }
  return seen;
}

/** The [start, end) spans of `text` a term may never be linked inside: a slash-joined path shape (with or without a
 *  leading ~, ., or scheme) and a dotted host (example.com, api.example.org/x). The same shape family the kernel's
 *  path linker reads as a candidate; a candidate it could not verify stays plain text here, unsplit. */
export function noLinkZones(text: string): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  // the token may follow a bracket or a quote (a path in parentheses is the common case); a bare host is one dot
  const re = /(?<![\w.\/~@%+-])(?:[A-Za-z][A-Za-z0-9+.-]*:\/\/\S+|[~.]?[\w.\-@%+]*\/[\w.\-@%+~\/#?=&]+|[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?:\/\S*)?)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) out.push([m.index, m.index + m[0].length]);
  return out;
}

/** The spans of `text` to link, honouring each entry's link mode: `all` every occurrence, `first` the first occurrence
 *  per message (`seen` is the message's set, shared across its text nodes), `off` never (its forms never entered the
 *  matcher). Pure over strings: the DOM walker below and the tests share it. */
export function scanTerms(text: string, m: TermMatcher, seen: Set<GlossaryEntry>): TermSpan[] {
  const out: TermSpan[] = [];
  const zones = noLinkZones(text);
  m.re.lastIndex = 0;
  let hit: RegExpExecArray | null;
  while ((hit = m.re.exec(text))) {
    const e = m.byForm.get(hit[1].toLowerCase());
    if (!e) continue;
    const a = hit.index, b = hit.index + hit[0].length;
    if (zones.some(([za, zb]) => a < zb && b > za)) continue;   // inside a path-shaped or host-shaped token: never
    if (e.link === "first") { if (seen.has(e)) continue; seen.add(e); }
    out.push({ start: hit.index, end: hit.index + hit[0].length, entry: e });
  }
  return out;
}

/** Where a term is never linked: code and pre, any link (a path link included), headings, math, SVG, an existing term
 *  link, and the popover cards (a previewed glossary file's own text would otherwise link itself). */
export const TERM_SKIP_SELECTOR = "code, pre, a, .file-uri-link, h1, h2, h3, h4, h5, h6, .katex, svg, .term-link, .cmt-pop, .file-preview-pop";

/** Link the terms in `root`'s text nodes: each span becomes the node `make` returns (the caller dresses it). One
 *  `seen` set per call = per message, the unit of the `first` mode. Returns the count linked. */
export function linkifyTerms(root: ParentNode, m: TermMatcher, make: (e: GlossaryEntry, text: string) => Node, skipSel: string = TERM_SKIP_SELECTOR): number {
  const doc = (root as Node).ownerDocument || document;
  const walker = doc.createTreeWalker(root as Node, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let n: Node | null;
  while ((n = walker.nextNode())) {
    const t = n as Text;
    const p = t.parentElement;
    if (!p || (p.closest && p.closest(skipSel))) continue;
    if (t.data.length >= 2) nodes.push(t);
  }
  const el = root as Element;
  const linked = el.querySelectorAll ? Array.from(el.querySelectorAll("span.term-link[data-term]")).map((s) => (s as HTMLElement).dataset.term || "") : [];
  const seen = seenFromLinked(linked, m);
  let count = 0;
  for (const t of nodes) {
    const spans = scanTerms(t.data, m, seen);
    if (!spans.length) continue;
    const frag = doc.createDocumentFragment();
    let last = 0;
    for (const s of spans) {
      if (s.start > last) frag.appendChild(doc.createTextNode(t.data.slice(last, s.start)));
      frag.appendChild(make(s.entry, t.data.slice(s.start, s.end)));
      last = s.end;
    }
    if (last < t.data.length) frag.appendChild(doc.createTextNode(t.data.slice(last)));
    t.parentNode?.replaceChild(frag, t);
    count += spans.length;
  }
  return count;
}

/** The term card, in the file preview popover's contract: the term, its status and registration as the subtitle,
 *  the definition and the plain words as the body, "Open glossary" landing the viewer on the term's heading. A
 *  retired term says so first. */
export function termContent(e: GlossaryEntry, ix: GlossaryIndex): PreviewContent {
  const reg = e.registered && (e.registered.date || e.registered.by)
    ? " · registered " + [e.registered.date, e.registered.by ? "by " + e.registered.by : ""].filter(Boolean).join(" ") : "";
  const retired = e.status === "retired";
  const body = (retired ? "*Retired: say the plain phrase.*\n\n" : "") + (e.definition || "")
    + (e.plainWords ? "\n\n*plain words:* " + e.plainWords : "")
    + (e.scope ? "\n\n*scope:* " + e.scope : "");
  // the index's cuts, each named for what cut it (the review's low: a heading-ceiling cut was blamed on the byte cap)
  const notes: string[] = [];
  if (ix.cutBytes) notes.push(ix.cutBytes + " entries beyond the index's byte cap are not linked");
  if (ix.cutHeadings) notes.push(ix.cutHeadings + " sections past the heading ceiling are not linked");
  if (!notes.length && ix.truncated) notes.push(ix.truncated + " entries are not linked (the index was cut)");   // an older kernel: the sum alone
  return { kind: "term", title: e.term, subtitle: (e.status || "unconfirmed") + reg + " · " + ix.group,
           body: { markdown: body }, note: notes.length ? notes.join("; ") : undefined,
           open: { label: "Open glossary", path: ix.path, frag: e.slug } };
}
