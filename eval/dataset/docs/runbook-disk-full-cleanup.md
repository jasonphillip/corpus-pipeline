# Runbook: Node Disk Full

Synthetic document, Larkspur Systems wiki (fictional).

Each node stores a rolling buffer of raw IQ samples. The buffer lets engineers replay a signal after a failed decode. If the buffer grows without limit, the root disk fills and the node stops working.

## Where the data lives

The raw IQ buffer lives in `/var/lib/larkspur/iq`. Node state lives in `/var/lib/larkspur/state`. Decoded frames wait for upload in `/var/lib/larkspur/outbox`.

## Alert

The disk usage alert fires at 85 percent. The alert name is `node_disk_high`. At 95 percent the receiver service pauses recording to protect the file system.

## Steps

1. Log in to the node.
2. Check usage with `df -h /var/lib/larkspur`.
3. Check which directory is large with `du -sh /var/lib/larkspur/*`.
4. If the IQ buffer is the largest, run `lsctl purge --older-than 72h` to remove IQ files older than 72 hours.
5. Check usage again. The target is below 60 percent.
6. If usage is still above 85 percent, run `lsctl purge --older-than 24h`. Then open a ticket for the hardware lead, because the buffer is growing faster than expected.

## If the outbox is large

A large outbox means uploads are failing. Do not delete outbox files. Fix the upload path first. Check the modem and the ingest endpoint. The node uploads the backlog on its own after the path recovers.

## Never delete

Never delete the contents of `/var/lib/larkspur/state`. This directory holds the node identity, the GPS almanac copy and the calibration table. A node without it must be provisioned again from scratch.

## Prevention

The default IQ buffer cap is 12 GB. The key is `iq_buffer_gb` in the receiver config. A node with a smaller 32 GB flash card should keep the cap at 12 GB or lower. Do not raise the cap to solve a disk alert.
