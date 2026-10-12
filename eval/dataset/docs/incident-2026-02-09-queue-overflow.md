# Incident 2026-02-09: Queue Overflow

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV2. Duration: 4 hours 10 minutes.

## Summary

On 2026-02-09 a bad decoder release caused the message queue to grow until it was nearly full. Queue depth peaked at 187,000 messages. The ingest gateway did not reach its limit of 250,000 messages, so no uploads were refused.

## Impact

Decoded frames reached the dashboard with a delay of up to 55 minutes. Two launches showed stale tracks for the first part of the flight. No frames were lost.

## Timeline

- 08:05 UTC: decoder version 4.1.0 was deployed.
- 08:50 UTC: the alert `queue_depth_high` fired at 50,000 messages.
- 09:00 UTC: the on-call engineer scaled the decoder to 6 replicas. Depth kept rising.
- 09:25 UTC: the engineer read worker logs and found workers in a crash loop.
- 10:15 UTC: the logs pointed to a frame with type 0x1F. The decoder crashed on it and the message returned to the queue.
- 11:10 UTC: the team rolled the decoder back to 4.0.9.
- 12:15 UTC: the queue was drained to under 4,000 messages.

## Root cause

Decoder 4.1.0 crashed on frames of type 0x1F when the payload length was zero. Nodes began to send this frame type in firmware 2.8.0 as a heartbeat. The decoder code read the first byte of an empty payload. The crashed worker never acknowledged the message, so the broker delivered it again to another worker. Every worker in turn crashed on the same message, and new messages piled up behind it.

## Fix

Decoder 4.1.1 checks the payload length before reading it. A new unit test covers every frame type with an empty payload. The poison message was moved to the dead letter queue.

## Follow-up actions

1. The broker now moves a message to the dead letter queue after 5 failed deliveries. Done.
2. Add a canary stage for decoder releases, with 1 replica for 30 minutes. Owner: platform team.
3. Fuzz test the decoder with empty and truncated payloads. Owner: data team.

## Lessons

Adding replicas does not help when the same message crashes every worker. Check the logs before you scale.
