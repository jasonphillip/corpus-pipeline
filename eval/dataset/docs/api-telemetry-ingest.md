# API Reference: Telemetry Ingest

Synthetic document, Larkspur Systems wiki (fictional).

The ingest gateway `tlm-ingest` accepts decoded frames from nodes. This page describes the HTTP interface. Nodes use it as a fallback when UDP delivery fails and as the path for outbox backlogs.

## Endpoint

`POST /v1/frames` on port 8443. The gateway serves HTTPS only. Plain HTTP is not offered.

## Headers

- `X-Node-Token`: the node token issued at enrollment. Required.
- `Content-Type`: `application/x-ndjson`. Each line of the body is one frame.
- `Content-Encoding`: `gzip` is accepted and recommended.

## Request body

The body holds up to 500 frames, one JSON object per line. The maximum body size is 256 KiB after decompression. A larger body gets HTTP 413.

Each frame object has these fields:

- `site`: the site ID, for example `LK-SE-009`.
- `ts`: the frame time as an ISO 8601 string in UTC, with millisecond precision.
- `chan`: the channel number, 0 to 3.
- `snr_db`: the signal to noise ratio as a number.
- `payload`: the decoded sonde payload as a hex string.
- `time_untrusted`: optional boolean. True when the GPS clock was in holdover for more than 30 minutes.

## Responses

- `202 Accepted`: the frames were queued. The body is `{"accepted": <count>}`.
- `400 Bad Request`: a line is not valid JSON. The body names the first bad line.
- `401 Unauthorized`: the token is missing or revoked.
- `413 Payload Too Large`: the body is over the limit.
- `429 Too Many Requests`: the node exceeded its rate limit. The header `Retry-After` gives the wait in seconds.
- `503 Service Unavailable`: the queue is full. Retry later.

## Rate limit

The limit is 120 requests per minute per node. A node that sends a batch of 500 frames each second in a burst reaches the limit quickly. Nodes combine frames into larger batches to stay under it.

## Idempotency

A frame is identified by the pair of `site` and `ts` and `chan`. The gateway drops duplicates silently, so a node may safely resend a batch after a timeout. Duplicates still count in the accepted number.

## Versioning

The path carries the major version. Fields may be added within version 1. Clients must ignore unknown fields.
