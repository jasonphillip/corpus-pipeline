# Decision Record 0006: Certificate Renewal Automation

Synthetic document, Larkspur Systems wiki (fictional).

Status: accepted. Date: 2025-12-19. Deciders: platform lead, security officer.

## Context

The ingest certificate expired on 2025-12-17 and ingest was down for 47 minutes. A cron job was supposed to renew it. The job was disabled in a base image and nobody saw the warning. We need renewal that fails loudly.

## Options considered

1. Fix the cron job and add a test. Rejected, because cron gives no record of success, and a missed run is silent.
2. Buy a longer lived certificate. Rejected, because the internal certificate authority issues 90 day certificates only, by policy.
3. Use a systemd timer that checks often and exports a metric.

## Decision

Use a systemd timer named `lsctl-cert-renew.timer`. It runs every 12 hours on each gateway host. The timer renews the certificate when 30 days remain. It exports the metric `cert_days_remaining` and the metric `cert_renew_last_success_ts`.

Alert rules:

- `cert_expiry_warn` fires when 14 days remain. It goes to the pager, not to a mailbox.
- `cert_renew_stale` fires when the last success is more than 36 hours old.

## Why 30 days

A certificate lasts 90 days. A renewal at 30 days remaining leaves 30 days of retries before the warning alert and 16 days between the warning and the expiry. This covers a long weekend plus a holiday week with margin.

## Why every 12 hours

A 12 hour check gives two attempts a day. A single failure is retried within half a day, and the stale alert fires after three missed runs.

## Consequences

- The manual runbook remains for the case that the timer fails twice in a row.
- Both gateway hosts renew independently, so their expiry dates may differ by a day. This is acceptable.
- Image builds run a test that checks the timer is enabled. A build that fails this test is not published.

## Review

Review after one full year of operation, in December 2026.
