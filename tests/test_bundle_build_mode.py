#!/usr/bin/env python3
"""An INSTALL must ship a production bundle, and the two things that can build one must agree.

Without --production the dashboard shipped a development build: render.js — the chat pane's
code — was 578 KB of unminified JS the browser parsed before anything appeared (a slow chat load
on a fresh install, the user 2026-07-27). Minified it is 297 KB, with no sourcemaps emitted.

The drift this guards is subtle and silent: vscode-extension/install.sh builds dist at install
time, and the kernel's _ensure_bundles() REBUILDS it whenever a .ts/.css looks newer. If only one
passed --production, any later source touch would swap the served dashboard back to the slow
bundle on the next kernel restart, with nothing saying so. Source-level assertions, because the
real build needs npm install and a network.
"""
import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
KERNEL = os.path.join(ROOT, "kernel", "kernel.py")
EXT_INSTALL = os.path.join(ROOT, "vscode-extension", "install.sh")
ESBUILD = os.path.join(ROOT, "vscode-extension", "esbuild.js")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class BundleBuildMode(unittest.TestCase):
    def test_esbuild_ties_minify_and_sourcemaps_to_the_production_flag(self):
        """The flag has to actually mean something — this is what the other two rely on. The webview build
        minifies whitespace and syntax only: identifiers are kept in every mode, because the browser's
        long-frame attribution (ui/webview/perf-telemetry.ts) names a callback by its compile-time name, and
        a mangled build reports a different letter after every rebuild. The extension host keeps `minify`."""
        src = _read(ESBUILD)
        self.assertIn('const production = process.argv.includes("--production")', src)
        self.assertIn("minify: production", src)              # the extension-host bundle
        self.assertIn("sourcemap: !production", src)
        self.assertIn("minifyWhitespace: production", src)    # the webview bundles: whitespace and syntax…
        self.assertIn("minifySyntax: production", src)
        self.assertIn("minifyIdentifiers: false", src)        # …but never identifiers

    def test_the_installer_builds_production(self):
        src = _read(EXT_INSTALL)
        self.assertIn("--production", src,
                      "vscode-extension/install.sh must build a minified bundle for an install")

    def test_the_kernel_rebuild_also_builds_production(self):
        """The rebuild path is the one that silently undoes the installer's work."""
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        self.assertIsNotNone(m, "_ensure_bundles not found")
        body = m.group(0)
        self.assertIn("esbuild.js", body)
        self.assertIn("--production", body,
                      "_ensure_bundles must match the installer, or a .ts/.css touch reverts "
                      "the served dashboard to the unminified bundle")

    def test_both_honour_the_same_dev_opt_out(self):
        """One knob for a UI dev loop, spelled the same in both places — two names would mean
        turning it off in one builder and silently not the other."""
        self.assertIn("ROMP_EXT_DEV_BUILD", _read(EXT_INSTALL))
        self.assertIn("ROMP_EXT_DEV_BUILD", _read(KERNEL))

    def test_a_failed_build_retries_once_after_npm_install(self):
        """The common build failure is DEP DRIFT: a merged commit imports a package this machine's
        node_modules predates, so every restart's rebuild fails and the kernel silently serves the
        old bundle (2026-08-10: the katex import kept an eight-day-old render.js live — no fast
        toggle, no attach fixes — with only a stderr line saying so). npm install is exactly the
        cure for that class, so a failed build must refresh deps and retry once — and still be
        loud when that isn't the cure."""
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('"npm", "install"', body,
                      "a failed build must refresh UI deps — dep drift is the common cause")
        self.assertEqual(body.count("subprocess.run(argv"), 2,
                         "…and retry the same build command once after the refresh")
        self.assertIn("UI may be stale", body,
                      "a retry that still fails must stay loud")

    def test_the_staleness_scan_covers_every_source_root_esbuild_reads(self):
        """esbuild.js builds the webview entrypoints from ../ui/webview (render.ts, styles.css,
        feed.ts, …), but _ensure_bundles used to scan only vscode-extension/src — so a
        ui/webview-only edit never marked the bundle stale, and the fix sat unshipped through
        every kernel restart with the ?v= cache token frozen (found 2026-08-09 hunting the
        optimistic-echo bug: the docstring promised a rebuild the check couldn't see)."""
        self.assertIn("ui/webview", _read(ESBUILD).replace("\\", "/"),
                      "esbuild reads the shared webview sources — the premise of this guard")
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('ROOT / "ui" / "webview"', body,
                      "the staleness scan must watch ui/webview, where the webview sources live")
        self.assertIn("for src in srcs", body, "…as one scan over every source root")

    def test_webview_js_modules_required_by_the_bundles_are_watched(self):
        """esbuild follows require("./x.js") into plain-JS webview modules (gear.js), so they are
        bundle sources exactly like the .ts files — but the staleness scan globbed only *.ts/*.css,
        so a gear.js-only edit never marked dist stale and sat unshipped through every kernel
        restart. Derive the requirement from the sources rather than pinning a filename list, so
        the next required .js module is covered the day it is added, whatever it is named."""
        webview = os.path.join(ROOT, "ui", "webview")
        mods = set()
        for ts in glob.glob(os.path.join(webview, "*.ts")):
            mods.update(re.findall(r'require\("\./([\w-]+\.js)"\)', _read(ts)))
        self.assertIn("gear.js", mods,
                      "the derivation lost its known case — did the require() shape change?")
        for mod in sorted(mods):
            self.assertTrue(os.path.exists(os.path.join(webview, mod)),
                            "%s is require()d by a webview source but does not exist" % mod)
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('rglob("*.js")', body,
                      "the staleness scan must watch the plain-JS modules the bundles require()")


class FailureLineTail(unittest.TestCase):
    """A guard on a pair of constants neither side reads from the other. The kernel's _rebuild_dist keeps
    the last N characters of the script's stderr for the notice that says the served UI is stale, and
    esbuild.js's failureSummary cuts its last line to N from the END (N minus three, then an ellipsis) so
    that tail is the line's head: what failed and what is unchanged. Should the kernel's tail shrink alone, it
    shows the end of the line's last clause and drops its head; should the script's cut shrink alone, the line
    loses its location for nothing. Neither drift fails any other test."""

    def test_the_scripts_cut_is_the_kernels_tail(self):
        kernel = re.search(r"def _rebuild_dist\(\):.*?(?=\n\S)", _read(KERNEL), re.S)   # to the next top-level line
        self.assertIsNotNone(kernel, "_rebuild_dist not found")
        tail = re.search(r"\.strip\(\)\[-(\d+):\]", kernel.group(0))
        self.assertIsNotNone(tail, "_rebuild_dist keeps a tail of the script's stderr")
        n = int(tail.group(1))
        fit = re.search(r"const fit = \(s\) => s\.length > (\d+)\s*\?\s*s\.slice\(0,\s*(\d+)\)", _read(ESBUILD))
        self.assertIsNotNone(fit, "failureSummary's fit cuts the line")
        self.assertEqual(int(fit.group(1)), n, "the script cuts its line where the kernel cuts its stderr")
        self.assertEqual(int(fit.group(2)), n - len("..."), "the cut keeps room for the ellipsis")
        # the node tests stand in for the kernel's tail with a constant of their own; it follows the kernel too
        for name in ("esbuild-build.test.ts", "esbuild-failure-cap.test.ts"):
            ts = _read(os.path.join(ROOT, "vscode-extension", "src", name))
            k = re.search(r"^const KERNEL_TAIL = (\d+);$", ts, re.M)
            self.assertIsNotNone(k, name + " names the kernel's tail")
            self.assertEqual(int(k.group(1)), n, name)


if __name__ == "__main__":
    unittest.main()
