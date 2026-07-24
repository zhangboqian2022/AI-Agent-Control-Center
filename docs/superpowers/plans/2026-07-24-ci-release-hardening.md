# CI and Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dependency auditing, lockfile reproduction, changed-line coverage, formatting, release assets, and self-signed community-install instructions continuously verifiable.

**Architecture:** PR CI performs only checks possible before a release exists. A separate release verifier checks GitHub lifecycle state after assets are published. Documentation clearly separates a self-signed community build from an Apple-notarized build.

**Tech Stack:** GitHub Actions, uv, pip-audit, pytest-cov, diff-cover, ruff, Bash, curl, jq.

## Global Constraints

- Do not require a future Release URL to exist in ordinary PR CI.
- Dependency vulnerabilities fail CI unless an explicit time-bounded exemption exists.
- Never claim Developer ID signing, notarization, or unperformed macOS matrix results.
- Keep README and user-guide changes bilingual.

---

### Task 1: Enforce reproducible and auditable CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `scripts/build_app.sh`
- Modify: `scripts/install.sh`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: locked sync, coverage XML, 90% diff coverage, blocking pip-audit JSON artifact, and format check.

- [ ] **Step 1: Add packaging assertions**

```python
def test_ci_enforces_locked_sync_audit_report_and_diff_coverage():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "uv sync --locked --extra dev" in workflow
    assert "continue-on-error: true" not in workflow
    assert "pip-audit.json" in workflow
    assert "diff-cover coverage.xml" in workflow
    assert "ruff format --check src tests" in workflow
```

- [ ] **Step 2: Verify the test fails**

Run: `.venv/bin/python -m pytest tests/test_packaging.py::test_ci_enforces_locked_sync_audit_report_and_diff_coverage -q`
Expected: FAIL on current CI text.

- [ ] **Step 3: Add the CI steps**

Use checkout `fetch-depth: 0`, `uv sync --locked --extra dev`,
`ruff check`, `ruff format --check`, mypy, pytest with
`--cov=src/aacc --cov-report=xml`, and:

```yaml
- name: Changed-line coverage
  run: uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=90
- name: Dependency vulnerability scan
  run: uv run pip-audit --skip-editable --format=json --output=pip-audit.json
- name: Upload vulnerability report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: pip-audit-report
    path: pip-audit.json
```

Add bounded dev dependencies for `diff-cover` and `pip-audit`, then run
`uv lock`. Change build/install sync commands to `uv sync --locked --extra dev`.

- [ ] **Step 4: Format the existing tree mechanically**

Run: `.venv/bin/ruff format src tests`
Expected: only formatting changes; no behavior changes.

- [ ] **Step 5: Run local gates**

Run:

```bash
uv sync --locked --extra dev
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
.venv/bin/python -m pytest -q
.venv/bin/pip-audit --skip-editable
```

Expected: every command exits 0 and pip-audit reports no known vulnerabilities.

- [ ] **Step 6: Commit formatting separately, then CI policy**

```bash
git add src tests
git commit -m "style: format Python sources"
git add .github/workflows/ci.yml pyproject.toml uv.lock scripts/build_app.sh scripts/install.sh tests/test_packaging.py
git commit -m "ci: enforce audit and coverage gates"
```

### Task 2: Verify published release assets without a PR lifecycle cycle

**Files:**
- Create: `scripts/verify_release.sh`
- Modify: `tests/test_packaging.py`
- Modify: `docs/macos-integration-checklist.md`

**Interfaces:**
- Produces: `scripts/verify_release.sh <version>` using the public GitHub API.

- [ ] **Step 1: Add shell contract tests**

Assert the script is executable, passes `bash -n`, requires one semver argument, checks
`draft == false`, checks `prerelease == false`, locates `AACC-$version.dmg` and
`.sha256`, rejects zero sizes, and uses `curl -I -f -L` for the download.

- [ ] **Step 2: Verify the test fails**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -k "release_assets" -q`
Expected: FAIL because the script is absent.

- [ ] **Step 3: Implement the verifier**

The script must use `set -euo pipefail`, validate
`^[0-9]+\.[0-9]+\.[0-9]+$`, query:

```text
https://api.github.com/repos/zhangboqian2022/AI-Agent-Control-Center/releases/tags/v$version
```

with curl, parse exact fields with jq, and verify both asset names and positive sizes.
It must never print signed redirect query strings.

- [ ] **Step 4: Test against v1.4.0**

Run: `scripts/verify_release.sh 1.4.0`
Expected: PASS and a concise statement naming both assets.

- [ ] **Step 5: Document release order and commit**

Record that the verifier runs after draft assets are uploaded and again after publishing;
ordinary PR CI does not check nonexistent future assets.

```bash
git add scripts/verify_release.sh tests/test_packaging.py docs/macos-integration-checklist.md
git commit -m "ci: verify published release assets"
```

### Task 3: Clarify self-signed community installation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/troubleshooting.en.md`
- Modify: `docs/troubleshooting.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: exact checksum and Gatekeeper instructions without claiming notarization.

- [ ] **Step 1: Add bilingual documentation assertions**

Assert the English and Chinese install sections contain:

```text
shasum -a 256 AACC-1.4.1.dmg
xattr -cr /Applications/AACC.app
```

and explicitly say self-signed/not notarized.

- [ ] **Step 2: Verify assertions fail**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -k "gatekeeper or checksum" -q`
Expected: FAIL because exact commands are absent.

- [ ] **Step 3: Add staged instructions**

Order the guidance as: download only from the official Release, compare SHA-256, try
Privacy & Security → Open Anyway, and use `xattr -cr` only when the verified official
build remains quarantined. Warn never to run the command for an unverified download.

- [ ] **Step 4: Run packaging tests and commit**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: all tests PASS.

```bash
git add README.md README.zh-CN.md docs/user-guide.en.md docs/user-guide.md docs/troubleshooting.en.md docs/troubleshooting.md tests/test_packaging.py
git commit -m "docs: clarify self-signed community installation"
```

### Task 4: Set the patch version after behavior is complete

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/aacc/__init__.py`
- Modify: `scripts/build_app.sh`
- Modify: `scripts/build_dmg.sh`
- Modify: `tests/test_api.py`
- Modify: `tests/test_packaging.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/user-guide.md`
- Modify: `CHANGELOG.md`
- Modify: `CHANGELOG.zh-CN.md`

**Interfaces:**
- Produces: internally consistent source version `1.4.1`; it does not create a tag or Release.

- [ ] **Step 1: Change version assertions to 1.4.1 and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_packaging.py tests/test_api.py -q`
Expected: FAIL while source remains 1.4.0.

- [ ] **Step 2: Update every active version surface**

Set package, lock, `__version__`, script defaults, API expectation, README links, guide
filenames, and bilingual changelogs to `1.4.1`. Do not edit historical release reports.

- [ ] **Step 3: Verify version consistency**

Run:

```bash
.venv/bin/python -m pytest tests/test_packaging.py tests/test_api.py -q
rg -n 'AACC-1\.4\.0\.dmg|releases/(download|tag)/v1\.4\.0' README.md README.zh-CN.md docs/user-guide.md docs/user-guide.en.md
```

Expected: tests PASS and rg returns no active download references.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/aacc/__init__.py scripts/build_app.sh scripts/build_dmg.sh tests/test_api.py tests/test_packaging.py README.md README.zh-CN.md docs/user-guide.en.md docs/user-guide.md CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "chore: prepare AACC 1.4.1"
```
