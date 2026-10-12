# Decision Record 0012: Reduce Raw Retention

Synthetic document, Larkspur Systems wiki (fictional).

Status: accepted. Date: 2026-02-20. Deciders: platform lead, data lead, finance partner.

## Context

The time-series store held raw frames for 90 days. The data volume reached 1.7 TB of a 2 TB volume. Growing the volume meant moving to a larger storage class, which would cost about three times more per month.

The data team measured what people read. Over six months, 97 percent of queries for raw frames asked for data under 30 days old. The oldest raw query ever seen was 52 days old, and it was a one-off replay.

## Options considered

1. Keep 90 days and buy storage. Rejected on cost.
2. Reduce to 30 days. Rejected, because incident analysis sometimes needs more than a month, and a 30 day limit would leave no margin.
3. Reduce to 40 days and move flight tracks to an archive.

## Decision

Raw frame retention drops from 90 days to 40 days. Flight tracks move to object storage at 40 days and are kept for good. One minute rollups stay for 400 days and hourly rollups for 1825 days.

The value 40 covers the longest incident review window of 10 business days plus a margin for a replay request that comes after a monthly report.

## Consequences

- Store usage fell from 1.7 TB to about 1.1 TB after the first nightly job removed old partitions.
- Raw replays older than 40 days are no longer possible. The archive can answer track questions but not per-frame SNR questions.
- The retention config gets validation rules to prevent a wrong value from being deployed.

## Risks

A wrong value in the config could fill the volume or drop data early. The April 2026 outage shows the first case. We added a CI check for it after that outage.

## Review

Review in February 2027. If fewer than 1 percent of raw queries reach past 30 days, consider a further cut to 35 days.
