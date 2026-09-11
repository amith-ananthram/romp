// The composer's resting placeholder names the session (the user 2026-09-09): "Message this session…" reads
// "Message <name>…", the name bold and in the session's identity colour — the colour its tab label and its
// timeline lane wear — so the box says WHO you are about to message, which matters most with the chat split
// into columns, where every column is a different session behind a same-looking box. A native placeholder is
// plain text and cannot carry a bold, coloured word, so render.ts paints the styled form as an overlay over
// the empty box (syncComposerPh) and keeps the native placeholder beneath it, transparent, for assistive tech.
// The split is pure so node executes it: the resting forms (full hint, short hint, the phone's bare prompt)
// take the name; every other placeholder (the closed-session notice, a picker's "add your own answer…") shows
// as it is, and a session with no name yet keeps the plain resting text.

export const RESTING_PREFIX = "Message this session";

export type PhParts =
  | { kind: "named"; before: string; name: string; after: string }
  | { kind: "plain"; text: string };

/** How the overlay renders `placeholder` for a session called `name`: the resting form with the name in place
 *  of "this session" (kept: the leading "Message ", the trailing "…" and hint), else the text as it is. */
export function phParts(placeholder: string, name: string | null | undefined): PhParts {
  const nm = (name || "").trim();
  if (nm && placeholder.startsWith(RESTING_PREFIX)) {
    return { kind: "named", before: "Message ", name: nm, after: placeholder.slice(RESTING_PREFIX.length) };
  }
  return { kind: "plain", text: placeholder };
}
