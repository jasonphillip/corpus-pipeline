# Runbook: Realign a Receiver Antenna

Synthetic document, Larkspur Systems wiki (fictional).

Each site uses one helical antenna, part LS-ANT-0090, on a fixed mast. Wind and ice can shift the mount over time. Use this runbook to check and correct the aim.

## When to realign

Realign when mean SNR drops more than 4 dB below the site baseline for 3 launches. The site baseline is stored in the site record and is set at commissioning. Do not realign for a single bad launch. A single bad launch is usually a balloon that drifted outside the antenna beam.

## Target values

Azimuth must be within 5 degrees of the target bearing. Elevation must be within 3 degrees of the target elevation, which is 15 degrees for most sites. The target bearing is the direction of the launch site from the receiver. It is listed in the site record.

## Tools

- A handheld compass or the surveyed bearing marks on the mast base.
- A digital inclinometer.
- A 13 mm wrench.
- Two people, because the mast is 4 m tall.

## Procedure

1. Read the current mean SNR from the dashboard and write it down.
2. Loosen the mast clamp bolts by half a turn. Do not remove them.
3. Rotate the antenna to the target bearing. Use the bearing marks on the mast base.
4. Set the elevation with the inclinometer on the antenna boom.
5. Tighten the mast clamp bolts to 12 newton-metres.
6. Wait 15 minutes. Read the mean SNR again.

## Acceptance

The new mean SNR must be within 2 dB of the site baseline. If it is not, check the coax cable run for water ingress and check the low-noise amplifier bias. Page the hardware lead if both are clean and the SNR is still low.

## Safety

Do not work on the mast in wind above 40 km/h. Do not work on the mast during a thunderstorm. Wear a hard hat when the mast is taller than 3 m.
