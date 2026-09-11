# `/clear` is an episode boundary, not a deletion

*History (2026-09-11): the terminal (tmux) backend this plan describes a lane shape for was removed from romp on that date (issue #1398; the migration note is in `docs/reference.md`). The tmux passages below describe the state at the time of writing and are kept as history.*

Direction picked by the user (2026-07-26): when a session's conversation is
cleared, keep the session — its name, tab slot, mailbox, worktree naming, and
timeline lane all persist — and treat the cleared conversation as a **closed
episode**: settled on the board, visible as a seam on the timeline, and (future
work) browsable read-only. Explicitly rejected: minting a literal new session
with a duplicate name (breaks postal addressing, session-order, and
worktree-per-session conventions).

## The problem (traced 2026-07-26, before the fix)

Nothing in romp knew a `/clear` happened. The transcript layer is fully
fork-aware — discover re-points the sid at the new `lastSid` file, the parse
drops the pre-clear branch (`kept_uuids`'s "clear" classification) — but the
goal layer was not. A session's open cards outlived the only conversation that
was their evidence, and four machines carried them into the fresh one:

- the **closer**'s history-nominated candidates (`_subtree_done_candidates`,
  `_starved_candidates`) re-judged pre-clear cards against unrelated new turns
  with an empty `<goal-history>` block;
- the **unblocker** re-examined every pre-clear blocked card on each new ended
  turn (an LLM call per turn, false-lift risk);
- the **auto-nudge** quoted pre-clear goals at an agent with no memory of them,
  then recorded a manufactured "nudge failed" block when its reply couldn't
  resolve them — escalating dead cards into Needs-you;
- the **awaiting backstop** pinged 6h later about background work from a
  conversation that no longer exists (`_lift_spent_awaiting` can never pair a
  launch it can't see, so the stamp never lifted).

Two quieter defects: the distiller/briefer emitted empty summaries with a
misleading "heals itself" warning for boundary-orphaned cards, and a
byte-identical prompt retyped after a `/clear` was silently deduped against its
dead twin's placement (`_placed_key`'s t-invariant fuzzy match — the recorded
key is orphaned post-clear, which is exactly the state the fuzzy path treats as
benign drift), so the new ask never got a card.

## What shipped (this plan's first commit)

**Detection — exact and event-based.** A `/clear` fork's transcript head has no
parent link; a resume-style fork chains `parentUuid` (or the compaction
stitch's `logicalParentUuid`) into the prior file. So "the session's current
transcript starts at a null-rooted head we have not recorded" is precisely the
boundary event — no heuristics, no time windows. `jd.transcript_head(path)`
reads (and caches — a transcript head is immutable) the first uuid-bearing
record; the kernel's `_episode_boundary_tick` compares it against the last row
of the new append-only **episodes log** (`state/episodes/<sid>.jsonl`, rows
`{head, fsid, t}`, mtime-memoized reads). First observation seeds the log
without settling, so deploying this never mass-clears an existing fleet.

**The boundary settle.** On a boundary, still-open tops (not cleared, not
completed) are settled exactly like the mute path: rows in `cleared.jsonl`
(one shared batch `t`, so a single Undo restores the whole batch) plus the
durable node flag via `_mark_nodes_cleared`, now recording a **romp-authored**
clear with the honest why — "dropped when the conversation was cleared" — so
the Fleet ledger reads dismissal, not completion. Deliberately absent: the
clear-wrap notify (the agent that knew this work is gone; messaging the fresh
one is exactly the confusion this exists to stop) and the delegation cascade
(a handed-off piece lives on in the peer's session). Completed cards stay —
the Completed column is history and needs no live evidence; rollup rolls the
clear down to open sub-steps as usual. All four carry-forward machines go
quiet automatically because every one of them already skips cleared nodes.
The tick runs in the producer thread before the pre-pass snapshot and the
judge tiers, so the same pass's planner/closer/nudge already see the settled
store.

**Placement scoping.** `_placed_key`'s fuzzy (t-dropped) match now requires
the recorded key to belong to the **current episode** (`jd.episode_floor`,
keyed off the seg id's embedded t). Exact hits are untouched on purpose:
pre-clear segment ids can only re-enter a parse as-written (the broken-chain
re-admission edge case), and then the exact match is exactly the anti-re-mint
guard that keeps archived nodes from flooding back.

**Timeline seam.** The kernel ships `clears: [{t}]` per lane (episodes rows
after the first); `ui/romp-timeline-view.js` draws a film-splice cut through
the lane at each boundary — quiet by default, "conversation cleared · a fresh
one starts here" on hover. The lane stays continuous: same session, same slot.

**The episodes log doubles as the missing attribution record.** The SDK
registry's `lastSid` is overwritten on every fork, so before this log there
was no durable map from a session to its past episodes' transcript files (SDK
transcripts carry no custom-title either). Every future episode surface hangs
off this file.

## What shipped (second commit): the settle is visible, and consented where possible

The first commit's settle was silent — one stderr line; the cards left the
feed, the Fleet ledger, and (via compaction) the live store with nothing shown
anywhere (the user 2026-07-27: "cards must not disappear without you seeing
anything"). Now:

- **The settle record rides the episodes log.** The settle path appends a
  `{settleFor: <head>, t, settled: [{id, text}, ...]}` ANNOTATION row to
  `episodes/<sid>.jsonl` — the authoritative, durable record of what the clear
  took with it. A separate row, never a field on the head row, because
  seed-vs-boundary is decided only after the head row lands (the two-writer
  race fix): a seed row must never be able to claim a settle. `episode_rows`
  skips annotation rows; `episode_settles` reads them back.
- **The bell logs the drop.** `build_feed` ships the newest settled boundary
  per living session (`clearNotices`); the feed mirrors each into the shell's
  notification bell exactly once (the badge-mirror seen-set), naming the
  dropped cards and the Undo-clear way back.
- **The chat boundary card counts and names them.** "Conversation cleared — a
  fresh one starts here · N open cards dropped with it", titles on hover.
- **The composer confirms first.** A `/clear` typed into the chat composer is
  the one place romp sees the command BEFORE it runs; with open cards it now
  puts up an explicit confirm ("Its N open cards get dropped with it: …",
  Cancel default-focused) instead of letting Enter drop them silently. A
  `/clear` typed into a terminal-attached TUI is still detect-after-the-fact —
  the bell + boundary card are the guarantee there.
- **The feed's per-node story is src-aware.** A boundary clear (src `romp`)
  reads "dropped with the cleared conversation", no longer "you cleared it".

## Deferred — the read-only episode browser

The pieces exist; none are wired. When built:

- **Entry point**: the episodes log enumerates a session's past episode heads
  and their starting fsids. `build_session` gains an optional transcript
  override (it is the single `sid → path` resolution point); the parse cache
  is already keyed by leaf path, and `bin/romp-event-model --test` proves an
  arbitrary-path parse works.
- **Rendering**: reuse the `viewReadOnly` / `_kept_open` struck, composer-
  disabled tab, with a real entry point (e.g. the timeline seam's hover, or a
  "previous conversations" item in the tab context menu) instead of the
  `confirmRevive` fork — and WITHOUT offering Revive on a bare fsid.
- **Hazards mapped in advance**: never feed raw fsids through `_ordered`/
  `session-order.json` (fsid churn polluted it once); `_reveal_or_confirm`
  must be bypassed or it offers a nonsense Revive; a fork-lane fsid has no
  goal store, so the ledger/TOC panel should hide rather than render empty.
- **Search** (the user's "searchable rather than deleted"): today no surface
  searches transcript text — the Fleet box matches names + goal trees, the
  picker matches the one-line archive headline. NOTE (corrected 2026-07-27):
  the Fleet's archived-goals path (`_fleet_archived_tops`) deliberately shows
  completed-or-summarized tops only, so a bare boundary dismissal does NOT
  surface there — after compaction the dropped cards are reachable via the
  tab-hover Recent list (any status, newest 5), the episode row's settle
  record, and Undo clear (newest batch only). Transcript-text search over past
  episodes is its own project.

## Known gaps, accepted

- **tmux backend**: a TUI `/clear` there surfaces as a separate fork lane
  (the anchor sid keeps pointing at the old file), so no boundary fires for
  the anchor. The stale-card harassment this plan fixes is an SDK-path
  phenomenon (discover re-points the sid); the tmux shape is different and
  untouched.
- **A session first observed on a resume-fork leaf** (kernel down across the
  session's birth) has no seeded episode until its next `/clear`, which then
  seeds without settling — one missed boundary, never a false one.
- **Multi-`/clear` between kernel passes** records only the newest head; the
  intermediate episode's transcript is recoverable by project-dir scan but
  unattributed in the log.
- Pre-existing sessions cleared **before** this shipped keep their stale open
  cards (the seed-don't-settle rule); they settle by hand or by the next
  boundary.
