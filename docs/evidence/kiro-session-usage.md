# Kiro session usage and Memory

Kiro CLI 3.3.0 (not the IDE), launched by Codex. Observed 2026-11-16. Selected model: **Auto** (server-side selection, per the session context). The concrete model per turn is not exposed.

## Session id

The session context gave `sess_3e9e9467-86cf-44c7-9871-5ed05cdfee58`. No supported command in this headless session reported usage for it.

## Usage

**Per-session credits or workflow usage: unavailable.** The only supported source I could reach was `/usage`, which is account-level. I ran `kiro-cli chat --no-interactive "/usage"`, which created a separate empty session (`2fc38bf0-8c62-403d-85a7-d8cbee5fb8ad`, 0 messages). It returned an account-wide usage snapshot. Plan balances, bonus-credit amounts and renewal/expiry fields are omitted from the public evidence because they do not measure this task. The unchanged original remains in ignored `.artifacts/crew-verification/kiro-usage.out`; [the public receipt](crew-manual-verification/kiro-usage-snapshot.json) records its SHA-256 and redaction scope.

No task credit delta, cost saving or internal model handoff is claimed. Codex applied this publication redaction after Kiro completed the check.

## Repository-scoped Memory

Not verified. `~/.kiro/settings/cli.json` and `kiro-cli settings list` show no Memory setting, and I found no cross-session API. I changed no Memory, permission or telemetry setting and wrote no fact. The tracked README already states the rule: Night Watch compares committed game refs and does not embed models.
