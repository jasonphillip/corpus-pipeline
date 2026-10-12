# Runbook: Swap a Site Battery

Synthetic document, Larkspur Systems wiki (fictional).

Each site has one backup battery, part LS-BAT-0301. It keeps the node running when mains power fails. Batteries age. Use this runbook to replace one.

## When to swap

Replace the battery when measured capacity falls below 70 percent of rated capacity. The rated capacity is 20 Ah. A capacity test runs automatically on the first Sunday of each month. The result appears in the fleet table as `bat_capacity_pct`. Also replace the battery if it is more than 5 years old, or if the case is swollen.

## Parts and tools

- One new LS-BAT-0301, charged to at least 90 percent.
- A 10 mm socket and a torque screwdriver.
- Insulated gloves.
- A labelled bag for the old battery.

## Procedure

1. Tell the ops channel which site you will work on.
2. Check that mains power is present. Swap at a site with mains power only. If mains is down, wait.
3. Set the node to maintenance mode with `lsctl maintenance on`. This suppresses power alerts.
4. Switch off the battery breaker.
5. Disconnect the negative terminal first. Then disconnect the positive terminal.
6. Lift out the old battery. It weighs 2.6 kg.
7. Place the new battery. Connect the positive terminal first. Then connect the negative terminal.
8. Tighten the terminal bolts to 4 newton-metres.
9. Switch on the breaker.

## Hot swap

The power supply unit keeps the node alive for a short time on its output capacitors. The hot-swap window is 90 seconds. Use it only if the node cannot be taken offline. Stay well inside the window. If you pass it, the node reboots and runs through a cold start.

## Verify

Run `lsctl power status`. The battery voltage must be between 12.6 V and 13.4 V. Turn maintenance mode off with `lsctl maintenance off`. Record the new battery serial number in the fleet table. Dispose of the old battery through the approved recycling bin at the office, never in site waste.
