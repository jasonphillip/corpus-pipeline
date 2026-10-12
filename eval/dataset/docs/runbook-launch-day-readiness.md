# Runbook: Launch Day Readiness Check

Synthetic document, Larkspur Systems wiki (fictional).

Balloons launch twice a day, at 00:00 and 12:00 UTC. The ops engineer on duty runs this check before each launch. The check takes about 15 minutes.

## Timing

Start the readiness check 60 minutes before each launch. The launch coordinators publish the predicted frequency list as the file `launch_freqs.csv` at 90 minutes before launch. If the file is missing at 60 minutes, call the launch coordinators on the shared line.

## Checklist

1. Load the frequency list into the fleet with `lsctl freq load launch_freqs.csv`.
2. Run `lsctl fleet check`. It prints one line per node.
3. Four of four channels must report `LOCKED` on each node that is assigned to the launch.
4. Confirm that the GPS satellite count is at least 6 on those nodes.
5. Confirm that no node is in maintenance mode, unless the ticket says so.
6. Open the dashboard and confirm that the queue depth is under 4,000 messages.
7. Check the weather page for wind above 60 km/h at the launch site. Strong wind pushes balloons out of the antenna beam early.

## Nodes assigned to a launch

Each launch is assigned a primary group of 4 receiver nodes and a standby group of 2. The primary group must pass every check. The standby group needs only the GPS check and a `LOCKED` state.

## Change freeze

No deploys run during launch windows. See the change management page for the exact hours. If a deploy is still running at 60 minutes before launch, ask the owner to pause it.

## Go and no-go

Post `GO` in the ops channel when every check passes. Post `NO-GO` with the failing line when one fails. A NO-GO on a primary node triggers the standby group. The ops engineer then tells the launch coordinators which nodes are active.

## After the launch

Watch the first 10 minutes of telemetry. The first frames normally arrive within 2 minutes of launch. If no frames arrive after 5 minutes, call the launch coordinators and confirm the balloon is airborne before you debug the fleet.
