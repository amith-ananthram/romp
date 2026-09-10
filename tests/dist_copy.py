"""copy_dist: the served-page test classes' copy of a built vscode-extension/dist/ into their lab.

vscode-extension/esbuild.js writes each bundle whole to a hidden sibling of its served name,
`.<name>.tmp-<pid>-<n>` (stagingPath there), and renames it into place once every output is
staged; a staging file an exited build left behind is removed at the start of the next build. The
served-page test classes under tests/ each run `node esbuild.js` into the shared dist/ and copy it
in setUpClass, and under a parallel local run (`pytest -n`) two of them build at once: one build's
rename or removal can land between another class's listing of dist/ and its copy of that entry, and
shutil.copytree collects the vanished file into a shutil.Error, so the class errors before its first
test. A staging file is never a served asset, so the copy skips every name of that shape, present or
vanishing. tests/test_dist_copy_staging.py drives both cases and reads the shape from the script.
"""
import shutil

# The hidden name esbuild.js stages an output under: a dot, the served name, `.tmp-`, the pid, a counter.
STAGING_GLOB = ".*.tmp-*"


def copy_dist(src, dst):
    """shutil.copytree(src, dst) without any bundle staging file a concurrent build holds in src."""
    return shutil.copytree(src, dst, ignore=shutil.ignore_patterns(STAGING_GLOB))
