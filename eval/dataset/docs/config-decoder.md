# Config Reference: Decoder

Synthetic document, Larkspur Systems wiki (fictional).

The decoder workers read frames from the queue, check them, decode the sonde payload and write the result to the time-series store. The config file is `/etc/larkspur/decoder.toml` on the decoder hosts.

## Keys

- `worker_threads = 4`: the number of threads in each worker process. Each replica runs one process. The default replica count is 3.
- `crc_mode = "strict"`: how the decoder treats frames with a failed checksum. In `strict` mode the decoder drops them. In `repair` mode it tries to fix single bit errors. In `off` mode it keeps every frame and marks it. Production uses `strict`.
- The key `max_frame_age_s` is 90 seconds by default. The decoder drops frames whose timestamp is older than this value at the time of decoding. Late frames from an outbox backlog use a separate path and skip this check.
- `dedupe_window_s`: the window for duplicate detection by site, time and channel. The default is 300.
- `batch_size`: the number of rows per write to the store. The default is 500.
- `dead_letter_after`: the number of failed deliveries before a message goes to the dead letter queue. The default is 5.

## Queue connection

The decoder connects to the queue broker on port 6710. The key `queue_url` holds the address. Credentials come from the host secret store, never from this file.

## Store connection

The key `tsdb_url` holds the address of the time-series primary on port 8812. After a database failover, update this key with `lsctl config set` and reload.

## Reload and restart

Most keys reload without a restart. Run `lsctl reload decoder`. Changes to `worker_threads` need a restart. A restart of one replica takes about 10 seconds. Restart replicas one at a time.

## Metrics

Each worker exposes the counters `decoder_frames_ok_total`, `decoder_frames_dropped_total` and `decoder_crc_fail_total` on its metrics port. The label `reason` on the dropped counter shows `crc`, `age` or `dupe`.

## Tuning notes

More than 8 threads per process gives no gain on the current hosts, because the store write is the limit. Add replicas instead of threads when the queue grows.
