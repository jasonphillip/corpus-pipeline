# Incident 2025-11-03: UDP Packet Loss

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV2. Duration: 3 hours 20 minutes. Detected 14:05 UTC, resolved 17:25 UTC.

## Summary

The receivers send frames to the UDP listener on port 5140. On 2025-11-03 the listener dropped a large share of datagrams. UDP loss reached 14 percent on 11 of 64 nodes. The affected nodes were all in the northeast and midwest regions.

## Impact

Frame arrival at the ingest gateway fell for the 11 nodes. Two launches saw gaps of 20 to 40 seconds in the flight track. No data was lost permanently, because affected nodes also keep frames in their outbox and later uploaded them by HTTP. The delay in the final tracks was up to 3 hours.

## Timeline

- 14:05 UTC: the alert `udp_loss_high` fired for the first node.
- 14:30 UTC: the on-call engineer saw that 11 nodes showed the same loss pattern.
- 15:10 UTC: network team found no loss in the core switches.
- 16:00 UTC: the engineer compared kernel counters on the gateway and saw `RcvbufErrors` rising by about 900 per second.
- 16:40 UTC: the engineer raised the receive buffer on gateway `gw-01`.
- 17:25 UTC: loss returned to under 0.1 percent on all nodes.

## Root cause

The kernel receive buffer on the gateway hosts was left at the default of 212992 bytes. A new firmware release in October made nodes send frames in bursts of 40 datagrams instead of 10. The burst overflowed the socket buffer. Packets were dropped before the listener could read them.

## Fix

The team set `net.core.rmem_max` to 8388608 and set the listener socket option to request the larger buffer. The change was added to the host configuration so it survives a reboot.

## Follow-up actions

1. Add the buffer setting to the gateway host image. Owner: platform team.
2. Add an alert on `RcvbufErrors` above 50 per second. Owner: platform team.
3. Test every burst size change against a gateway under load before release. Owner: firmware team.

## Lessons

The loss alert fired on node counts, not on gateway counters. Gateway counters would have pointed to the cause an hour earlier.
