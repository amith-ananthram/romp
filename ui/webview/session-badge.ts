// The statusline's session badge (the user 2026-09-09): a small chip before the state chip ("Awaiting",
// "Ready", "Working") carrying the session's NAME on its identity colour — the colour its tab label and
// timeline lane wear — with the name in black. The pane already tints its window border with that colour
// and the composer's resting placeholder names the session; the badge puts the name where the eye reads the
// session's state, so a glance at any chat column says which session it is and what it is doing. Pure so
// node executes it: a session with no name yet gets no badge (the statusline's opening line covers that
// window); a session with no colour yet gets the badge on the theme's neutral fill (CSS), never on nothing.

export interface BadgeSpec { text: string; bg: string | null; }

export function badgeSpec(s: { name?: string | null; color?: { bg?: string | null } | null } | null | undefined): BadgeSpec | null {
  const name = (s && s.name ? String(s.name) : "").trim();
  if (!name) return null;
  const bg = s && s.color && s.color.bg ? String(s.color.bg) : null;
  return { text: name, bg };
}
