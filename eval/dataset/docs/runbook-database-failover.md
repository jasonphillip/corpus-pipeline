# Runbook: Time-Series Database Failover

Synthetic document, Larkspur Systems wiki (fictional).

The time-series store, called `tsdb`, keeps decoded telemetry. The primary instance listens on port 8812. Two replicas, `replica-a` and `replica-b`, follow it. Dashboards and the query API read from the primary.

## When to fail over

Promote the replica only when one of two conditions holds. First, the primary has been down for more than 5 minutes. Second, replication lag has stayed above 5 seconds for 10 minutes and the primary cannot be fixed fast. A short lag spike alone is not a reason.

## Checks before you promote

1. Run `tsdbctl status` on the primary host. If the host answers, try `tsdbctl restart` once.
2. Run `tsdbctl lag` and note the lag of each replica.
3. Pick the replica with the lowest lag. By default this is `replica-b`, which sits in a second rack.

## Promotion

Promote the replica with `tsdbctl promote replica-b`. The command takes about 40 seconds. It stops replication on the replica, makes it writable and prints the new primary address.

Then update the service config. Run `lsctl config set tsdb.primary <address>:8812` and reload the ingest gateway and the query API with `lsctl reload`. Dashboards reconnect on their own within 1 minute.

## After promotion

The old primary must not come back as a second writer. Keep it shut down. When it is repaired, wipe its data directory and rebuild it as a replica with `tsdbctl join --from <new-primary>`. A rebuild of a full data set takes about 3 hours.

## Data loss window

Replication is asynchronous. A promotion can lose the last few seconds of writes. Nodes keep frames in their outbox until the gateway confirms them, so the lost frames are uploaded again after the failover. Check for gaps with `tsdbctl gaps --last 1h` and confirm that the gap count is zero 30 minutes after promotion.

## Notify

Post in the ops channel when you start and when you finish. Open a SEV2 ticket for any promotion.
