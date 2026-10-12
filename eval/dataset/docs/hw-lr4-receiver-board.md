# Hardware Note: LR-4 Receiver Board (LS-RX-0412)

Synthetic document, Larkspur Systems wiki (fictional).

The LR-4 is the compute and radio board in every node. The part number is LS-RX-0412. The board hosts the SDR front end connector, the GPS module connector, the cellular modem slot and a quad-core ARM processor.

## Specifications

- Memory: 4 GiB of RAM.
- Storage: a 32 GB flash card, with a separate 4 MB boot flash that holds the bootloader.
- Input power: 9 V to 15 V DC.
- Typical power draw is 6.5 W. The peak during a cold GPS start is 9 W.
- Operating temperature: minus 30 to plus 60 degrees Celsius.
- Interfaces: two USB 3 ports, one gigabit Ethernet port, one serial console header, one SMA connector for the GPS antenna.

## Revisions

Two board revisions are in the field.

- Revision A: 41 boards. Uses the flash part from the first supplier.
- Revision B: 23 boards. Uses a different flash part from a second supplier, after the first supplier ran short. Firmware 2.8.2 does not support the flash part on revision B. Firmware 2.8.3 and later do.

The revision is printed on the board in white ink near the console header. It is also reported by `lsctl hw info` as `board_rev`. The fleet table lists the revision for each node.

## Thermal notes

The processor throttles above 85 degrees Celsius. In a closed enclosure at 40 degrees outside the board reaches about 62 degrees, so throttling is rare. The enclosure heater is not on the board. It is controlled by a separate thermostat.

## Serial console

Connect at 115200 baud, 8N1. The console shows the bootloader and kernel messages. It is the only way to read a boot loop. Hardware staff keep a USB serial cable in every site visit kit.

## Known issues

- The USB 3 port next to the Ethernet port can lose the SDR front end when a cable is bumped. A restart does not clear this. Power-cycle the node.
- The Ethernet port is unused at most sites, because they use the cellular modem.

## Spares

The stock room holds 6 spare boards of revision B and none of revision A. Replacing a revision A board with a revision B board needs firmware 2.8.3 or later on the spare.
