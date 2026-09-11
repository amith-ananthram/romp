// Pure label logic for the chat rail's HH:MM time-markers, split out of render.ts
// so it can be unit-tested without a DOM. renderEvent() wraps the returned text in
// a `.time-marker` div (with the `day` class when `day` is true).

export const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface MarkerLabel {
  text: string; // "" → suppressed (same minute as the previous timed turn). On a day marker this
                // is still the combined "Yesterday · 11:03", but the rail renders only `hm` there
                // and hands `date` to the divider, so here it mostly answers "is there a stamp?"
  day: boolean; // true → first turn of a past day; opens a day divider carrying the date
  hm: string;   // the bare "HH:MM", ALWAYS — a suppressed turn still carries its time so the
                // sticky rail stamp can name the time at the top of the view.
  date: string; // the date word ("Yesterday" / "Mon" / "Jun 3") on a day marker, else "".
                // Split out from `text` because the renderer puts it on a full-width DAY DIVIDER
                // rather than in the rail (dayDividerFor in render.ts): the gutter is 59px wide
                // and "Yesterday" measures 52.6px, so in the rail its leading "Y" was clipped.
}

// HH:MM for a turn, given the previous TIMED turn's epoch (or null) and "now".
// Rules, in order:
//   1. First turn of a past (non-today) day → day: true, with the date word split into `date`.
//      The rail shows just "11:03"; the date opens a divider above the turn (see dayDividerFor).
//   2. Same minute AND same day as the previous timed turn → suppressed (""), so a run of
//      same-minute events (11:03, 11:03, 11:03, 11:04) shows the stamp only when it changes.
//   3. Otherwise → "HH:MM".
// `hm` is the bare "HH:MM" regardless of the rule, so a suppressed turn still carries the
// time the sticky rail stamp reads when that turn is the one at the top of the view.
export function markerLabel(epoch: number, prevEpoch: number | null, nowMs: number): MarkerLabel {
  const d = new Date(epoch * 1000);
  const dayKey = (x: Date) => `${x.getFullYear()}/${x.getMonth()}/${x.getDate()}`;
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const now = new Date(nowMs);
  const prev = prevEpoch == null ? null : new Date(prevEpoch * 1000);
  const dayChanged = prev == null || dayKey(d) !== dayKey(prev);
  const isToday = dayKey(d) === dayKey(now);
  if (dayChanged && !isToday) {
    const sod = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((sod(now) - sod(d)) / 86400000);
    const date = days === 1 ? "Yesterday" : days < 7 ? WEEKDAY[d.getDay()] : `${MONTH[d.getMonth()]} ${d.getDate()}`;
    return { text: `${date} · ${time}`, day: true, hm: time, date };
  }
  const sameMinute = prev != null
    && dayKey(d) === dayKey(prev)
    && d.getHours() === prev.getHours()
    && d.getMinutes() === prev.getMinutes();
  if (sameMinute) return { text: "", day: false, hm: time, date: "" };
  return { text: time, day: false, hm: time, date: "" };
}

/** The date a day divider shows above a row, or "" for none: the boundary rule markerLabel applies (the first row of a
 *  PAST day), with one more condition (T339, the user 2026-09-11): the crossing must be FORWARD. A row stamped earlier
 *  than the row before it (a notice that kept the moment it was queued and landed at delivery, a clock skew) is not a
 *  day opening: reading the step back as a boundary drew "Yesterday" inside today, above a row whose neighbours were all
 *  today's. Such a row keeps its own time and draws no divider. */
export function dayOpens(epoch: number, prevEpoch: number | null, nowMs: number): string {
  if (prevEpoch != null && epoch < prevEpoch) return "";
  const { day, date } = markerLabel(epoch, prevEpoch, nowMs);
  return day && date ? date : "";
}

/** The day walk's state (T339 review): the reference a divider is decided against is a HIGH-WATER MARK, the latest
 *  epoch the walk has passed, never the row just before. A row stamped earlier than the rows around it (a live echo the
 *  kernel merges into the last turn at its send time) opens no day (dayOpens) and must not become the reference either:
 *  the next in-sequence row would cross "forward" out of the stale day and open a SECOND divider for a day already open,
 *  whenever that day is not today (today never opens, which is the one case a previous-row rule got right). `open` asks
 *  whether a row opens a day against the mark; `pass` moves the mark over a row (or a unit's exit epoch) and never
 *  rewinds. The rail's own HH:MM chain is separate (it reads the raw previous row), so a row after a stale one still
 *  shows its time. */
export class DayWalk {
  mark: number | null = null;
  open(epoch: number, nowMs: number): string { return dayOpens(epoch, this.mark, nowMs); }
  pass(epoch: number | null): void { if (epoch != null && (this.mark == null || epoch > this.mark)) this.mark = epoch; }
}

// (A chooseStamps() spacing pass used to live here: it re-revealed a suppressed same-minute stamp every
// ~6 rows so the gutter never went long without a time. The sticky rail stamp now guarantees a time at the
// top of the view at all times, which made those repeats pure noise — so the pass is gone and a stamp means
// exactly one thing: the time CHANGED here (the user 2026-07-23). See paintRailSticky in render.ts.)

// The DAY CONTEXT for the stamp at the top of the view (the user 2026-08-17): a human-readable
// relative day ("Yesterday", "3 days ago", "Last week", "2 weeks ago") painted ABOVE the top
// visible rail stamp whenever that stamp is not from today — so mid-scroll through history you
// always know which day you are reading, even with the day divider off-screen. "" for today
// (no label). Bounded vocabulary, oldest form the divider's own "Mmm D" (+ year when different).
export function dayContext(epoch: number, nowMs: number): string {
  const d = new Date(epoch * 1000);
  const now = new Date(nowMs);
  const sod = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((sod(now) - sod(d)) / 86400000);
  if (days <= 0) return "";
  if (days === 1) return "Yesterday";
  if (days < 7) return days + " days ago";
  if (days < 14) return "Last week";
  if (days < 28) return Math.floor(days / 7) + " weeks ago";
  const md = MONTH[d.getMonth()] + " " + d.getDate();
  return d.getFullYear() === now.getFullYear() ? md : md + " " + d.getFullYear();
}
