# Decision Record 0019: Staged Firmware Gate

Synthetic document, Larkspur Systems wiki (fictional).

Status: accepted. Date: 2026-08-26. Deciders: platform lead, firmware lead, hardware lead.

## Context

Firmware 2.8.2 went to 31 nodes without a gate and put 6 revision B nodes into a boot loop. Two needed a site visit. The rollout script updated nodes in id order and stopped only at the end of the update window. See the incident report of 2026-08-19.

## Options considered

1. Keep the old process and be more careful. Rejected, because it relies on memory.
2. Update a fixed number of nodes per day. Rejected, because the nodes differ by hardware, and an unlucky first group hides the problem.
3. Staged rollout with groups chosen by hardware and a health gate between stages.

## Decision

All firmware rollouts use three stages with a soak period.

- Stage 1 covers 5 nodes. At least one must be board revision B and at least one must be revision A. At least one must sit at a site with the cabinet heater on.
- Stage 2 covers 20 nodes.
- Stage 3 covers all remaining nodes.

Each stage soaks for 24 hours before the next one starts. During the soak, a script reboots each updated node once, to prove that the node survives a restart. The health check then looks for the state `LOCKED`, a GPS lock and a normal frame rate.

The rollout tool aborts the stage if more than 1 node in the stage fails the health check. An abort sends a page and runs rollback on the failed nodes.

## Consequences

- A full rollout now takes three days, not one night. Urgent security fixes may skip the soak with the approval of the platform lead and the firmware lead.
- The test bench holds one node of each board revision. A build must pass on the bench before stage 1.
- The rollout tool no longer starts outside the Tuesday window.

## Review

Review after five rollouts or in February 2027, whichever comes first. If no stage failed in five rollouts, consider shortening the soak to 12 hours.
