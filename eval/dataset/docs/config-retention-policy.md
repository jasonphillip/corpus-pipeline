# Config Reference: Retention Policy

Synthetic document, Larkspur Systems wiki (fictional).

The time-series store drops old data on a schedule. The retention policy sets how long each data tier is kept. The policy file is `retention.toml` in the config repository. A job named `tsdb-retain` applies it every night at 03:30 UTC.

## Tiers

| Key | Meaning | Value |
|---|---|---|
| `retention.raw_days` | decoded frames at full detail | 40 |
| `retention.rollup_1m_days` | one minute rollups | 400 |
| `retention.rollup_1h_days` | hourly rollups | 1825 |

Raw frames are kept for 40 days. One minute rollups keep the mean, minimum and maximum of SNR and altitude for each flight and node. Hourly rollups keep fleet level counts and health data. The value 1825 days is 5 years.

## Where raw data is used

Replays, decoder bug checks and flight re-processing use raw frames. After 40 days these jobs must read an archive instead, which holds flight tracks only.

## Archive

Flight tracks go to object storage when they are 40 days old. The archive keeps tracks forever. It does not keep per-frame SNR.

## Validation rules

The config repository CI rejects:

- a `raw_days` value above 90.
- a `rollup_1m_days` value below the `raw_days` value.
- a key that is missing.
- a value that is not an integer.

## Capacity

With the policy above, the store uses about 1.1 TB of a 2 TB volume. Raw data takes 0.7 TB of that. A change that doubles raw retention would fill the volume in a few weeks, so any change needs a capacity check from the platform team.

## Changing the policy

Open a pull request with the new value and a capacity estimate. The platform lead approves. The change takes effect at the next nightly job. The job logs the number of partitions it dropped. Check the log the morning after any change.

## Related

The decision to reduce raw retention is recorded in the retention reduction decision record. The April 2026 dashboard outage came from a mistaken value in this file.
