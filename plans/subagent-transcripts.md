# Subagent transcripts: open any agent's whole conversation from the dashboard

*History (2026-09-11): the terminal (tmux) backend this plan names among the session kinds was
removed from romp on that date (issue #1398; the migration note is in `docs/reference.md`). The tmux
passages below describe the state at the time of writing and are kept as history.*

**Status: slice 1 IN FLIGHT** (branch `subagent-transcripts`, PR #935, 2026-09-05; lands with that PR's
merge commit; a second cut the same day flattened the Agent head's fold — see "The head's fold").
**Slice 2 BUILT** the same day on branch `awaiting-rows`, stacked on #935 — the Awaiting box lists what a
session waits on as rows grouped by kind, and the chip opens it; its section is at the bottom. A 2026-09-06
cut on the same branch made the box ONE presentation in both turn states (see "One presentation" there).

Direction picked by the user (2026-09-05): a Claude Code subagent (the `Agent` tool, older
transcripts say `Task`) should be readable in full from the dashboard — live while it runs and
after it finishes — the way the desktop app lets you open one. Paraphrased: the two endpoints romp
shows today are not enough to tell what an agent is doing or why it took the turn it did.

## The problem (traced 2026-09-05, before the fix)

romp showed exactly two points of a subagent's life, both on the parent's Agent tool head
(`renderTool`, pinned by `ui/webview/render-agent.test.ts`): the kickoff **prompt** and the final
**report**, as collapsed folds. Everything in between — the agent's own reasoning, its tool calls,
the files it read, the commands it ran — was invisible. Three concrete defects fell out of that:

- **No progress while running.** A background agent's head showed an amber dot for minutes with
  nothing to say about what it was on. The bg-tasks box (`renderBgTasks` / kernel `_bg_tasks`)
  had a row, but its expansion showed only the clipped prompt and the last 8 KB of an output file
  that is, for an agent, a raw JSONL — the ugly launch record, not a transcript.
- **The report fold lied for background agents.** The tool_result of a `run_in_background` Agent
  is only the launch acknowledgement ("async agent launched…"), and that is what the fold showed
  forever. The real closing summary landed later as a `<task-notification>` user turn, rendered
  as its own notice card (`renderAgentNotif`) with no link back to the head.
- **The Awaiting word confusion.** The statusline's "Awaiting agents" chip collapses to a generic
  "task" wording as soon as one pending row is not an agent (`_session_awaiting`: kind is
  `agents` only if EVERY pending row is one), so a session waiting on two agents and one build
  reads as waiting on "tasks", and nothing on screen lists which. (Slice 2.)

## Verified data facts (read-only survey of this machine; nothing real is copied here)

- Claude Code writes ONE complete JSONL per subagent beside the parent transcript:
  `~/.claude/projects/<proj-slug>/<parent-sid>/subagents/agent-<agentId>.jsonl`, plus a sidecar
  `agent-<agentId>.meta.json` with keys `agentType`, `description`, `spawnDepth`, `toolUseId`
  (optional `name`, `isFork`, `stoppedByUser`, `parentAgentId`). `agentId` is `a` + 16 lowercase
  hex. **`meta.toolUseId` equals the parent's Agent `tool_use` block id** — the join key.
- The records are the same shape as a normal transcript (`user` / `assistant` / `attachment`,
  `message.content` blocks of text / thinking / tool_use / tool_result), tagged
  `isSidechain: true`, `agentId`, `sessionId` = the PARENT sid, `parentUuid` chaining within the
  file. So the FileAdapter parse that builds the chat already understands them.
- The background Agent tool's output file (`/tmp/claude-<uid>/<slug>/<sid>/tasks/<agentId>.output`)
  is a SYMLINK to that same JSONL — the projects dir is the single authoritative copy. Background
  BASH tasks (`b` + 9 alphanumerics) are plain text and are not subagents; their treatment is
  unchanged.
- Parent linkage: the assistant record's `tool_use` block `{id, name: "Agent"|"Task", input:
  {description, prompt, subagent_type, run_in_background?}}`; the matching user record's top-level
  `toolUseResult` carries `agentId` (the async variant also `isAsync`, `status`, `outputFile`; the
  sync variant the final `content`, `usage`, `totalDurationMs`…). A background agent's report
  lands later as a `<task-notification>…<result>` user turn (`origin.kind == "task-notification"`).
- Sizes: median agent file ~400 KB / ~116 records; the largest seen ~22 MB, dominated by a few
  giant tool_result blobs. Per-block truncation (the chat's existing caps) matters more than
  pagination; the shipped event list is a capped TAIL with a `truncated` flag.

## Decision: read the file, do not un-drop the stream

The SDK backend deliberately DROPS live stream messages tagged `parent_tool_use_id`
(`kernel/sdk_backend.py` `msg_to_atom`, pinned by `tests/test_sidechain_atoms.py`): a subagent's
kickoff prompt used to leak into the parent chat as a giant expanded box. That stays exactly as it
is. This feature reads the agent's ON-DISK file instead, which:

- keeps the parent stream clean (no sidechain atoms to filter per push);
- works for dormant and revived sessions and for tmux sessions, which have no SDK stream at all;
- reads the authoritative store (the file the CLI itself writes), never a reconstruction.

Liveness is event-based, never a clock: for an SDK session the backend's SubagentStart/Stop set
(`SdkSession._subagents`, now shipped WITH each agent's id) and its task-lifecycle set
(`bgTasks`, keyed by tool-use id) say what is in flight; for a tmux or dormant session, "running"
means the parent transcript has no final result for that tool-use id yet (a sync tool_result, or a
task-notification for the launch) and the launch postdates the current CLI epoch — the same ghost
gate `_bg_tasks` already applies, so the head's dot, the gist, the viewer and the box can never
disagree.

## What ships in slice 1

### Kernel

**Discovery + join.** `_subagent_meta_map(parent_path)` enumerates
`<proj>/<fsid>/subagents/agent-*.meta.json` into `toolUseId → {agentId, agentType, description,
spawnDepth}`, cached per directory on the directory's mtime (a new sidecar changes it — a stat, not
a timer). The parent's `toolUseResult.agentId` on the tool_result record is the second source of
the join (it also fills the bg-scan rows via `_bg_step`).

**The Agent tool event** (`build_session`, the `kind:"tool"` event) now carries:
- `toolUseId` — the tool_use BLOCK id. Verified: the event's `uuid` was the RECORD uuid, so the
  block id was not on the wire before. Every tool event carries it now (cheap; the join key for
  anything else that wants to pair a result to its call).
- `agentId` (or `null`) on every Agent/Task event.
- `agentAsync: true` when the tool_result was an async launch ack.
- on EVERY Agent/Task event with a readable agent file, running or finished: `agentSteps:
  [{tool, desc, ts}, …]` — every tool call the agent has made so far, oldest first, capped at the
  NEWEST `SUBAGENT_STEPS_CAP` (200) — and `stepsTotal`, the true count (so the label can say it and
  the fold can note the cut). Folded append-incrementally from the agent's file (`em.fold_records`
  on `_AGENT_GIST_CACHE`, keyed by the file's (mtime, size)): a growing file steps only its new
  records, a finished file costs one read and then a stat per build (and its launch turn seals, so
  the chat fold stops even that). `desc` is the head vocabulary: `input.description`, else the file
  path, else the first line of the command, clipped. (2026-09-05, second cut: the first cut shipped
  only `agentGist.recent`, the last three, and only while running; the fold lists them all now, so
  `recent` left the wire — the client takes `steps.slice(-3)` for the preview.)
- while a BACKGROUND agent runs: `agentRunning: true`, `output: ""` (the launch ack is not a
  report — the client's existing "no output → amber dot" rule then reads it as running), and the
  preview's clock `agentGist: {calls, since, last}`. A FOREGROUND agent still mid-turn (no
  tool_result yet) ships the clock too — the client reads its empty output as running, so the
  preview needs it.
- once the background agent's `<task-notification>` has landed in the parent transcript: `output`
  = the notification's `<result>` text (`_parse_task_notification` now captures it; the scan-all
  rows keep it, capped like the chat's own output cap) and `isError` from its status, so the head's
  report fold shows the closing summary instead of the launch ack. The task-notification notice
  card in the transcript stays as it was. A foreground agent's tool_result was always the report.
- `agentGist` is absent once the agent has finished (no clock to show); `agentSteps` + `stepsTotal`
  keep shipping — the fold lists what the agent did under its prompt and above its report.

**The chat fold** (issue 903's sealed-prefix cache) treats a running agent the way it treats an
undecided interrupt seam: the launch's turn is never sealed while the agent runs
(`_chat_agent_open_at` holds the boundary), so the gist and the eventual report are rebuilt live
with no per-push gate. Sealed Agent events are remembered in the entry (`agents`: tool-use id,
agent id, pending flag) and one cheap gate — `_chat_agents_moved` — demotes to a full build when a
sealed pending agent's row turns terminal or comes alive again, or a sealed null `agentId` gains
its sidecar.

**Open/close protocol.** Client → kernel `{type:"openSubagent", id:<sid>, agentId}` and
`{type:"closeSubagent", id, agentId}`. The kernel answers, and re-pushes while the viewer is open,
`{type:"subagent", id, agentId, meta:{agentType, description, spawnDepth, toolUseId}, running,
events:[…], truncated}` — `events` built through the SAME `build_session` path the chat uses
(`path_override` = the agent file, plus `sidechain=True`, which keeps the parent's side-store
notes — retry recoveries, orphan replies, effort/gesture chips — out of a transcript they do not
belong to). Capped at `SUBAGENT_EVENT_CAP` tail events with `truncated: true` when cut (the
`/clear` episode fold's precedent). A missing or unreadable file pushes
`{type:"subagent", …, error:"<plain sentence>"}` — loud, never blank. Open viewers live on the
client record (`client["subagents"]`); the pusher's existing per-cycle chat pass re-sends a frame
only when its change key — the agent file's (mtime, size) and the running flag — moved, and the
per-client dedup slot `("subagent", sid, agentId)` absorbs the rest; a close or a disconnect
ends the pushes. No new polling loop.

**Federation.** Nothing bespoke: the request carries the parent `id`, so `routeOutbound` sends it
to the owning host and strips the prefix, and `prefixInbound` re-prefixes the frame's `id` — the
same path a comment-thread request takes. The client's tab id is `<parentId>/agent/<agentId>`
(`ui/webview/subagent-view.ts`), NOT the `sub:<sid>:<agentId>` shape first sketched: host-prefix.ts
reads the FIRST colon of an id as the host marker, so a `sub:` prefix would have named a phantom
host everywhere `hostOf()` is consulted (offline marks, strip dimming, outbound routing). A
colon-free suffix keeps `hostOf(subId) === hostOf(parentId)` with no special case.

**bg-tasks rows** carry `agentId` when the launch is an agent (from the ack's `agentId`, the
sidecar map, or the output symlink's basename), so the box can offer the same arrow. Shell-task
rows are untouched.

### UI (`ui/webview/render.ts` + `ui/webview/subagent-view.ts`)

- **Arrow** (level 0): a house line-icon button on the Agent/Task head — and on agent bg-task
  rows — when `agentId` is present; tooltip "open transcript" (`setTip`), delegated through
  `actions.ts` (`data-act="openSubagent"`, click-safe across re-renders, `.romp-acted` pulse).
- **Live preview** (level 0, only while running AND the head's fold is closed): up to three dim rows
  under the head, one per recent tool call in the head vocabulary (`<tool> <desc>`) — the newest
  three of `agentSteps` — newest at the bottom, the last row trailing `· N tool calls · <elapsed>`.
  It wears the tool-fold-toggle's 0.86em (no new size) and lives INSIDE the tool turn, so a
  collapsed compact run hides it and an expanded run shows it. Gone the moment the agent finishes.
  When the fold is OPEN the full list below replaces it: the render reads the fold's state from
  `openFolds` under the fold's own key (no new state), and a CSS twin
  (`.turn-tool.fold-open > .agent-preview`) hides it the instant the fold is clicked, before the
  next push rebuilds the turn.
- **The head's fold** (level 1, ONE click; same `tool:<uuid>` key as every tool fold, so an open fold
  survives re-renders). Its label says what the click reveals, in order: `prompt · 12 tool calls`
  while running, `prompt · 12 tool calls · report · 3 lines` once finished (singulars handled; the
  tool-calls part is omitted at zero — `agentFoldLabel` in `subagent-view.ts`). Inside, all OPEN
  and nothing nested: the prompt as markdown (the `prompt` field, the green-edged `.agent-report`
  box); then EVERY shipped tool call, one dim row each in the preview's own classes
  (`.agent-gist-row` / `-tool` / `-desc`), newest last, growing live on each push while the agent
  runs (the last row trails the elapsed alone — the count is in the label), with a one-line
  `N earlier calls not shown` note above when `stepsTotal > agentSteps.length`; then, once
  finished, the report as markdown. The 2026-07-08 cut nested the prompt and the report as
  collapsed caret boxes INSIDE the fold, so reading the prompt took two clicks — the user found that
  odd (2026-09-05), hence the flat fold. The list is inline (no inner scroll box): the cap bounds
  it at 200 rows, and a scroll-follow rule for a nested live list is more machinery than the case
  earns; the arrow's viewer is where a long agent gets read properly.
- **Peek tab viewer** (level 1): the arrow opens a peek tab (`peekId` / `.tab-peek` mechanics —
  `chatVisible()` says a subagent view is in the chat lens only when pinned) with id
  `sub:<sid>:<agentId>`, labelled by the sidecar's description (clipped) or agentType, wearing the
  parent's colour. Its header reads "subagent of <parent> · <agentType> · running|finished" with
  the parent name a link back to the tool head (setActive with the head's uuid as the anchor, the
  chat's own scroll-to-uuid), a pin control ("keep this tab") that converts it to a regular tab
  that stays until closed, and a "earlier part not shown" note when `truncated`. The transcript
  renders through the SAME `displayItems` / `renderEvent` path as the chat (Compact transcript
  applies), read-only: the composer is disabled, no ask controls. Live pushes replace the events in
  place with the chat's own scroll rule (follow the bottom only when already there). The romp loader
  holds the pane until the first frame; an `error` frame shows its sentence in the pane. Pinned
  subagent tabs do not survive a reload in this slice (a code comment says so).
- Feed and timeline: no changes.

## Sizes and caps

Per-block caps are the chat's own (`output` 16000 chars, `input` 4000, prompt/report markdown).
`SUBAGENT_EVENT_CAP` bounds the shipped tail; `SUBAGENT_STEPS_CAP` (200) bounds the steps on the
head, with `stepsTotal` honest about the cut; the clock is a count and two timestamps. The steps
fold state is bounded by the JSONL cache's LRU (384 files) like every other reader.

## Slice 2 (PR stacked on #935): the Awaiting box lists what a session waits on, by kind

**Status: built 2026-09-05** (branch `awaiting-rows`). The user's call, paraphrased: the three things a
session can wait on — agents, background commands, kernel watches — are different things, so show them
as SEPARATE ROWS grouped by kind, and make the chip clickable.

### The defect it fixes (traced before the change)
`_session_awaiting` answered with ONE kind chosen by source precedence: live SDK subagents → "agents";
else the pending background launches → "agents" only if EVERY pending row was an agent, else the generic
"task" (so a background shell command plus a background agent read "Awaiting 2 tasks" with the agent
silently absorbed); else armed watches → "job", a word nothing else on screen explained. The same
situation read "agents" or "tasks" depending on which source spoke first, and the statusline chip was a
plain span — the one status word on the pane you could not click through. The feed's pill showed only
when the legacy `tasks` list was non-empty, so a wait on live subagents had no clickable affordance on
the card at all.

### Kernel (`kernel/kernel.py`, `kernel/judge.py`)
- `_session_awaiting` COMBINES its live sources instead of short-circuiting. Each contributes rows
  (`_awaiting_item`): the backend snapshot's live subagents (kind `agents`, carrying the hook's
  `agentId` so the slice-1 arrow works), the pending background launches (`agents` for Agent/Task/
  Workflow dispatches, `commands` for run_in_background Bash and Monitor), and the armed watches
  (`watches`; label = the `--note`, else the clipped predicate, else `PR #N (repo)`). A background agent
  seen by BOTH the hook set and the task stream is one row (matched on agentId), wearing the launch's id
  (Stop's handle), its description, and the earlier start. **The stream row's agentId is the lifecycle
  task's own id** (`taskId` on every bgTasks row since 2026-09-06 — the CLI keys an Agent task by its
  agent id), or the async ack's `agentId` on the transcript-scan path; the sidecar meta map is only a
  fallback. Before that fix the stream rows carried no key at all (the ledger is Bash/Monitor-only and
  records the ACTING agent; the meta map is keyed on the original launch's toolUseId, which a resumed
  agent's task no longer carries), so each agent listed twice — once by type with the arrow, once as
  "Running <description>" without it — and the chip counted both. Agent rows also drop the CLI's
  "Running " prefix from the lifecycle description (`_agent_task_label`): the STATUS word already says it. `_awaiting_from_items` derives the legacy
  `kind` / `count` / `why` / `since` / `tasks` from the union: one kind present → that kind's legacy key
  and the sentence it always wore; several → kind `"mixed"`, count = every row, and a why that names
  each group ("waiting on 2 background agents, 1 background command and 1 armed watch"). The all(...)
  collapse is gone. The overlay, owned-yield, judge-stamp and delegation arms ship `items: []` (they
  name no live rows) except the peer arms, which list their peers as `peer` rows.
- **The wire shape**, shipped wherever `awaitingKind`/`awaitingCount` ship — the chat status
  (`awaitingItems`), the timeline lane (`awaitingItems`), the goal card and the placeholder card
  (`awaiting.items`): `[{kind, id, label, since, agentId?, detail?, watchId?}]`, kind in
  `agents | commands | watches | peer | timer`. `since` is the row's OWN event time or null, never now.
  A generic watch row carries `watchId` (cancel_watch's handle) and its predicate as `detail`; a PR
  watch carries neither.
- `AWAIT_KINDS` gains `"mixed"`; the judge's parse sites (`_parse_close`, `_parse_plan`) validate
  against `AWAIT_KINDS_JUDGED` (the five specific kinds), so a closer never FILES mixed — an LLM
  emitting the word degrades to kindless like any other off-enum kind, and every lift/supersede rule
  keyed on a stamp's kind keeps seeing a specific one. The overlay reader accepts mixed as data.
- Vocabulary in the kernel's sentences: "waiting on a background command: X" / "waiting on 2 background
  commands — X, …" (was "task(s)"), "waiting on 2 armed watches — X, …" (unchanged), "N background
  agent(s) still working" (unchanged). The owned-yield arm and `_awaiting_card`'s fallback headline say
  "command" too; the legacy kind KEYS (`task`, `job`) stay for every consumer that reads them.
- A `cancelWatch` WS door: `{type: "cancelWatch", id: <sid>, watchId}` → the SAME `cancel_watch` that
  `romp watch --cancel <id>` and `POST /watch {"cancel"}` reach; loud `warn` on a miss. **A cancel path
  existed for generic watches only** — nothing retires a `romp watch-pr` early today, so PR-watch rows
  carry no `watchId` and the box offers no button for them (not added here: it would be a new retire
  path, out of this slice's scope).

### Words (`ui/webview/spin-caption.ts`, `ui/romp-timeline-view.js`)
`KIND_WORD` keeps the kernel's keys and changes the words: `task` → "command", `job` → "watch",
`mixed` → no word; `kindWord` pluralizes "watch" → "watches". New shared helpers: `groupRows` (display
order agents → commands → watches → peers → timers; an unknown group is kept, never dropped),
`awaitBreakdown` ("2 agents · 1 command · 1 watch"), and `awaitWord` — the ONE label rule for the chip,
the box gist and the feed pill: one row → its word ("agent" / "command" / "watch" / "timer"; a peer row →
the peer's own name, swapped in by the caller); several of one kind → count + word ("3 agents");
several kinds → the number alone ("4"); no rows (an older kernel, a stamp) → the legacy kind + count as
before. The timeline's standalone twin mirrors the table (`tlKindWord`), and its badge goes through
`tlAwaitSuffix` so a mixed wait reads "Awaiting 4" on the lane — the only timeline change.

### Chat pane (`ui/webview/render.ts`, `styles.css`)
- The statusline chip is a `<button class="chip chip-awaitingBg chip-btn" data-act="awaitingChip">`
  on a delegate installed once on the stable `#statusline` (click-safe across the per-push rebuild; the
  delegate's `.romp-acted` pulse acknowledges). Click → `bgFoldOpen.add(sid)` (the box's own fold
  state), `renderBgTasks()`, `scrollIntoView`. Tip via `setTip`: the breakdown, the kernel's why, and
  what the click does. Label per `awaitWord`; a single named peer keeps its coloured name.
- `renderBgTasks` hands the box to `renderAwaitWhy` whenever a wait exists (tracked tasks join its
  rows); the tasks-only path (no wait: a service the session keeps around) is unchanged. `renderAwaitWhy`
  header: "Awaiting <n> · <breakdown>" for mixed, the single-kind sentence as before. Expanded: the rows
  grouped by kind under `.bg-group-head` (shown only when 2+ groups, "Background tasks" counting as a
  group for the tracked leftovers), then the plain-words note. ONE row renderer (`bgRow`, fed by
  `awaitRowSpec` / `taskRowSpec`): agent rows → the slice-1 arrow + Stop while their launch is a live
  tracked task, the prompt as the fold, NO output tail (the output file is the raw transcript; the arrow
  is the way in — this also drops the raw JSONL fold from tracked agent rows in the tasks-only path);
  command rows → status, Stop, the command + output-tail fold; watch rows → await-green dot, "armed",
  "· 31m" since registration, Cancel when `watchId` rides, the predicate as the fold; peer rows → the
  name in identity colour; a row with nothing to unfold is not a toggle. Per-row "· 12m" clocks tick
  with the statusline timer from `data-since` (the box re-renders only on new fields; `awaitKey` now
  includes `awaitingItems`). A wait with no rows (stamp / overlay) still expands to its full sentence.
- No change to WHEN the chip flips or a card moves: `_session_chip`'s formula is untouched; only what
  the surfaces say and what is clickable changed.

### Feed (`ui/webview/feed.ts`, `feed.css`)
The pill shows for ANY wait with rows (`awaiting.items`, or an older kernel's `tasks` read as rows of
the legacy kind's group), reads by `awaitWord` (a single peer → its coloured name), opens by default
like the bg-task pill did, and its expansion lists the rows grouped under `.ftask-group` headers when
2+ groups — labels only (peer rows keep the identity colour + click-opens-session). `spinFor`'s
say-it-once rule now stands the caption down under rows of ANY kind (`items` or `tasks`); a wait the
kernel cannot enumerate keeps the boxed caption, the only place its why shows.

### Tests
Kernel: `tests/test_awaiting_rows.py` (all three sources at once → mixed; the shell + agent case is two
rows of two kinds, never "task"; the hook/launch merge; single-kind sentences; watch rows' handles; the
mixed kind's enum/parse rules; the cancel door), plus the existing pins updated with a note each
(`test_awaiting_count.py` also pins that every surface ships the rows). UI: `ui/webview/awaiting-rows.
test.ts` (the label rules executed; the three word maps agree on every kind × count; chip-as-button +
delegate; grouped rows and per-kind affordances; Cancel → cancel_watch; the feed pill for any wait; the
vocabulary), plus the updated pins in awaiting-state / awaiting-box-sync / awaiting-peer-name /
bg-tasks-layout / feed-awaiting-swirl / spin-caption / timeline-awaiting / src/bg-tasks.
Screenshot fixtures: `tools/ui-verify/fixtures/awaiting-rows-{chat,feed}.html`.

### One presentation in both turn states (2026-09-06, same branch)
**The flap, seen live:** a session with two background agents and a background command showed the grouped
rows while idle ("Awaiting 3 · 2 agents · 1 command", the idle note). The moment the user sent a message the
view vanished; when the turn ended it came back. Cause: `_session_awaiting` answers None unless the session
is idle (by design — the chip's Awaiting is idle-only, a working session is Working), and the rows shipped
ONLY through it, so mid-turn `awaitingItems` was empty and `renderBgTasks` fell to the LEGACY branch — the
"N background tasks" list built from `s.bgTasks` (kernel `_bg_tasks`, never idle-gated) — or hid the box when
that list was empty. Two presentations of one set of facts, swapped at every turn boundary. The chip was
right and is untouched; the box must not flap.

- **Kernel: the rows do not depend on idleness.** The live-source assembly (hook subagents ⋈ pending agent
  launches on agentId, the pending commands, the armed watches — the slice-2 code, label rules included) is
  `_awaiting_live_rows(sid, path, live)`, called by `_session_awaiting` for the idle read and by
  `_session_background_items(sid, path)` for the turn-agnostic one; `_awaiting_join_items` is the ONE
  concatenation both use. `_awaiting_items_payload(aw, sid, path, tmux)` is what the two session-scoped
  surfaces ship as `awaitingItems` (the chat status and the timeline lane, under the SAME key): the wait's
  own rows when `aw` (= `_session_awaiting`'s answer) exists — for a live-source wait these ARE the live
  rows, for a peer stamp its peers, for an overlay row nothing — else everything in flight, read under the
  build's own liveness map (`_serve_live` lends it to `_live_scope`, the pusher cycle's one-snapshot
  mechanism): the working path never took a fresh liveness read and `test_kernel_pusher_snapshot.py`
  holds it to that — the first cut forked tmux twice per build there. `awaitingWhy` / `awaitingKind`
  / `awaitingCount` / `awaitingTasks` / `awaitingTaskIds` stay idle-gated exactly as before, so nothing
  about WHEN the chip or a card moves changed; peer waits and timers exist only through the idle-gated arms
  and simply do not appear mid-turn. The goal card and the placeholder keep their rows inside the card's
  `awaiting` object (a wait only) — the feed pill already shows only for a wait.
- **The 2026-08-30 mid-turn watch arm is retired.** The chat status used to re-run `_watch_awaiting` alone
  into `awaitingWhy` while the turn was open, to keep armed watches visible mid-turn. That satisfied the
  rule then but made the box read "Awaiting" (idle note included) under a Working chip, and left every OTHER
  in-flight row to the legacy list. Watches ride `awaitingItems` mid-turn like everything else now, and
  `awaitingWhy` means one thing on every surface: idle and waiting on these (⇔ the chip's Awaiting).
  `tests/test_kernel.py::test_a_working_session_still_lists_its_armed_kernel_watches` pins the new shape.
- **Client: one renderer.** `renderAwaitWhy` is folded into `renderBgTasks`; the legacy count-headed branch
  is gone and the string "background tasks" no longer exists in the renderer. The box shows whenever there is
  a wait, rows, or tracked tasks, and hides otherwise. Header: a wait (`awaitingWhy`) → today's
  "Awaiting 3 · 2 agents · 1 command" / single-kind sentence / named peers, await-green dot, the idle note
  closing the list; no wait → "In the background · 2 agents · 1 command" (singular forms fall out of
  `awaitBreakdown`), the worst tracked status as the dot (a failed task stays glanceable while collapsed;
  running-yellow otherwise), NO note. Row affordances are identical in both states (`bgRow` fed by
  `awaitRowSpec` / `taskRowSpec`; `s.bgTasks` still lends command rows their output tail and Stop handle).
  Tracked tasks the wait does not name list under "Also running" (was "Background tasks"); when the kernel
  names no rows at all the working header counts them as the commands they are ("In the background · 1
  command" for a dev server). The `bg-awaited` outline is one toggle: a wait, or a tracked task named in
  `awaitingTaskIds` — the ids' presence, never the chip state; mid-turn the box wears its neutral border.
  `bgFoldOpen` is only ever written by the header toggle and the chip click, so the status-only frame that
  flips `awaitingWhy` (through `awaitKey`) finds the fold as it was.
- **Tests:** kernel `tests/test_awaiting_rows.py::RowsDoNotDependOnIdleness` (same synthetic session idle vs
  mid-turn → identical rows; the wait only when idle; watches mid-turn; a stamp's own rows; the shared join),
  `test_awaiting_count.py` (the idle-only fields stay idle-only while the rows ride; the re-pinned ship
  sites), `test_kernel_bg_tasks.py` (the same three joined rows on the real `_bg_live_norm` with the turn
  open). UI `awaiting-rows.test.ts` ("one presentation" section: one renderer, the header rule per state
  executed, no note working, no legacy string, the fold writers), plus the deliberately re-pinned
  awaiting-state / awaiting-box-sync / awaiting-peer-name / bg-tasks-layout / src/bg-tasks pins, each with a
  note saying why. The chat fixture (`tools/ui-verify/fixtures/awaiting-rows-chat.html`) now shows both
  states one above the other.

### Left for later
- Nested subagents' chain in the viewer header (parent → agent → agent) — slice-2 polish from the
  original scoping, not done here.
- An early-retire path for PR watches (then the watch row's Cancel appears for them too).
- The owned-yield arm words every owned dispatch as a "command" (it carries no type); a placed agent
  dispatch that outruns a block with no stamp is the one case that reads slightly off.
