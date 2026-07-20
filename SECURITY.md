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

AACC is designed for local operation. Its API listens only on loopback and requires a generated Bearer token, and it exposes no standalone shell-execution endpoint. However, the API can inject arbitrary text (`/send-text`) together with allowlisted keystrokes (including Enter); against a terminal-like target, that combination is equivalent to the current user's interactive typing ability, up to and including running commands. Treat the API token as a password-grade secret: keep it local and never share or transmit it. macOS automation additionally requires Accessibility permission and a successfully focused target application.
