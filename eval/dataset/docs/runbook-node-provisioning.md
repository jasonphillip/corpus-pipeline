# Runbook: Provision a New Node

Synthetic document, Larkspur Systems wiki (fictional).

Use this runbook to bring a new receiver node into the fleet. A new node ships without a site identity. It gets its identity during enrollment.

## Prepare the image

Flash the file `larkspur-node-2.8.4.img` to the node flash card. Use a USB stick with the flasher tool. The flash takes about 6 minutes. The image carries firmware 2.8.4 and the default receiver config.

## Create the site record

1. Open the fleet table and add a new site.
2. Set the site ID in the format `LK-<region>-<nnn>`. The region is a two-letter code. The number is three digits and is the next free number for that region.
3. Fill in the latitude, longitude and the target bearing to the launch site.
4. Select the antenna and low-noise amplifier part numbers.

## Create an enrollment token

Run `lsctl enroll token --site LK-NE-012` with the real site ID. The command prints a token. Enrollment tokens expire after 24 hours. Each token works once. Do not paste a token into a chat or a ticket. Send it through the password vault.

## Enroll the node

1. Power on the node with the cellular modem connected.
2. Run `lsctl enroll --token <token>` on the node.
3. The node contacts the admin API on port 9102, receives its certificate and writes its identity to `/var/lib/larkspur/state`.
4. Run `lsctl status`. The node should report `SEARCHING` and then `LOCKED` within 10 minutes of the antenna being connected.

## Commissioning checks

- GPS lock with at least 6 satellites.
- Mean SNR within 2 dB of the value expected for the path length.
- The first decoded frame appears on the dashboard.
- Upload through the ingest gateway returns HTTP 202.

Record the SNR as the site baseline. The baseline is used later to decide when to realign the antenna.

## If the token expired

Create a new token. The old one cannot be revived. Do not edit the state directory by hand.
