# Runbook: Restart a Receiver

Synthetic document, Larkspur Systems wiki (fictional).

Use this runbook when a receiver node stops decoding frames but the host still answers SSH. The receiver service is named `lsrx`. The control tool is `lsctl`.

## Before you start

Check the node first. Run `lsctl status --node <site-id>` from the ops host. If the state is `LOCKED`, the receiver is healthy and a restart is not needed. If the state is `SEARCHING` or `STALLED`, continue with the steps below.

## Steps

1. Open an SSH session to the node.
2. Run `lsctl restart receiver`.
3. Wait 45 seconds for the service to reload the SDR driver and re-tune.
4. Run `lsctl status` again. The state must read `LOCKED`.
5. Open the metrics page and confirm that the counter `rx_frames_total` increases.

## Limits

Do not restart a receiver more than 3 times in 10 minutes. Repeated restarts hide the real fault and can corrupt the IQ buffer. After the third restart, stop and page the on-call engineer.

## If the restart hangs

If `lsctl restart receiver` does not return after 120 seconds, send SIGKILL to the process named `lsrx`. Then run the restart command once more.

A hung restart usually means the USB bus lost the SDR front end. In that case, power-cycle the node with `lsctl power-cycle`, which cuts power for 10 seconds. A power cycle also clears the GPS module state, so expect a cold start. See the GPS lock-loss runbook for what to do next.

## After the restart

Write a note in the node log. Include the time, the symptom, and the state before and after the restart. Add the reason code `RX-STALL` when the state was `STALLED`. Add the reason code `RX-SEARCH` when the state was `SEARCHING`.

If the node returns to `LOCKED` but the frame counter stays flat, the fault is not in the receiver service. Check the antenna cable and the low-noise amplifier before you try another restart.

## Who may run this

Any engineer on the ops rotation may run this runbook. Trainees may run it only with the on-call engineer present. The restart is logged by the control tool, and the log entry names the user who ran it.
