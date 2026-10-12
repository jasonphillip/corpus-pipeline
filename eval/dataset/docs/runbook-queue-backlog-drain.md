# Runbook: Drain a Queue Backlog

Synthetic document, Larkspur Systems wiki (fictional).

Decoded frames pass through an internal message queue between the ingest gateway and the decoder workers. The queue broker listens on port 6710. A backlog means the workers cannot keep up with the ingest rate.

## Alert

The alert `queue_depth_high` fires at 50,000 messages. A normal depth during a launch is under 4,000 messages. A depth that rises for more than 10 minutes needs action.

## Steps

1. Check the depth with `lsctl queue stats`. Note the depth and the age of the oldest message.
2. Check the decoder workers with `lsctl decoder status`. Look for workers in the state `CRASHLOOP`.
3. If workers are healthy, add capacity. Run `lsctl scale decoder --replicas 6`. The default is 3 replicas. Each replica adds about 1,500 messages per second.
4. Watch the depth for 5 minutes. It should fall.
5. When the depth is under 4,000 messages, scale back with `lsctl scale decoder --replicas 3`.

## Crash loops

If workers restart over and over, scaling up does not help. Read the worker log with `lsctl decoder logs --tail 200`. A repeated stack trace points to a bad message. Move the bad message aside with `lsctl queue quarantine --id <message-id>`. Quarantined messages go to the dead letter queue named `tlm.dead`.

The dead letter queue keeps messages for 14 days. Review it each week. Do not replay messages from it into the main queue without checking that the decoder version is fixed.

## Limits

The broker refuses new messages at 250,000 messages. Past this point the ingest gateway returns HTTP 503 to nodes. Nodes keep frames in their outbox and retry, so no data is lost, but delay grows. Treat a depth above 150,000 as a SEV2 case.

## After the incident

Record the peak depth and the time to drain in the ticket. If the cause was a software bug, link the decoder issue. The February 2026 incident report shows a worked example.
