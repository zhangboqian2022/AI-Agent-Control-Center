# Security Policy

## Supported versions

Security fixes are applied to the latest published AACC release and the current `main` branch.

## Reporting a vulnerability

Please do **not** create a public GitHub issue for a suspected vulnerability. Send a concise report to <zhangboqian@hotmail.com> with:

- affected version and macOS version;
- a minimal reproduction or proof of concept;
- impact and any practical mitigations;
- whether the report includes sensitive material.

Do not email API tokens, private prompts, source code, screenshots with secrets, or full local configuration files. We will acknowledge the report, assess impact, and coordinate a fix and disclosure timeline.

## Security model

AACC is designed for local operation. Its API listens only on loopback and requires a generated Bearer token. The project intentionally excludes remote-control endpoints and arbitrary command execution. macOS automation actions are constrained by a key allowlist and require a successfully focused target application.
