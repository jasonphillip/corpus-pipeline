# Hardware Note: Low-Noise Amplifier LS-LNA-0133

Synthetic document, Larkspur Systems wiki (fictional).

The low-noise amplifier (LNA) sits at the antenna, before the coax cable run. It lifts weak balloon signals above the noise of the cable and the SDR front end.

## Specifications

- Gain: 22 dB at 404 MHz.
- The noise figure is 0.6 dB.
- Passband: 400 MHz to 406 MHz. A bandpass filter inside the unit rejects signals outside this range.
- Bias: 5 V through a bias tee on the coax. The bias tee is in the receiver enclosure.
- Bias current: 28 mA typical.
- Input compression point: minus 12 dBm. Strong signals near the launch site can reach this level.
- Connectors: N-type on the antenna side, SMA on the cable side.
- Housing: die cast, rated IP67.

## Placement

Mount the LNA within 0.5 m of the antenna feed point. Use a short jumper with a low-loss cable. Every dB of loss before the LNA adds directly to the system noise figure.

## Failure signs

- Mean SNR falls by 20 dB or more at all channels at once. The LNA may have lost bias or failed.
- Bias current reads zero. The bias tee or the cable has an open circuit.
- Bias current reads above 60 mA. The LNA has a short, so switch off the bias at once.

Read the bias current with `lsctl rf bias`. The tool reports it in milliamps.

## Replacement

Switch off the bias with `lsctl rf bias off` before you disconnect the unit. Connecting a powered LNA to a bad load can destroy it. Seal the N-type connector with self-amalgamating tape and a layer of vinyl tape. Water in the connector is the most frequent cause of slow SNR decay.

## Spares and cost

The stock room keeps 10 spare units. The unit cost is low compared with a site visit, so replace a doubtful LNA rather than test it on the mast.

## Revision note

The first lot had a passband from 395 MHz to 410 MHz. The 2025 lot narrowed it to reject a paging transmitter near two sites. Lots can be told apart by the date code on the label. A date code before 2501 means the old filter.
