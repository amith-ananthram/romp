// ONE duration formatter for every notice, footer and countdown (the 2026-09-08 notice audit counted six:
// the worked footer's "1h 03m", the work timer's "1h 3m", the strip's "1d 2h 5m" with no seconds, the
// retry line's "7s…", the API card's "3m · 2 tries so far" and its paused twin "(in 2m 14s)"). Seconds in;
// "45s" / "2m 14s" / "1h 03m" / "2d 05h" out — the two-digit minor unit keeps a ticking value from
// jittering its row, and the units say "a span" where the rail's HH:MM says "a clock" (time stays on the
// left rail — the user's 2026-09-08 decision 1). Pure and DOM-free so node --test executes it; render.ts
// and strip.ts both import it.
export function durLabel(secs: number): string {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${String(h % 24).padStart(2, "0")}h`;
}
