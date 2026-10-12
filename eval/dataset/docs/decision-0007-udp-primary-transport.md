# Decision Record 0007: UDP Stays the Primary Transport

Synthetic document, Larkspur Systems wiki (fictional).

Status: accepted. Date: 2026-01-15. Deciders: platform lead, firmware lead, data lead.

## Context

The November 2025 packet loss incident raised the question of whether nodes should send frames over HTTP only. HTTP gives acknowledgements and retries by design. UDP is lighter, but it can lose datagrams on the way.

The team compared both transports on a test group of 8 nodes over 30 days.

## Measurements

- HTTP used about 2.4 times more cellular data than UDP for the same frames.
- HTTP added a median delay of 900 milliseconds, because of the connection setup and batching. UDP added 120 milliseconds.
- UDP loss on healthy links stayed under 0.1 percent after the buffer fix.
- Three of the 8 test nodes would have exceeded the monthly data cap on HTTP.

## Options considered

1. HTTP only. Rejected on data cost and delay.
2. UDP only. Rejected, because a UDP outage would then lose data after the outbox fills.
3. UDP as the primary transport and HTTP as the fallback.

## Decision

UDP on port 5140 stays the primary transport. Nodes switch to HTTP upload to the ingest gateway after 60 seconds of failed acknowledgements. The key is `http_fallback_after_s` in the receiver config. Nodes return to UDP after 5 minutes of successful acknowledgements.

The gateway acknowledges each UDP batch. A node counts a batch as lost when the acknowledgement does not arrive within 2 seconds.

## Consequences

- Gateway hosts need a large socket buffer. This is part of the host image.
- The outbox holds frames that no path delivered. Nodes upload outbox content by HTTP in the background.
- Data cost stays inside the plan for all nodes in the test.

## Review

Review again if the carriers change plans or if the fleet adds sites with poor cellular signal, where UDP loss is higher.
