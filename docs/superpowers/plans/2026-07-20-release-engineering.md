# Release Engineering and Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible source/runtime installation, auditable RC DMG, bilingual documentation, and GitHub prerelease without overstating notarization.

**Architecture:** Packaging metadata is the version source; build scripts support either explicit Developer ID/notary inputs or clearly labeled ad-hoc RC output. The installer builds with development tools but creates a separate runtime-only CLI environment under Application Support.

**Tech Stack:** Bash, uv, PyInstaller, codesign/notarytool/hdiutil, PySide6 resources, GitHub CLI, pytest.

## Global Constraints

- Python package version is `1.3.0rc1`; public tag/DMG label is `v1.3.0-rc.1` / `AACC-1.3.0-rc.1.dmg`.
- Supported operating systems are macOS 13 or newer.
- Without both `AACC_CODESIGN_IDENTITY` and `AACC_NOTARY_PROFILE`, output is an ad-hoc-signed GitHub prerelease, never a stable/notarized release.
- The installed CLI runtime lives at `~/Library/Application Support/AACC/runtime` and contains no pytest, mypy, Ruff, or PyInstaller.
- Documentation and known limitations are maintained in English and Simplified Chinese.

---

### Task 1: Version, packaged QSS, redaction, and build cache

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/aacc/__init__.py`
- Create: `src/aacc/styles.qss`
- Modify: `src/aacc/gui.py`
- Modify: `src/aacc/security.py`
- Modify: `scripts/build_app.sh`
- Modify: `scripts/build_dmg.sh`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_security.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: `load_stylesheet() -> str`; build scripts consume `AACC_VERSION=1.3.0-rc.1` and optional `SKIP_BUILD=1`.

- [ ] **Step 1: Add failing resource, redaction, version, and skip-build tests**

```python
@pytest.mark.parametrize("value", [
    '"token": "abc123456"', "password='hunter2'", "Authorization: Bearer abc.def"
])
def test_redact_structured_secrets(value: str) -> None:
    assert "[REDACTED]" in redact(value)
    assert "abc123456" not in redact(value)

def test_stylesheet_is_packaged() -> None:
    assert "#panel" in load_stylesheet()
```

Shell-contract tests read scripts and assert `SKIP_BUILD`, `AACC_CODESIGN_IDENTITY`, `AACC_NOTARY_PROFILE`, and version strings are present.

- [ ] **Step 2: Prove packaging tests fail**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_security.py tests/test_packaging.py tests/test_gui.py -q`

Expected: stylesheet loader and new build variables are absent.

- [ ] **Step 3: Extract QSS, expand redaction, and update build inputs**

```python
def load_stylesheet() -> str:
    return resources.files("aacc").joinpath("styles.qss").read_text(encoding="utf-8")
```

Include `styles.qss` in Hatch wheel package data and PyInstaller `--add-data`. Redaction patterns cover quoted JSON/YAML/Python values for token/password/secret and bearer tokens while retaining the key. `build_dmg.sh` skips `build_app.sh` only when `SKIP_BUILD=1` and validates that `dist/AACC.app` exists.

- [ ] **Step 4: Run GUI/security/packaging tests**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest tests/test_security.py tests/test_packaging.py tests/test_gui.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit packaging foundations**

```bash
git add pyproject.toml src/aacc/__init__.py src/aacc/styles.qss src/aacc/gui.py src/aacc/security.py scripts/build_app.sh scripts/build_dmg.sh tests/test_gui.py tests/test_security.py tests/test_packaging.py
git commit -m "build: prepare 1.3 RC packaging"
```

### Task 2: Runtime-only installer and signing/notarization branches

**Files:**
- Modify: `scripts/install.sh`
- Modify: `scripts/build_app.sh`
- Modify: `scripts/build_dmg.sh`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Installer creates `runtime/.venv` with `uv venv` and `uv pip install --no-deps` plus exported runtime requirements, then links three CLI entrypoints from that environment.
- Signed branch requires both signing identity and notary profile; partial configuration exits nonzero.

- [ ] **Step 1: Add failing installer/signing contract tests**

```python
def test_installer_links_runtime_not_repository_venv() -> None:
    script = Path("scripts/install.sh").read_text()
    assert 'Application Support/AACC/runtime' in script
    assert 'project_root/.venv/bin/aacc' not in script
    assert 'uv sync --extra dev' in script

def test_partial_signing_configuration_is_rejected() -> None:
    script = Path("scripts/build_app.sh").read_text()
    assert 'AACC_CODESIGN_IDENTITY' in script
    assert 'AACC_NOTARY_PROFILE' in Path("scripts/build_dmg.sh").read_text()
```

- [ ] **Step 2: Prove new contract tests fail**

Run: `uv run --extra dev pytest tests/test_packaging.py -q`

Expected: runtime path and signing/notary assertions fail.

- [ ] **Step 3: Implement reproducible runtime and explicit release branches**

The installer keeps `uv sync --extra dev` only for tests/build, removes and recreates `runtime/.venv`, installs the local wheel plus production dependencies without the `dev` extra, and links `~/.local/bin/{aacc,aacc-run,aacc-gui}` to runtime executables. Build scripts ad-hoc sign when both release variables are empty. With both set they use hardened runtime, submit the DMG through `xcrun notarytool submit --wait`, staple it, and verify with `codesign --verify --deep --strict` plus `spctl --assess`.

- [ ] **Step 4: Run packaging tests and a clean temporary runtime install**

Run: `uv run --extra dev pytest tests/test_packaging.py -q`

Run: `AACC_INSTALL_ROOT="$(mktemp -d)" scripts/install.sh --no-launch`

Expected: tests pass; runtime `python -m pip show pytest mypy ruff pyinstaller` reports none installed, and CLI `aacc --help` exits zero.

- [ ] **Step 5: Commit installer and signing flows**

```bash
git add scripts/install.sh scripts/build_app.sh scripts/build_dmg.sh tests/test_packaging.py
git commit -m "build: isolate runtime and release signing"
```

### Task 3: Bilingual release documentation and manual integration checklist

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Create: `KNOWN_LIMITATIONS.md`
- Create: `KNOWN_LIMITATIONS.zh-CN.md`
- Create: `docs/macos-integration-checklist.md`
- Create: `docs/macos-integration-checklist.zh-CN.md`
- Modify: `docs/test-report.en.md`
- Modify: `docs/test-report.md`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: user-facing install/update, permission, token rotation, discovery-warning, support-matrix, and RC verification instructions.

- [ ] **Step 1: Add the integration marker and manual test skeleton**

```toml
[tool.pytest.ini_options]
markers = ["integration: requires a real macOS desktop session"]
```

The checklist contains macOS 13/14/15/26 rows with columns `machine`, `architecture`, `Accessibility`, `Codex discovery`, `API`, `DMG install`, and `result`; only this Mac's actual row may be marked tested.

- [ ] **Step 2: Write English and Chinese known-limitations documents**

Document the ad-hoc Gatekeeper warning, lack of `SIGKILL` cleanup guarantees, configurable 5-second automation timeout, Codex metadata compatibility `2026-07`, local-GUI-only token rotation, macOS 13+ floor, and versions not yet tested on hardware.

- [ ] **Step 3: Update README, changelog, and test reports**

Add `[Security]`, `[Stability]`, and `[Breaking]` entries. Explain clean install/update, Accessibility permission, discovery degradation banner, copying diagnostics, source/runtime separation, and checksum verification in both languages. State precisely that `v1.3.0-rc.1` is a prerelease and not notarized in the current environment.

- [ ] **Step 4: Validate links, versions, and forbidden claims**

Run: `rg -n "1\.2\.0|macOS 12|notarized stable|正式公证版" README* CHANGELOG.md KNOWN_LIMITATIONS* docs scripts src pyproject.toml`

Expected: no stale release version in current install/build instructions and no unsupported compatibility/signing claim; historical changelog entries may retain `1.2.0`.

- [ ] **Step 5: Commit bilingual documentation**

```bash
git add README.md README.zh-CN.md CHANGELOG.md KNOWN_LIMITATIONS.md KNOWN_LIMITATIONS.zh-CN.md docs/macos-integration-checklist.md docs/macos-integration-checklist.zh-CN.md docs/test-report.en.md docs/test-report.md pyproject.toml
git commit -m "docs: document 1.3 RC operations"
```

### Task 4: Full verification, app/DMG installation, source review bundle, and GitHub prerelease

**Files:**
- Update after measured results: `docs/test-report.en.md`
- Update after measured results: `docs/test-report.md`
- Replace source-only review copy: `/Users/zhangboqian/Desktop/code`
- Produce: `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`

**Interfaces:**
- Consumes all previous tasks and produces the local installed app, checksum, Git tag, push, and GitHub prerelease.

- [ ] **Step 1: Run the complete static and automated verification suite**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest -q`

Run: `uv run --extra dev ruff check .`

Run: `uv run --extra dev mypy src/aacc`

Run: `git diff --check`

Expected: every command exits zero.

- [ ] **Step 2: Measure focused coverage and record exact results**

Run: `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest --cov=aacc --cov-report=term-missing --cov-report=xml -q`

Expected: all tests pass; update reports with exact total coverage and focused changed-line coverage evidence, never an estimated percentage.

- [ ] **Step 3: Build and inspect app/DMG**

Run: `scripts/build_dmg.sh`

Run: `codesign --verify --deep --strict dist/AACC.app`

Run: `hdiutil verify /Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`

Run: `shasum -a 256 /Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`

Expected: ad-hoc signature and DMG verify; checksum is captured for release notes. `spctl` rejection is documented as expected for the non-notarized RC.

- [ ] **Step 4: Replace-install and smoke test this Mac**

Run: `scripts/install.sh`

Verify: app launches once, remains visible/positioned, detects active Codex tasks, terminal completed tasks remain until `×`, warning banner recovery works under a simulated bad index, token/config/database permissions are private, local API health returns `1.3.0rc1`, and Accessibility guidance opens the correct pane.

- [ ] **Step 5: Refresh the source-only review directory**

Use `rsync -a --delete` with explicit include rules for source, tests, scripts, docs, licenses, lockfile, and project metadata; exclude `.git`, `.venv`, `dist`, `build`, caches, coverage, DMGs, and secrets. Inspect with `find /Users/zhangboqian/Desktop/code -maxdepth 3 -type f` and scan for tokens before delivery.

- [ ] **Step 6: Commit measured reports, push, tag, and publish prerelease**

```bash
git add docs/test-report.en.md docs/test-report.md
git commit -m "test: record 1.3 RC validation"
git push origin codex/aacc-v1-3-rc
gh pr create --base main --head codex/aacc-v1-3-rc --title "AACC v1.3.0 RC hardening" --body-file /tmp/aacc-pr-body.md
gh pr merge --squash --delete-branch
git tag -a v1.3.0-rc.1 -m "AACC v1.3.0-rc.1"
git push origin v1.3.0-rc.1
gh release create v1.3.0-rc.1 /Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg --prerelease --title "AACC v1.3.0-rc.1" --notes-file /tmp/aacc-release-notes.md
```

Expected: main contains the reviewed changes, the tag is immutable, and GitHub shows a prerelease with DMG and SHA-256. If branch protection prevents merge, leave a ready PR and do not tag unreleased code.
