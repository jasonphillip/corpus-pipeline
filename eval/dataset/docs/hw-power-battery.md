# Hardware Note: Power Supply and Battery

Synthetic document, Larkspur Systems wiki (fictional).

Each site has a mains power supply unit and a backup battery. The two sit in the receiver enclosure on a DIN rail.

## Power supply unit LS-PSU-0055

- Input: 100 V to 240 V AC, 50 or 60 Hz.
- Output: 12 V DC, 3 A.
- It also charges the battery and acts as a diode-OR source, so the node runs from mains when present and from the battery when not.
- Output capacitors hold the node for about 90 seconds after both sources drop, which is the basis of the hot-swap window.
- A setting switch holds the output off after a low-battery shutdown until mains power returns and the battery reaches 30 percent. This revision has no auto-restart option.

## Battery LS-BAT-0301

- Chemistry: lithium iron phosphate.
- Nominal voltage: 12.8 V.
- Rated capacity: 20 Ah, which is 256 Wh.
- Weight: 2.6 kg.
- Charge temperature range: 0 to 45 degrees Celsius. The charger stops below 0 degrees. A built-in heater pad warms the battery in the cold.
- Discharge temperature range: minus 20 to plus 60 degrees Celsius.
- Cycle life: about 2,000 cycles to 80 percent capacity.

## Load and runtime

A bare node with the board and modem draws about 8 W. A site with the cabinet heater on draws about 22 W. At 22 W the battery runs the site for roughly 10 to 12 hours when new. Colder weather shortens this because the battery delivers less capacity.

## Low-battery behavior

The node shuts down cleanly when the battery reaches 10 percent charge. A clean shutdown saves the GPS almanac, closes the IQ buffer and writes the state directory. The battery monitor chip then cuts the load to prevent deep discharge.

An alert named `bat_low` fires at 30 percent charge. The alert `mains_lost` fires as soon as the supply loses its input.

## Monitoring

The metric `bat_voltage_v` is exported every 15 seconds. A healthy float voltage is 13.4 V to 13.6 V. A voltage under 12.0 V with mains present means the charger or the battery has failed.

## Capacity test

On the first Sunday of each month, the node runs a short discharge test and estimates capacity. The result goes to the fleet table. See the battery swap runbook for the replacement threshold.

## Surge protection

A surge protector sits on the mains input and on the antenna line. Replace the mains surge module after any lightning storm that tripped the breaker at the site.
