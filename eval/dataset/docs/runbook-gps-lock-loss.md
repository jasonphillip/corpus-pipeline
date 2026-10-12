# Runbook: GPS Lock Loss

Synthetic document, Larkspur Systems wiki (fictional).

Every node takes its time from a GPS timing module. Frame timestamps depend on it. Use this runbook when the alert `gps_lock_lost` fires.

## What the alert means

The module needs at least 6 satellites to report a lock. When the count falls below 6, the module reports `NOLOCK` and the node switches to holdover mode. In holdover, the node keeps time from its local oscillator.

## Holdover limits

Holdover is valid for 30 minutes. Clock drift during holdover is about 2 microseconds per minute. After 30 minutes the node marks every new frame with the flag `time_untrusted`. The ingest service stores these frames but the flight tracker ignores them.

## Steps

1. Run `lsctl gps status`. Note the satellite count and the fix type.
2. If the count is between 3 and 5, wait. A short sky obstruction, such as a heavy cloud deck or snow on the antenna, often clears alone.
3. If the count stays below 6 for 10 minutes, check the GPS antenna cable at the module end. Reseat the SMA connector.
4. Check the cable run for damage. Look for kinks and for cuts from animals.
5. If the count is zero, power-cycle only the GPS module with `lsctl gps reset`. This does not restart the receiver.
6. After a reset, expect a cold start. A cold start can take up to 90 seconds.

## Escalation

If there is no lock 30 minutes after the reset, mark the node as degraded in the fleet table. Open a hardware ticket. Do not leave the node in service for launches, because frames without trusted time cannot be matched to a flight.

## Almanac

The module stores its almanac in flash every 6 hours. A power cycle before the first save can leave the almanac stale and slow the next lock. A stale almanac once caused timestamp errors on several nodes. See the January 2026 incident report.

## After recovery

Confirm that `time_untrusted` frames are no longer produced. Clear the degraded mark. Note the cause in the node log.
