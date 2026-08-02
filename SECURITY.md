# Security Policy

## Supported Version

Security fixes are applied to the latest commit on the default branch. Older snapshots are not maintained as separate supported releases.

## Reporting a Vulnerability

Use GitHub private vulnerability reporting when it is enabled for this repository. If private reporting is unavailable, open a minimal issue requesting a private contact channel and do not include exploit details, tokens, user data, logs, internal addresses, or screenshots containing credentials.

Include the affected component, impact, reproduction prerequisites, and a minimal proof of concept. Allow reasonable time for investigation and remediation before public disclosure.

## Deployment Boundaries

- The default listener is `127.0.0.1`; remote exposure must be an explicit operator decision.
- Put remote deployments behind HTTPS and network-level access control.
- Treat `data/admin_token.txt`, identity databases, tenant directories, audit logs, exports, and OS credential-store entries as secrets or private data.
- Do not expose user-controlled interface monitoring to untrusted users without the bundled SSRF policy, quotas, timeouts, and scheduler limits.
- Rotate any credential that has been committed, pasted into an issue, or included in a public log. Removing a secret from the latest commit is not sufficient if it remains in Git history.

## Out of Scope

Upstream market-data availability, quote accuracy, exchange licensing, and vulnerabilities in unmodified third-party libraries should be reported to their respective maintainers. Reports that require disabling documented security controls are assessed case by case.
