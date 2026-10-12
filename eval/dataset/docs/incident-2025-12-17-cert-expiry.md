# Incident 2025-12-17: Ingest Certificate Expired

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV1. Ingest was down for 47 minutes.

## Summary

The ingest certificate expired at 06:12 UTC on 2025-12-17. Nodes validate the gateway certificate and refuse to upload when it is invalid. Uploads were rejected for 47 minutes, until a new certificate was in place at 06:59 UTC.

## Impact

All 64 nodes failed their HTTP uploads in that period. UDP delivery also depends on the gateway, so no frames were stored. Nodes buffered frames in their outbox and uploaded them after recovery. No launch was in progress, so no flight track was affected. The 00:00 UTC launch had ended earlier and the 12:00 UTC launch began after recovery.

## Timeline

- 06:12 UTC: the certificate expired.
- 06:15 UTC: the alert `ingest_error_rate` fired.
- 06:20 UTC: the on-call engineer paged the platform lead.
- 06:31 UTC: the engineer found the expired certificate with `lsctl cert show`.
- 06:44 UTC: a manual renewal ran on `gw-01`.
- 06:52 UTC: the same renewal ran on `gw-02`.
- 06:59 UTC: uploads recovered on all nodes.

## Root cause

Root cause: image 2.6.0 shipped with the renewal cron job disabled. The gateway hosts were rebuilt from this image on 2025-09-18. The certificate was issued that day with 90 days of validity, so no renewal ran before expiry. The warning alert `cert_expiry_warn` existed, but it pointed to a mailbox that no longer had an owner.

## Fix

The team renewed the certificate by hand. The renewal cron job was turned on in image 2.6.1. A timer based renewal replaced the cron job after the decision on 2025-12-19.

## Follow-up actions

1. Route `cert_expiry_warn` to the pager. Done 2025-12-18.
2. Add a check that confirms the renewal job is active when an image is built. Owner: platform team.
3. Document the manual steps in a runbook. Done, see the certificate renewal runbook.

## Lessons

A warning with no owner is not a warning. Every alert now has a named team in its definition.
