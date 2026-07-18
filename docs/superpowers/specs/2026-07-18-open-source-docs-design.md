# Open-source Documentation Design

## Goal

Make AACC immediately understandable and usable by the global open-source community while preserving a complete Chinese experience.

## Audience and language model

- `README.md` is the English landing page for GitHub visitors, contributors, and release users.
- `README.zh-CN.md` is a complete Chinese landing page with the same essential installation, safety, and contribution guidance.
- Product-design documents are maintained as matched English and Chinese files so implementation decisions are discoverable in either language.

## Repository surface

The repository will expose an MIT license credited to zhangboqian, explicit author contact information, contribution guidance, a security reporting path, and a code of conduct. GitHub metadata will describe the macOS desktop product and relevant discovery/agent topics.

## Documentation boundaries

Public docs describe only local behavior, supported integrations, build paths, security boundaries, and known limitations. They must never include generated local tokens, session histories, or user configuration values.

## Verification

Verify Markdown links resolve to tracked local files, the MIT license contains the declared author, both README language links exist, and the published repository metadata and commit are visible on GitHub.
