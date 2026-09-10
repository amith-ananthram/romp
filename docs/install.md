# Install

## Requirements

- **[Claude Code](https://docs.claude.com/en/docs/claude-code), signed in.**
  Install it and run `claude` once in a terminal to log in.
- **Python 3.10 or newer, and Node.js.**

    ```bash
    brew install python node               # macOS (Homebrew)
    sudo apt install python3 nodejs npm    # Ubuntu / Debian
    ```

### Which Python runs the kernel

`romp-serve` chooses the interpreter each time it starts the kernel, and the
choice follows the Agent SDK venv (`sdkvenv` under the state directory), whose
compiled extensions import into the kernel process and so must be built for the
interpreter the kernel runs. The order is: `ROMP_PYTHON` if set, refused with
one line when it is not an executable interpreter; otherwise the interpreter
the venv's `pyvenv.cfg` records, if it still runs and still reports the venv's
version and build; otherwise another Python of that same minor and build on
`PATH` or in `~/.local/bin`, which the venv still matches; otherwise the newest
`python3.X` on `PATH` or in `~/.local/bin`, then `python3`, the rule for a
machine that has no venv yet (`pick_python` in `bin/romp-serve`;
`bin/romp-sdk-setup` and `bin/romp-codex-setup` carry the same function, so
each venv is built with the interpreter the kernel runs). `install.sh` only
checks that a `python3` exists. The full rules, and what the kernel reports
when the two disagree, are in the [reference](reference.md#the-kernels-python).

Because the venv comes first, installing another interpreter does not move the
kernel onto it. One hazard remains: `uv python install <version>` puts a
`python3.X` shim in `~/.local/bin`, which the newest-first fallback searches, so
on a machine with no SDK venv (or a venv whose recorded interpreter is gone) the
next restart runs the newest Python it finds. Install extra interpreters with
`uv python install --no-bin <version>` and reach them through `uv python find
<version>` or a venv, never as a bare `python3.X` on `PATH`. To move the kernel
to another Python on purpose, whether another version or the free-threaded build
(`3.14t`) of the same one, go in this order: set `ROMP_PYTHON` to the new
interpreter (in `~/.config/romp/service.env` for the login service), rebuild the
SDK venv for it with `ROMP_PYTHON=<path> bin/romp-sdk-setup` (the same value the
service reads; run plainly, the script follows the existing venv's interpreter
and rebuilds nothing), run the test suite there, then restart.
Skipping a step leaves a kernel that cannot start sessions; the setup script says
from what to what it rebuilds, and the kernel names the mismatch on every
session's card if it comes up on the wrong interpreter anyway.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/romp-on/romp/main/bootstrap.sh | bash
```

Open a new terminal afterwards, so `~/romp/bin` is on your `PATH`, and type
`romp` to launch the user interface in a browser.

The same command updates Romp later. To remove Romp, run `romp uninstall` (add
`--purge` to delete recorded sessions too).

This clones Romp to `~/romp` and installs the newest release.
[What it installs, in detail](architecture.md#what-the-installer-sets-up).

### Manual and custom installs

Install this way to keep Romp somewhere other than `~/romp`, or to run the
latest commit rather than the newest release:

```bash
git clone https://github.com/romp-on/romp.git ~/romp
cd ~/romp
git checkout "$(git tag -l 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1)"   # newest release
# or:   git checkout main                                        # the latest commit
./install.sh
```

Then add `bin/` to your `PATH` in your shell rc; `install.sh` prints the exact
line for your clone.

```bash
export PATH="$PATH:$HOME/romp/bin"
```

## First run

The installer leaves Romp's back end running, so there is nothing to start. Open
the dashboard by typing `romp` in the terminal. That prints the URL at which
Romp can be reached and opens it in your browser.

### In VS Code or Cursor

The installer adds the extension automatically. Reload your editor window and
open Romp from the sidebar.

### Start a session

<video src="../assets/guide/first-session.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>
