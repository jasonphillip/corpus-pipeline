# Incident 2026-04-30: Dashboard Outage

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV2. Duration: 2 hours 5 minutes.

## Summary

The dashboard on port 3080 returned HTTP 502 for 2 hours 5 minutes on 2026-04-30. Users could not view live telemetry or past flights. Ingest continued to work for the first 90 minutes, then stopped when the database refused writes.

## Impact

Engineers and launch coordinators lost the dashboard during the 12:00 UTC launch. Coordinators used the command line tool `lsctl fleet check` as a fallback. Frames received after 12:40 UTC waited in the queue and were stored after recovery.

## Timeline

- 11:20 UTC: the metric `tsdb_disk_used_pct` passed 95 percent. The alert went to a channel that nobody watched.
- 12:05 UTC: the dashboard began to return 502.
- 12:10 UTC: the on-call engineer was paged by a user report, not by an alert.
- 12:35 UTC: the engineer found that the tsdb data volume was full.
- 13:20 UTC: the engineer removed old raw data and restarted the store.
- 14:10 UTC: the dashboard recovered.

## Root cause

The cause was the tsdb disk reaching 100 percent. A template change on 2026-04-27 set the raw data retention to 400 days instead of 40. The value for rollups had been copied into the raw field. The store stopped dropping old raw data and the volume filled over three days.

## Fix

The retention value was corrected. The oldest raw partitions were dropped by hand. A validation rule in the config repository now rejects a raw retention value above 90 days.

## Follow-up actions

1. Route `tsdb_disk_used_pct` above 80 percent to the pager. Done.
2. Check config templates with a schema test. Owner: platform team.
3. Add a dashboard status page that does not depend on the tsdb. Owner: web team.

## Lessons

The two retention fields had similar names. The schema now gives them distinct key names and checks each against its own limit.
