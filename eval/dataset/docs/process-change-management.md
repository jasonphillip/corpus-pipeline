# Process: Change Management

Synthetic document, Larkspur Systems wiki (fictional).

This page sets the rules for changes to production: services, fleet config, firmware and alert rules. The goal is to keep launches safe while teams keep shipping.

## Change types

- `risk:low`: a change with an easy rollback and no effect on ingest. Examples are a dashboard text change or a doc fix.
- `risk:medium`: a change to a service that can be rolled back within 15 minutes.
- `risk:high`: a change to the ingest path, the database, certificates or firmware, or any change that cannot be rolled back at once.

Changes tagged `risk:high` need two approvals, one from the owning team and one from the platform lead. Medium changes need one approval. Low changes need a review but no extra approval.

## Freeze hours

No deploys during launch windows: 11:00 to 14:00 UTC and 23:00 to 02:00 UTC. These windows cover the two daily launches and the 60 minute readiness check before each. A change in progress at the start of a window must pause or finish. Emergency fixes for a SEV1 or SEV2 incident are exempt, but the on-call engineer must tell the ops channel first.

## Normal windows

Deploys of services run on weekdays between 15:00 and 21:00 UTC. Firmware updates run only on Tuesdays from 02:00 to 04:00 UTC, as set in the firmware runbook.

## Ticket

Every change has a ticket with these fields: what changes, why, the test evidence, the rollback steps and the expected duration. The rollback steps must be commands, not a promise to think of something.

## Announcing

Post in the ops channel when you start and when you finish. Include the ticket link. If the change is `risk:high`, post at least 1 hour before you start.

## After a failed change

Roll back first, investigate second. Do not try a second fix on production while the first is unresolved. Record the failure in the ticket. If the failure caused user impact, an incident review follows.

## Config changes

Fleet config lives in the config repository. Changes go through pull requests. CI checks the schema. Nightly sync applies the merged config to nodes. Do not edit files on a node, because the sync overwrites them.

## Emergency changes

An emergency change skips the approval wait, not the ticket. Fill in the ticket within 24 hours. The platform lead reviews all emergency changes at the next handoff.
