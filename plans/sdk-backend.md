# The SDK (non-tmux) session backend

*History (2026-09-11): the terminal (tmux) backend this plan was written to coexist with was removed from romp on that date (issue #1398; the migration note is in `docs/reference.md`). The tmux passages below describe the state at the time of writing and are kept as history.*

Resolves the open question in `event-model.md` ("Agent SDK vs hand-rolled
stream-json client for the future headless-with-parity substrate") and the
backlog item "Headless-with-parity substrate". **Decision: the Python Agent
SDK**, as a second `SessionBackend` that coexists with tmux (selectable per
session), motivated by robustness — retiring the fragile TUI-scraping
(`capture-pane`, `send-keys`, arrow-key picker driving, stuck-state healing) in
favour of an exact, event-based control channel.

## Why this is a near-passthrough

The read side is already substrate-neutral: the event model, judges, and all
three panes consume only the transcript JSONL under `~/.claude/projects/`. The
SDK drives the **same `claude` binary** romp already launches in tmux, so it
writes the **same transcripts to the same paths** — the read side is unchanged.
tmux was only ever the *drive + liveness* layer, never the data layer. The SDK
backend swaps that layer: stdin JSON (not `send-keys`), a message stream (not
`capture-pane`), `interrupt()` (not the Escape key), and `can_use_tool` (not
picker pane-scraping).

## Verified mechanics (probed live against claude 2.1.186, SDK 0.2.107)

All of the following was confirmed empirically, not inferred:

- **Auth / billing.** The SDK spawns the user's `claude` CLI (via
  `ClaudeAgentOptions.cli_path`) and inherits its logged-in credentials. A live
  turn returned a `RateLimitEvent(rate_limit_type="five_hour")` — the
  **subscription** window — so usage bills against the Max plan exactly as the
  tmux path does today. No API key, no `CLAUDE_CODE_OAUTH_TOKEN` needed when a
  CLI binary is already logged in.
- **Transcripts.** The session wrote
  `~/.claude/projects/<munged-cwd>/<session_id>.jsonl`, identical format and
  location to the CLI. `session_id` is forced via `ClaudeAgentOptions.session_id`
  (≡ `--session-id`) and resumed via `resume` (≡ `--resume`), preserving romp's
  anchor-sid / lastSid identity model.
- **Interrupt.** `await client.interrupt()` exists and cancels the in-flight
  turn while keeping the session alive (the true Esc equivalent).
- **AskUserQuestion → `can_use_tool`** (the headline result). When the model
  calls AskUserQuestion it arrives at the `can_use_tool` callback as
  `tool_name="AskUserQuestion"`, `input` =
  `{"questions":[{"question","header","multiSelect","options":[{"label","description","preview"?}]}]}`
  — the whole picker, including any `preview` mockups, as **data**. To answer,
  return:

  ```python
  PermissionResultAllow(updated_input={
      "questions": input["questions"],
      "answers": {question_text: "label" | ["label", ...]},   # one entry per question
  })
  ```

  The CLI then synthesises the tool_result (`"…answered: \"Q\"=\"cats\"…"`) and
  the model continues. Returning `PermissionResultDeny(message=…)` surfaces the
  message to the model as an `is_error` tool result (a cancel).
- **Ordinary permissions** (Bash/Edit/Write/…) use the **same** `can_use_tool`
  channel, keyed on `tool_name`; return allow / allow-with-`updated_input` /
  deny. So one mechanism covers both the picker and permission prompts — the
  Shift+Tab mode-cycling and `/model` confirm-Enter hacks are gone.
- **Observable state.** The `init` SystemMessage carries `session_id`, `model`,
  `permissionMode`, `tools`, `mcp_servers`; hook inputs carry
  `effort: {level}`. `ResultMessage` marks turn end. So state is exact and
  event-based (user message written → working; `ResultMessage` → waiting), per
  the repo's "events over heuristics" rule — and the pane-scrape diagnostics
  (stuck-working, compaction-% OCR) disappear.
- **Conversation rewind / edit-message branch** (claude 2.1.210). The CLI's
  `--resume-session-at <record uuid>` (no typed
  SDK field → `extra_args` passthrough) resumes loading only messages up to and
  including the target, and the next turn appends to the **same** transcript
  file with `parentUuid=target` — an in-place branch, same fsid, no lastSid
  churn. User-record uuids are valid targets too (not just assistant ones), so
  the cut point for editing message U is simply U's nearest user/assistant
  ancestor. Records at/before the last `compact_boundary` are NOT addressable
  ("No message found", exit 1 — loud, nothing written); post-boundary replayed
  records are. The event model's leaf→root walk already drops the abandoned
  tail ("rewound branches are non-ancestors"), so chat/timeline/judge heal from
  the parse with no extra plumbing. romp's write side: the chat's edit
  affordance → kernel `rewindSend` (validates via `_rewind_target`) → 
  `SdkBackend.rewind()` — a ONE-SHOT flag (`reg.rewindTo` + the leaf recorded
  at request time) applied on reconnect only while the transcript's leaf is
  unmoved (`rewind_disposition`), with the edit turn HELD by the input gate
  until the rewound client is up. A refused connect drops flag + held turn and
  warn-toasts (never a crash loop). Claude's own task store is NOT rewound —
  to-dos created on the abandoned branch survive, exactly as the interactive
  CLI behaves.

## Architecture

```
kernel (threaded, single-writer; no asyncio)
  │  _send_to_app(app,msg)  ▲ resolve(reqId,decision)
  ▼ in_q.put(text)          │
SdkBackend  ── one daemon thread per session ──┐
  asyncio.run(  ClaudeSDKClient(cli_path, session_id|resume,
                  mcp_servers, can_use_tool, extra_args={append-system-prompt}) )
  │  events → out_q → kernel → WS                (event loop quarantined in-thread)
  └  can_use_tool → emit askLive/permission to chat clients, block on a per-request
       Event until the user answers, then return PermissionResultAllow/Deny
```

- **Quarantined event loop.** Each SDK session runs in its own daemon thread
  that owns a private `asyncio.run()`; the loop never escapes that thread. The
  kernel stays threaded and synchronous and bridges via thread-safe queues
  (`in_q` to send turns, `out_q`/callbacks to receive). The single-writer
  invariant is untouched — session threads only *produce* events; the kernel's
  judges remain the sole writers of durable records.
- **AskUserQuestion reuses the existing picker UI.** The `can_use_tool` input is
  translated into the same `{"type":"askLive","id":sid,"ask":{kind,header,
  question,options:[{n,label,desc,…}]}}` message the pane-scraper emits, and the
  existing inbound `answerAsk`/`toggleAsk`/`submitAsk`/`addCustomAsk`/`cancelAsk`
  messages are translated back into the `answers` mapping. Zero UI changes.
- **Identity / state files are shared.** `names/<sid>` and `states/<sid>.jsonl`
  are written exactly as the tmux path writes them, so discovery, the timeline
  state-log, and the judges work uniformly across both backends.

## What changes vs tmux (watch-outs)

- **Supervision flips.** tmux is a durable host that survives kernel restarts and
  is attachable (`tmux attach`). SDK sessions are children of the kernel; on
  kernel restart they revive from transcript (`resume`). State is durable; the
  live process is not. There is no raw TUI to attach to — the only window is
  romp's panes (aligned with romp's philosophy).
- **Dependency.** Adds `claude-agent-sdk` (Python 3.10+). Imported lazily so the
  tmux-only path keeps zero third-party deps and degrades gracefully when the SDK
  is absent.
- **Possibly coarser display signals.** Live compaction-% (today pane-OCR'd) and
  context-% may be coarser until derived from stream `usage`/system events.

## Phases

0. `SessionBackend` seam in the kernel; today's tmux calls become `TmuxBackend`.
1. Shared registry/state helpers (`names/`, `states/`).
2. `SdkBackend`: per-session quarantined-loop thread; send/interrupt/spawn/
   resume/kill/rename/live_sessions; crash-recovery via `resume` lastSid.
3. `can_use_tool` round-trip → askLive/permission over WS and back.
4. UI + lifecycle integration (spawn/resume/kill headless from the web UI;
   backend kind in the registry; kernel-restart revival).
```
