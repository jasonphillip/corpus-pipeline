# Runbook: Silence and Tune Alerts

Synthetic document, Larkspur Systems wiki (fictional).

Alerts reach the on-call engineer through the pager. Sometimes an alert is known and expected. A silence stops pages for a set time. Use silences with care, because a forgotten silence hides real faults.

## Alerts you will see often

- `rx_snr_low` fires when SNR stays below 8 dB for 10 minutes.
- `gps_lock_lost` fires when the satellite count falls below 6.
- `node_disk_high` fires at 85 percent disk use.
- `queue_depth_high` fires at 50,000 messages.
- `cert_expiry_warn` fires when 14 days remain on a certificate.

## Creating a silence

1. Open the alert manager page.
2. Find the alert and click Silence.
3. Match on the alert name and the site label, for example `site="LK-SW-003"`. Do not silence by alert name alone, because that mutes the alert for the whole fleet.
4. Set the duration. A silence may last at most 4 hours. The tool refuses longer values.
5. Write a reason and the ticket number in the comment field.

## When silence is allowed

- Planned maintenance on a node with maintenance mode on.
- A known hardware fault that already has a ticket.
- A confirmed false alert while the rule is being fixed.

Do not silence `gps_lock_lost` before a launch. Do not silence any alert to get a quiet night.

## Renewing

If the cause is not fixed in 4 hours, create a new silence and update the ticket with the reason. Repeat no more than twice. After the third silence, the platform lead must approve the next one.

## Changing a threshold

Thresholds live in the alert rules file in the config repository. Change the rule through a pull request, not on the alert manager page. A rule change needs one review from the platform team. Test a new rule against the last 7 days of metrics before merging.

## Audit

The platform lead reviews all active silences each Monday during the on-call handoff.
