# 1.4.3 Product Screenshot Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GitHub default-branch README's stale product screenshots with exact-text synthetic 1.4.3 mockups that show the current quota, work-directory, and completed-state UI.

**Architecture:** Create two deterministic SVG mockups with a shared 420×650 layout and language-specific copy, rasterize them to PNG assets, and update only the two default-branch README image references plus their nearby captions. Keep the existing PNGs as unreferenced rollback assets so the change does not break historical links.

**Tech Stack:** SVG, librsvg/ImageMagick rasterization, Markdown, GitHub branch/PR workflow.

## Global Constraints

- The screenshots are synthetic demo data and must not contain real account names, tokens, paths, or task data.
- The OpenCode label must say “额度” in Chinese and “quota” in English; never “用量/usage” in the new screenshots.
- The screenshots must show Codex's working-directory suffix, a blue running card, and a green completed card.
- Modify only the screenshot assets, README image paths/captions, and this plan document.

---

### Task 1: Build deterministic bilingual mockup assets

**Files:**
- Create: `docs/images/panel-overview-1.4.3.svg`
- Create: `docs/images/panel-overview-1.4.3.en.svg`
- Create: `docs/images/panel-overview-1.4.3.png`
- Create: `docs/images/panel-overview-1.4.3.en.png`

**Interfaces:**
- Produces two 420×650 PNGs that GitHub README Markdown can render directly.
- The SVG sources remain beside the PNGs so future screenshot updates can edit exact text without image-generation ambiguity.

- [ ] **Step 1: Write the two language-specific SVG layouts**

  Use the same card geometry and synthetic values in both files. The Chinese version uses `Codex 额度`, `Kimi 额度`, `OpenCode 额度`, `· codelight`, `运行中`, `已完成`; the English version uses `Codex quota`, `Kimi quota`, `OpenCode quota`, `· codelight`, `Running`, `Completed`.

- [ ] **Step 2: Rasterize and verify dimensions**

  Run:

  ```bash
  rsvg-convert -w 420 -h 650 docs/images/panel-overview-1.4.3.svg -o docs/images/panel-overview-1.4.3.png
  rsvg-convert -w 420 -h 650 docs/images/panel-overview-1.4.3.en.svg -o docs/images/panel-overview-1.4.3.en.png
  file docs/images/panel-overview-1.4.3.png docs/images/panel-overview-1.4.3.en.png
  ```

  Expected: both PNGs report `420 x 650`.

- [ ] **Step 3: Visually inspect both PNGs**

  Open both images and confirm that all labels are legible, the blue running light and green completed light are visible, quota rows are aligned, and no real user data appears.

- [ ] **Step 4: Commit the assets**

  ```bash
  git add -- docs/images/panel-overview-1.4.3.svg docs/images/panel-overview-1.4.3.en.svg docs/images/panel-overview-1.4.3.png docs/images/panel-overview-1.4.3.en.png
  git commit -m "docs: add 1.4.3 product screenshots"
  ```

### Task 2: Point GitHub's default README pages at the new screenshots

**Files:**
- Modify: `README.md:9-11`
- Modify: `README.zh-CN.md:9-11`

**Interfaces:**
- README English references `docs/images/panel-overview-1.4.3.en.png`.
- README Chinese references `docs/images/panel-overview-1.4.3.png`.
- Captions explicitly identify the image as a 1.4.3 synthetic product illustration.

- [ ] **Step 1: Update both image paths and captions**

  Preserve the existing privacy disclaimer and change only the image path plus the adjacent alt/caption wording.

- [ ] **Step 2: Verify every repository reference**

  Run:

  ```bash
  rg -n "panel-overview" README.md README.zh-CN.md
  git diff --check
  ```

  Expected: only the two new `panel-overview-1.4.3` paths are referenced by the default README pages and `git diff --check` is clean.

- [ ] **Step 3: Commit the README references**

  ```bash
  git add -- README.md README.zh-CN.md
  git commit -m "docs: point README at 1.4.3 screenshots"
  ```

### Task 3: Review and publish the default-branch documentation update

**Files:**
- Review: all files changed by Tasks 1–2

- [ ] **Step 1: Run the documentation asset gate**

  ```bash
  file docs/images/panel-overview-1.4.3.png docs/images/panel-overview-1.4.3.en.png
  git diff origin/main...HEAD --stat
  git diff --check origin/main...HEAD
  ```

  Expected: both PNGs are 420×650, only the planned assets/README files/plan are changed, and no whitespace errors are reported.

- [ ] **Step 2: Push the feature branch**

  ```bash
  git push -u origin codex/refresh-product-screenshots
  ```

- [ ] **Step 3: Merge the authorized documentation update into `main`**

  Create one non-draft PR from `codex/refresh-product-screenshots` to `main`, wait for its required checks, and merge it only after the changed-image references and asset files are present on the base branch.
