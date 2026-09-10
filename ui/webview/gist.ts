// The gist rule every text-derived notice head shares, and the postal card's head built on it. A pure module
// (no DOM) so the rule is unit-testable: render.ts imports it, and the one-line heads it builds, a peer's mail,
// a background report, a romp notice, a harness note, all clip the same way.

// The first meaningful line of a message, clipped for a head.
export function gistOf(text: string, max = 90): string {
  const lines = text.split("\n").map((l) => l.trim());
  const first = lines.find((l) => l && !l.startsWith(">")) || lines.find((l) => l) || text.trim();
  return first.length > max ? first.slice(0, max - 2).replace(/\s+\S*$/, "") + "…" : first;
}

export const collapseWs = (s: string): string => s.replace(/\s+/g, " ").trim();

// A postal card's head: the gist the head shows, and the message the fold holds beneath it, as sent (null when
// the gist already carries the whole message, so nothing is hidden and there is nothing to open).
export interface PostalHead {
  gist: string;
  body: string | null;
}

export function postalHead(ev: { body?: string; summary?: string }): PostalHead {
  const raw = ev.body || "";
  const full = raw.trim();
  const cap = ev.summary && ev.summary.trim();
  const gist = cap || gistOf(full);   // CLIPPED: an unclipped first line equalled a one-paragraph message and left it no fold (T294)
  // the fold holds the message AS SENT, not the trimmed copy the comparison uses: a leading indent is markdown (four
  // spaces open a code block) and the trim would have flattened it into prose
  const body = !!full && collapseWs(full) !== collapseWs(gist) ? raw : null;
  return { gist, body };
}
