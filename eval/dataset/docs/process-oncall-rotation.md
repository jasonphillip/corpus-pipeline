# Process: On-Call Rotation

Synthetic document, Larkspur Systems wiki (fictional).

The platform team runs a 24 hour on-call rotation. One engineer is primary. A second is secondary. This page explains how it works.

## Shifts

A rotation lasts one week. Handoff happens on Mondays at 09:00 UTC. The outgoing engineer writes a handoff note in the ops channel. The note lists open incidents, silences, nodes in degraded state and any planned work for the week.

The team has 8 engineers in the rotation. Each takes the primary role about once every 8 weeks, and the secondary role in the week before.

## Paging

Pages come from the alert manager through the pager service. The pager calls first and then sends a message.

Acknowledge a page within 10 minutes. After 15 minutes without acknowledgement, the page goes to the secondary engineer. After 25 minutes it goes to the platform lead.

A page you cannot handle is not a failure. Hand it to the secondary and write down why.

## Expected response

For a SEV1, start work at once and post in the ops channel within 15 minutes. For a SEV2, start within 30 minutes. For a SEV3, handle it during working hours unless it affects an approaching launch.

## Tools

You need three things on your phone: the pager app, the ops channel and a VPN client. Test them on the Friday before your shift. You also need your laptop and a mobile hotspot.

## Swapping

You may swap a shift with another engineer. Both of you post in the ops channel and tell the platform lead. A swap must not leave a week without a trained primary.

## Compensation

On-call time is paid at a flat weekly rate. Pages outside 08:00 to 20:00 local time earn compensatory time off. Take it within 30 days.

## Quiet weeks

Use quiet time to improve alerts and runbooks. Every alert that fires without needing action is a bug. File a ticket to fix or remove it.

## Escalation list

First the secondary engineer. Then the platform lead. Then the head of engineering. For hardware faults at a site, call the hardware lead directly.
