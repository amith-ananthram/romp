# kernel/: the always-on core

The Python backend: one process (`kernel.py`) that reads every session's Claude
Code transcript, builds the event tree, runs the judges, drives the session
backends, and serves the four panes over HTTP + WebSocket on `127.0.0.1:29855`.
Spawned by `bin/romp-serve` (the `bin/romp-kernel` symlink points here); see
`docs/architecture.md` for the data-flow picture.

Layered bottom-up:

| File | Layer | What it is |
|---|---|---|
| `event_model.py` | 1 | Transcript JSONL → event tree (atoms / segments / turns). The schema is pinned in `docs/event-model.md`. |
| `judge.py` | 2 | The judge engine + every judge prompt (captioner, archiver, planner, …). Writes the durable records (captions, archive, goal tree). `docs/judges.md`. |
| `kernel.py` | 3 | The read side: selects and displays what the layers below computed: HTTP + WebSocket server, pane payload builders, session lifecycle, nudges. `docs/read-side.md`. |

Session control (how romp drives Claude Code) sits behind one seam:

| File | What it is |
|---|---|
| `session_backend.py` | The `SessionBackend` ABC, the single interface the session backends (Claude Code, Codex) implement (guarded by `tests/test_session_api.py`). |
| `sdk_backend.py` | The Agent SDK backend, which runs every Claude Code session: an exact, event-based control channel. Design in `plans/sdk-backend.md`. |
| `codex_backend.py` | The Codex backend: drives OpenAI Codex sessions through `codex app-server` and materializes each thread as a transcript the read side already understands. `docs/codex.md`. |

Shared lookup tables: `colormap.py` (recency tints, single source shared with
the web bundles) and `palette.py` (session-identity colors).

`credentials.py` is the whole of romp's contact with API credentials, and romp
holds no key (the user 2026-09-08, who wants romp to hold no key). Sessions and
judge children authenticate through Claude Code's own resolution: the
`apiKeyHelper` in its settings for a key, the login otherwise, with a
login-billed launch disabling the helper through its per-session settings layer
(`"apiKeyHelper": ""` in the file the SDK hands the CLI as `--settings`). The
module has three parts. The settings reader (`api_key_helper`) resolves the
helper from the four settings files in Claude Code's precedence for a working
directory; the picker, the spawn seed and the judges' default read it and never
run it. The in-process helper runner (`helper_key`) serves the kernel's two own
API calls, the model catalog refresh and the fast-mode org probe; the value
lives in process memory for the helper's TTL and reaches no environment
variable, file or log line. The boot check (`check_boot_environment`) stops the
kernel before anything is spawned when a retired key path is still configured
(a provider line in `service.env`, the marker beside it, or a key in the
kernel's own environment), naming variable names and paths only. See
`docs/reference.md` for setup and rotation.

Everything here is loaded by file path (`loadsource.load_source`, the
`spec_from_loader` + `exec_module` idiom), not installed as a package; the repo
runs straight from a git clone. Python tests live in `tests/test_*.py`.
