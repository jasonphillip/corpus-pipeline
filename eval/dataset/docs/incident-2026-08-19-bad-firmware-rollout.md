# Incident 2026-08-19: Firmware 2.8.2 Boot Loop

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV2. Duration: 6 hours 30 minutes, with site visits for two nodes.

## Summary

Firmware 2.8.2 was rolled out on Tuesday 2026-08-18 during the update window. On 2026-08-19, 6 nodes with board revision B entered a boot loop. The other nodes in the rollout ran normally. The rollout had reached 31 nodes when it was stopped.

## Impact

The 6 nodes were offline for between 4 and 30 hours. Four recovered by remote rollback. Two needed a site visit because they could not reach the bootloader remotely. Launch coverage dropped, and the standby groups were used at three sites.

## Timeline

- 2026-08-18 02:10 UTC: the rollout began, with no stage limits.
- 2026-08-18 03:40 UTC: 31 nodes had updated. The rollout script paused on its own at the end of the window.
- 2026-08-19 05:50 UTC: the first node failed to report. Two more followed.
- 2026-08-19 06:30 UTC: the on-call engineer saw a repeating boot pattern in the serial log of a test node.
- 2026-08-19 08:00 UTC: the team halted the rollout and ran `lsctl fw rollback` on all reachable nodes.
- 2026-08-19 12:30 UTC: 4 of the 6 nodes were back.

## Root cause

Board revision B uses a different flash part than revision A. The driver in firmware 2.8.2 did not support the new flash part. A node that rebooted after the update could not read its configuration and started again. The update itself succeeded, so the node reported the new version before the first reboot. The reboot came at the next power event, which explains the delay of one day.

## Fix

Firmware 2.8.3 added the driver. Firmware 2.8.4 fixed a related timeout. The team added board revision B nodes to the test bench.

## Follow-up actions

1. Use a staged rollout with a soak period. Decision recorded in the staged firmware gate record.
2. Add one revision B node to every first stage. Done.
3. Make the bootloader reachable remotely on revision B. Owner: hardware team.

## Lessons

An update that reports success has not yet shown that the node survives a reboot. The health check now includes a forced reboot during the soak.
