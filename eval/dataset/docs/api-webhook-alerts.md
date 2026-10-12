# API Reference: Alert Webhooks

Synthetic document, Larkspur Systems wiki (fictional).

The alert manager can send alerts to an external HTTP endpoint. Use this for chat tools and ticket systems. This page describes the request that your endpoint will receive.

## Registering a webhook

Register an endpoint with `lsctl webhook add --url <https-url> --secret-file <path>`. The URL must use HTTPS. The secret is a random string of at least 32 bytes. The alert manager keeps it in the secret store.

## Request

The alert manager sends `POST` with a JSON body. Each request carries the header `X-Larkspur-Signature`, an HMAC-SHA256 of the raw body, written as lowercase hex. Compute the same value with your secret and compare the two in constant time. Reject the request if they differ.

The request also carries `X-Larkspur-Delivery`, a unique ID for the delivery. Use it to drop repeats.

## Body fields

- `alert`: the alert name, such as `gps_lock_lost`.
- `status`: `firing` or `resolved`.
- `severity`: one of `info`, `warn` or `page`.
- `site`: the site ID, when the alert is tied to a node.
- `started_at`: the time the alert began, in UTC.
- `summary`: one sentence in plain text.
- `runbook`: the wiki path of the matching runbook.

## Response

Return any 2xx status within 5 seconds. Any other status, or no answer, counts as a failure.

## Retries

The alert manager retries a failed delivery 5 times. The wait starts at 2 seconds and doubles each time, so the waits are 2, 4, 8, 16 and 32 seconds. After the fifth failure the alert manager marks the webhook as `degraded` and sends one alert to the on-call pager.

A degraded webhook gets one test delivery every 10 minutes. It returns to normal after a 2xx answer.

## Ordering

Deliveries are not ordered. A `resolved` message may arrive before a late `firing` retry. Use `started_at` and the `status` field to settle the final state.

## Rate

A single webhook receives at most 10 deliveries per second. Above this, the alert manager groups alerts by site into one delivery with an `alerts` array.
