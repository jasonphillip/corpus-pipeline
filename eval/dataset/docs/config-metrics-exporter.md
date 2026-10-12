# Config Reference: Metrics Exporter

Synthetic document, Larkspur Systems wiki (fictional).

Every node and every service exposes metrics in a text format on an HTTP path. The collector scrapes them and stores them for the alert rules and the dashboard.

## Endpoint

Node exporters and service exporters listen on port 9187. The path is `/metrics`. The exporter binds to the private network interface only. Nodes reach the collector through the VPN.

## Scrape interval

The collector scrapes every 15 seconds. Each scrape has a timeout of 10 seconds. A node that misses 4 scrapes in a row is marked `down` and raises `node_down`.

## Node metrics

- `rx_frames_total`: a counter of decoded frames, by channel.
- `rx_snr_db`: a gauge with the latest SNR in decibels, by channel.
- `rx_dropped_total`: a counter of frames dropped by squelch.
- `gps_satellites`: a gauge with the satellite count.
- `gps_almanac_age_days`: a gauge with the age of the almanac.
- `outbox_frames`: a gauge with the frames waiting for upload.
- `disk_used_pct`: a gauge for the data partition.
- `bat_voltage_v`: a gauge for the battery voltage.

## Labels

Every metric carries the labels `site` and `fw` for the firmware version. Channel metrics add the label `channel`. Do not add labels with unbounded values, such as a frame ID. Unbounded labels blow up storage.

## Service metrics

The ingest gateway exports `ingest_requests_total`, `ingest_rejected_total` and `ingest_latency_seconds`. The decoder exports the counters named in the decoder config page. The queue broker exports `queue_depth` and `queue_oldest_age_s`.

## Retention

Raw scrape data is kept for 15 days. After that the collector keeps 5 minute averages for 1 year. This is separate from the telemetry retention policy.

## Adding a metric

Add the metric to the exporter code with a name that ends in a unit, for example `_seconds` or `_total`. Add a line to this page. Alert rules may use only metrics listed here.
