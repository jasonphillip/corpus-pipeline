# Incident 2026-06-05: False Low-SNR Alerts

Synthetic document, Larkspur Systems wiki (fictional).

Severity: SEV3. Duration: one night, 7 hours.

## Summary

Between 21:00 UTC on 2026-06-04 and 04:00 UTC on 2026-06-05 the alert `rx_snr_low` fired over and over, while nodes were healthy. A total of 63 false alerts reached the pager. The on-call engineer was woken 11 times.

## Impact

No data was lost. The cost was engineer time and trust in the alert. By the end of the night the on-call engineer had silenced the alert for the whole fleet for 4 hours, which hid one real fault at site LK-MW-004 for part of that period.

## Timeline

- 2026-06-04 21:00 UTC: a rule change from the previous week took effect for the nightly low-traffic period.
- 2026-06-04 22:10 UTC: the first page arrived.
- 2026-06-05 01:30 UTC: the on-call engineer created a fleet-wide silence.
- 2026-06-05 05:40 UTC: the engineer compared the SNR graphs of paged nodes and saw clean data.
- 2026-06-05 09:00 UTC: the platform team found the rule error.

## Root cause

The rule averaged SNR over all frames in a 10 minute window. At night few balloons fly and a node hears mostly noise frames between flights. The mean of the window dropped below the limit even though every real flight had good SNR. The rule should have used the median over frames above the squelch level.

The threshold stayed at 8 dB. The problem was the statistic, not the number. Raising the threshold would have hidden real faults.

## Fix

The rule now uses the median of frames that passed squelch. It also requires at least 20 such frames in the window. If a window has fewer frames, the rule does not evaluate. The new rule was tested against 7 days of stored metrics, and it produced zero alerts on healthy nodes and fired on the two known faulty nodes.

## Follow-up actions

1. Review the other rules for the same statistic problem. Done, one more rule changed.
2. Limit a single silence to one site unless the platform lead approves. Done, see the alert runbook.
3. Add rule tests to the config repository CI. Owner: platform team.

## Lessons

A silence taken for convenience can hide a real fault. The silence runbook now limits matches and duration.
