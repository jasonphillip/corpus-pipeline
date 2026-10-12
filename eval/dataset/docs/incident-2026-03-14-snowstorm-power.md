# Incident 2026-03-14: Snowstorm Power Loss at LK-NE-007

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV3. Site LK-NE-007 was offline for 19 hours 40 minutes.

## Summary

A snowstorm cut mains power to site LK-NE-007 at 02:20 UTC on 2026-03-14. The backup battery carried the node through the outage. The node shut down when the charge fell too low. Power returned about 31 hours after the first loss.

## Impact

The site missed the launch at 00:00 UTC on 2026-03-15. Neighbouring sites covered the flight at lower signal quality. No data from other sites was affected.

## Timeline

- 02:20 UTC: mains failed. The node switched to battery. The alert `mains_lost` fired.
- 02:35 UTC: the on-call engineer confirmed the outage with the hosting provider.
- 08:00 UTC: roads to the site were closed. No generator could be delivered.
- 14:00 UTC: the node shut down. The battery carried the site for 11 hours 40 minutes before the 10 percent shutdown threshold.
- 2026-03-15 09:10 UTC: mains returned. The node did not restart on its own.
- 2026-03-15 09:40 UTC: the host contact power-cycled the breaker and the node booted.

## Root cause

Two causes combined. The mains outage lasted longer than the battery runtime. The cabinet heater drew most of the battery energy in the cold. The site load with heater and modem was 22 W, not the 12 W assumed in the sizing sheet.

The node also did not boot after power returned, because the power supply was set to hold the node off after a low battery shutdown. That setting protects the battery from deep discharge. It has no auto-restart option on this supply revision.

## Fix

The sizing sheet now uses 22 W for sites with a heater. Northern sites get a second battery. The host contacts at all sites received a one page card that says how to power-cycle the breaker.

## Follow-up actions

1. Add a second battery at the 9 northern sites. Owner: hardware lead.
2. Add a remote power-on relay to the site design. Owner: hardware team.
3. Alert at 30 percent battery charge, not only at shutdown. Owner: platform team.
