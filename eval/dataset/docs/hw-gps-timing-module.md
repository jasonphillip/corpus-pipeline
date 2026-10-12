# Hardware Note: GPS Timing Module LS-GPS-0150

Synthetic document, Larkspur Systems wiki (fictional).

The LS-GPS-0150 supplies the time base for every frame timestamp. It connects to the receiver board by a serial link and a pulse-per-second (PPS) line.

## Specifications

- Constellations: GPS and one second system, chosen in the config.
- PPS accuracy is 50 ns, 1 sigma, with a lock on at least 6 satellites.
- Cold start to first fix: up to 90 seconds. Warm start with a valid almanac: about 30 seconds. Hot start: about 3 seconds.
- Antenna: active, 3.3 V bias from the module. Maximum cable length is 10 m with low-loss cable.
- Connector: SMA female on the board side.
- Power: 0.3 W typical.
- Oscillator: temperature compensated, with a stability of 0.5 ppm.

## Holdover

The module holds time for 30 minutes without satellites, in the sense that the drift stays below the timestamp budget of 100 microseconds. After 30 minutes the node marks frames as untrusted. The drift in holdover depends on temperature. A fast temperature swing of more than 5 degrees per hour makes the drift larger.

## Almanac storage

The module keeps its almanac in flash. Firmware 2.7.1 and later saves it every 6 hours. A battery backed memory would be better, but the board has no coin cell.

## Placement

Place the GPS antenna with a clear view of the sky. It needs a metal ground plane of at least 70 mm across. Keep it more than 1 m from the helical antenna, because the strong transmitter signals in the 400 MHz band can overload the GPS front end if the two sit too close.

## Diagnostics

Read the state with `lsctl gps status`. The output shows the fix type, the satellite count, the almanac age and the PPS status. The PPS line is also visible as a metric on the exporter.

## Replacement

A failed module is replaced as a unit. The new module needs a cold start and about 15 minutes to download the full almanac. Do not use the node for launches until the almanac age is under one day.
