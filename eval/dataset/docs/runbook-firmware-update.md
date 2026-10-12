# Runbook: Roll Out a Firmware Update

Synthetic document, Larkspur Systems wiki (fictional).

The current production firmware is version 2.8.4. Each node has two firmware slots, A and B. An update writes to the inactive slot and boots from it. The old slot stays intact for rollback.

## Update window

Apply updates only on Tuesdays between 02:00 and 04:00 UTC. This window is far from the launch windows at 00:00 and 12:00 UTC. Do not start a rollout outside the window without approval from the platform lead.

## Rollout stages

The fleet has 64 nodes. A rollout runs in three stages with a soak period between them.

- Stage 1 covers 5 nodes. The platform lead picks them. At least one must have board revision B.
- Stage 2 covers 20 nodes.
- Stage 3 covers all remaining nodes.

Wait 24 hours between stages. During the soak, watch the health checks on the dashboard.

## Commands

1. Stage the image: `lsctl fw stage --version 2.8.4 --nodes @stage1`.
2. Apply it: `lsctl fw apply --nodes @stage1`.
3. Check the result: `lsctl fw status --nodes @stage1`. Every node must report the new version and the state `LOCKED`.

## Health check

A node passes when it is `LOCKED` within 10 minutes of the reboot, reports a GPS lock, and uploads frames at the normal rate. If more than one node in a stage fails, stop the rollout.

## Rollback

Run `lsctl fw rollback --nodes <list>`. The command boots the node from slot B, which holds the previous version. Rollback takes about 4 minutes per node. Nodes that fail to boot at all need a site visit, because slot B is reachable only through the bootloader.

## After the rollout

Update the fleet table with the new version. Close the change ticket. Post a short note in the ops channel with the stage results.

The staged gate was added after the August 2026 boot loop. See the decision record on the staged firmware gate.
