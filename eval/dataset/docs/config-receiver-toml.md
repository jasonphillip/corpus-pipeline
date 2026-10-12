# Config Reference: receiver.toml

Synthetic document, Larkspur Systems wiki (fictional).

The receiver service reads `/etc/larkspur/receiver.toml` at start. Changes need a restart of the service. Config management overwrites the file nightly from the config repository, so local edits do not last.

## Radio keys

- `center_freq_hz`: the center frequency of the SDR capture. The default is `404500000`. The band for the fleet is 400 MHz to 406 MHz.
- `sample_rate_hz = 2400000`: the SDR sample rate in samples per second. Do not change this without a firmware check, because the decoder filters assume this rate.
- `gain_db`: the SDR gain, from 0 to 49. The default is 38. Setting `gain_db` above 45 clips strong signals near the launch site.
- `squelch_snr_db`: frames below this SNR are dropped before decoding. The default is 6. A node in a noisy site may use 7.
- `channels`: the number of parallel channels. The default is 4.

## Buffer keys

- `iq_buffer_gb`: the cap for the raw IQ buffer on disk. The default is 12.
- `outbox_max_mb`: the cap for the upload outbox. The default is 2048. When the outbox is full, the node drops the oldest frames first.

## Transport keys

- `udp_target`: the address of the gateway UDP listener. The listener port is the same for all gateways.
- `udp_ack_timeout_s`: the wait for a gateway acknowledgement before a frame counts as lost. The default is 2.
- `http_fallback_after_s`: the time of failed acknowledgements before the node switches to HTTP upload. The default is 60.
- `http_batch_frames`: the number of frames per upload request. The default is 200. The maximum is 500.

## Time keys

- `gps_min_sats`: the satellite count required for a lock. The default is 6.
- `holdover_max_min`: the time in minutes before frames get the flag `time_untrusted`. The default is 30.

## Validation

Check a file before you deploy it with `lsctl config check receiver.toml`. The tool rejects unknown keys and out of range values. It prints the line number of the first error.

## Overrides

A site may override a key through the `[site]` table in the config repository. Overrides need a review from the hardware lead. Record the reason in a comment above the key.
