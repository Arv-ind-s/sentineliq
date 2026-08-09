# SentinelPdM demo notes

Generated deterministically with seed `42`. Replay origin `t0` is day 56
(`2025-02-26T00:00:00+00:00` UTC). These are the expected, model-free signal facts; the later model-integrity
check appends the measured alert crossing after training.

## Planted scenarios

- **PUMP-07**: `bearing_wear` begins about 7.5 plant-hours after `t0`. Vibration RMS ramps
  from its healthy baseline to more than four times baseline before the labelled failure.
- **PRESS-03**: `sensor_drift` raises a fault-shaped signal but has no failure event. It is
  the deliberate false-positive trap and should be handled as data-quality evidence.
- **CNC-11**: `seal_leak` is planted while its dominant historical code is `PMP-02`, so
  sensor evidence and maintenance history conflict.
- **CONV-14**: every sensor channel is unavailable for one contiguous eight-hour gateway
  outage. The correct behaviour is to withhold a prediction for that interval.

The generator's `verify_signal()` checks only persisted, model-free facts and raises
`DemoIntegrityError` when any fact is missing. It does not depend on a trained model.

## Model-integrity measurement

- PUMP-07 fused risk crossed tau=0.420000 at 52.3s of the 600x replay.
