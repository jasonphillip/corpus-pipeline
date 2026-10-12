# API Reference: Flight Query

Synthetic document, Larkspur Systems wiki (fictional).

The query API returns flight tracks and per-frame data from the time-series store. It listens on port 8090 and serves HTTPS. Dashboards and the reporting scripts use it. It reads from the tsdb primary.

## List flights

`GET /v1/flights?from=<time>&to=<time>`

The parameters `from` and `to` are ISO 8601 timestamps in UTC. Both are required. The span between them may be at most 31 days. A longer span gets HTTP 422 with the code `E_SPAN`.

Optional filters:

- `site`: return only flights heard by this site ID.
- `min_alt_m`: return only flights that reached this altitude in metres.
- `launch`: the launch slot, `00Z` or `12Z`.

## Pagination

Results come in pages. The parameter `page_size` sets the page length. The default is 100. The maximum `page_size` is 500. A larger value is clamped to 500 without an error. Each response carries a `next` cursor. Pass it as `cursor` to get the next page. When `next` is null there are no more pages.

## Flight object

Each flight has these fields:

- `flight_id`: a stable string such as `F-20260311-12Z-004`.
- `launch_time`: the launch time in UTC.
- `burst_alt_m`: the highest altitude reached.
- `landing`: the last known position, with `lat` and `lon`.
- `sites`: the list of site IDs that heard the flight.
- `frame_count`: the total number of frames merged into the track.

## Frames for a flight

`GET /v1/flights/{flight_id}/frames` returns the merged frames of one flight in time order. Frames with `time_untrusted` set are left out unless the query has `include_untrusted=true`.

## Caching

Responses for flights older than 2 days carry `Cache-Control: max-age=3600`. Responses for newer flights are not cached, because late frames can still arrive from outboxes.

## Limits

The API allows 60 requests per minute per token. The response code 429 carries a `Retry-After` header. A query that scans more than 5 million frames is cancelled after 30 seconds and returns HTTP 504.
