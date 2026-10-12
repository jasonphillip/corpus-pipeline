# Hardware Note: Cellular Modem LS-MDM-0240

Synthetic document, Larkspur Systems wiki (fictional).

The LS-MDM-0240 gives a node its uplink. It plugs into the modem slot on the receiver board. The modem supports LTE Cat 4 and falls back to a lower speed 3G network where LTE is weak.

## SIM cards

Each modem has two SIM slots. SIM 1 is the primary plan from carrier A. SIM 2 is the backup plan from carrier B. The two carriers share no towers at most sites. The modem switches to SIM 2 after 5 minutes without connectivity. It returns to SIM 1 after SIM 1 has been reachable for 30 minutes.

The access point name is `larkspur.iot` on both plans. Traffic goes through the private network to the VPN endpoint.

## Data allowance

Each node has a data cap of 30 GB per month on SIM 1. SIM 2 has a smaller cap of 5 GB per month. A typical node uses about 9 GB per month. Most of this is UDP telemetry. HTTP fallback uses about 2.4 times more data than UDP for the same frames, so a node stuck in fallback can reach the cap in less than two weeks.

Monitor the use in the fleet table field `data_used_gb`. The alert `modem_data_high` fires at 80 percent of the cap. At 100 percent the carrier throttles the line to 64 kbit/s.

## Antennas

Two small LTE antennas connect to the modem. Mount them outside the enclosure on the cabinet side, at least 0.3 m from the helical antenna.

## Status

Run `lsctl modem status`. The output shows the active SIM, the signal in dBm, the network type and the data used this month. A signal weaker than minus 105 dBm gives poor throughput.

## Power

The modem draws 1.5 W idle and 3 W peak while it sends. It is the second largest load on the board after the processor.

## Fault handling

If the modem hangs, `lsctl modem reset` power-cycles it without touching the receiver. After a reset the modem needs about 60 seconds to register on the network. Nodes keep frames in their outbox while the uplink is down, so a short outage loses no data.

## Replacement

Swap the modem only with the node off. Move the SIM cards to the new unit, and keep the SIM order.
