# Incident 2026-01-22: Wrong Timestamps on Nine Nodes

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV3. Duration: 2 days before detection, 5 hours to fix.

## Summary

On 2026-01-22 the flight tracker flagged a launch track with a jump of more than 2 seconds between two nodes that heard the same balloon. The team found that timestamps on 9 nodes were off by up to 2.3 seconds. The other 55 nodes were correct.

## Impact

The tracker merges frames from several nodes by timestamp. Frames from the 9 nodes landed out of order. The merged track showed the balloon moving backward for short stretches. Position data was still usable, but the ascent rate chart for two flights was wrong. The two flights were re-processed after the fix.

## Timeline

- 2026-01-20: a power outage at a regional hosting site rebooted 9 nodes.
- 2026-01-22 09:40 UTC: the tracker flagged the track jump.
- 2026-01-22 10:30 UTC: the engineer saw that all 9 nodes had restarted on 2026-01-20.
- 2026-01-22 11:15 UTC: `lsctl gps status` on one node showed a lock with the flag `almanac_age` at 41 days.
- 2026-01-22 14:50 UTC: a firmware patch reached the 9 nodes.

## Root cause

The cause was a stale almanac after a power cycle. The GPS module kept its almanac in volatile memory and wrote it to flash only on a clean shutdown. After the unclean power loss, the module started from a 41 day old copy. It locked on satellites but used an outdated clock correction. The error was small enough to avoid the `gps_lock_lost` alert.

## Fix

Firmware 2.7.1 saves the almanac to flash every 6 hours. It also checks `almanac_age` after boot and sets `time_untrusted` when the age is above 14 days. The patch was applied to the 9 nodes first and then to the fleet during the normal Tuesday window.

## Follow-up actions

1. Add an alert on `almanac_age` above 14 days. Done.
2. Add a timestamp consistency check between nodes in the tracker. Owner: data team.
3. Review which other components keep state in volatile memory. Owner: firmware team.

## Lessons

A lock does not prove correct time. The tracker now compares timestamps across nodes and reports a skew above 500 milliseconds.
