# Native installer build (step 17)

These scripts prepare **unsigned review artifacts**, not a published release.
Each OS must build its own artifact. Run from the repository root with Python
3.12 + Tk 8.6 or newer. Apple system Tk 8.5 is rejected: it produced a nearly
blank window on the test Mac despite passing a minimal Tk smoke test. The corrected
macOS development build uses Python 3.12.16 / Tcl/Tk 9.1.0.

```sh
python -m pip install -r requirements-build.txt
python tools/build_installer.py --vendor /absolute/path/to/vendor --version 1.0.0
```

The build refuses an existing destination in `dist/`; move the previous build
aside explicitly, along with `build/installer-<target>/isolated-vendor` on macOS
or `build/installer-<target>/deb` on Linux. It does not delete personal settings, videos or models.
`--app-only` stops after freezing and the functional self-test.
`--development --version 0.17.0` permits an unreviewed vendor manifest for local
inspection and uses a separate `*-development` output directory. Do not distribute
these artifacts. It does not disable checksum or executable checks.

## Vendor input

No FFmpeg download is implicit. Supply a reviewed native build of **both** tools:

```
vendor/
  ffmpeg[.exe]
  ffprobe[.exe]
  manifest.json
  notices/FFmpeg.txt
  notices/SOURCES.txt
  notices/DEPENDENCIES.txt
```

`manifest.json` is an object with:

- `target`: `windows-x64`, `macos-arm64` or `linux-amd64`;
- `ffmpeg_version`: exact version; `origin_url`: immutable origin of the selected build;
- `source_url`: corresponding source location; `configuration`: actual `ffmpeg -version` output;
- `redistribution_reviewed`: true only after reviewing the actual binary and its dependencies;
- `files`: object mapping every relative file name (except manifest itself) to its lowercase SHA-256.

Additional license/source/SBOM files belong under `notices/` and must be hashed too.
Never copy placeholder license text or set `redistribution_reviewed` merely to pass
a build. `SOURCES.txt` records corresponding sources, patches/build instructions
and how recipients obtain them. Include notices for actual linked libraries and
Python dependencies; automated manifest validation cannot determine legal completeness.
Project license selection remains stage 18. Distribution requirements depend on
the actual FFmpeg configuration: https://ffmpeg.org/legal.html.

On macOS, FFmpeg's dependency closure is isolated from OpenCV to avoid collisions
between different `libavcodec` builds. The current staging helper accepts absolute
non-system dylib paths (e.g. Homebrew-style); unresolved `@rpath` input fails explicitly.
A reviewed macOS manifest must also provide `dependency_sha256`, mapping each dylib
basename in that closure to its pre-rewrite hash. Development mode records this lock
without claiming redistribution approval. Mach-O install names are rewritten only
in build copies. macOS signing/packaging runs in a temporary directory to avoid
Finder metadata races in Documents; final package and reports are copied to `dist/`.

On Windows/Linux, dynamic dependencies are collected by PyInstaller. On clean machines verify that
none still resolve through Homebrew, a developer's Python, or a build directory.
The manifest pins the supplied binary hashes; a curated vendor lock for each release
platform is still required. A local Homebrew development manifest is **not** that lock.

## Windows x64 / Inno Setup

Use Windows x64, Python with Tcl/Tk and Inno Setup 6. Supply ISCC if absent from PATH:

```powershell
python tools/build_installer.py --vendor C:\vendor\ffmpeg --version 1.0.0 --iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Installs per-user to `%LOCALAPPDATA%\Programs\MotionSplitter`; stable AppId supports
updating the same installation. Start Menu shortcut and uninstaller are generated.
Uninstaller has no commands to delete user data. Updates/removal need VM testing,
including locked files and remnants of older versions. No Authenticode signing yet.

## macOS arm64 / PKG

Build on Apple Silicon with Xcode command-line tools (`pkgbuild`, `codesign`).
The `.pkg` installs `MotionSplitter.app` into `/Applications`. The script strips
extended attributes only from its freshly generated app and applies/verifies an
ad-hoc signature for local execution. This is **not** Developer ID signing and
**not** notarization. Distribution signing/entitlements/notarization remain pending.
Delete the app in Finder to remove it; there is no script that removes user data.

## Linux amd64 / DEB

Initial build baseline: Ubuntu 24.04 amd64 (glibc 2.39). Install build prerequisites:
`python3-venv python3-tk dpkg-dev libgl1 libglib2.0-0 libx11-6 libxext6 libxrender1 libfontconfig1`.
Build inside a matching clean VM/container; Python dependencies are bundled, system
graphics libraries remain package dependencies. Package installs under
`/opt/motion-splitter` and adds a desktop entry. Use `apt install ./…deb` and
`apt remove motion-splitter`. No maintainer scripts delete user data.
Older Ubuntu/Debian and other distributions are not yet supported claims. Resolve
and verify final dependencies on clean Ubuntu before declaring support.

## Checks and artifacts

Before packaging, native tools encode/decode a generated H.264/AAC fixture. After
freezing, the app runs `--self-test REPORT.json` with an empty PATH and a different
working directory: imports dependencies, detects motion, exports and verifies an
accurate segment using bundled tools. Failure blocks installer creation.
For interactive-host Tk smoke checking, invoke the frozen binary with
`--self-test REPORT.json --gui`. This is not a substitute for GUI acceptance.

Outputs: native installer, `SHA256SUMS`, `build-info.json`, `self-test.json`.
No upload, tag, release or GitHub publication occurs. The existing test workflow
continues running source tests; installer Actions are deferred until reviewed,
pinned vendor bundles for all three platforms are available.

References: https://pyinstaller.org/en/stable/spec-files.html,
https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm.

To inspect a macOS package without installing it:

```sh
python tools/check_macos_package.py path/to/package.pkg --gui --report tests/ci-artifacts/package-final.json
```

This expands into a temporary directory, verifies the ad-hoc signature, audits
non-system Mach-O links and runs the frozen self-test. A desktop session is required
for `--gui`. Temporary inspection directories are retained for diagnosis.
