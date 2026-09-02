# 03 · Function allocation

Every vehicle function is placed by three questions, asked in order:

1. **Does it have a safety goal at ASIL B or above?** → Tier 1, wired, no link dependency. Stop.
2. **Does the user notice a delay above ~50 ms?** → Tier 1 (container partition), Tier 2 only as an accelerator with on-board fallback.
3. **Otherwise** → Tier 3, with a cached last-known-good result on board.

Legend: **T0** smart node · **T1** on-board (CCU or zone or dedicated ECU) · **T2** edge ECU farm · **T3** regional ECU farm. "Loss of link" describes behaviour when Tier 2/3 are unreachable.

## Chassis, motion and ADAS

| Function | ASIL | Placement | Latency budget | Loss of link |
|---|---|---|---|---|
| Brake-by-wire, ABS, ESC | D | T1 · dedicated brake ECU + CCU safety island | ≤ 5 ms sensor→actuator | No effect |
| Electric power steering | D | T1 · dedicated EPS ECU + safety island | ≤ 5 ms | No effect |
| Airbag / restraint deployment | D | T1 · dedicated restraint ECU | ≤ 1 ms from crash sensing | No effect |
| Vehicle motion control (torque vectoring, stability) | D | T1 · CCU safety island | ≤ 5 ms | No effect |
| ADAS perception (camera, radar, LiDAR) | B(D) | T1 · CCU high-performance partition | ≤ 30 ms frame-to-object | No effect |
| Sensor fusion and tracking | B(D) | T1 · CCU | ≤ 10 ms | No effect |
| AEB, LKA, ACC, lane change decision | D | T1 · CCU safety island | ≤ 5 ms decision cycle | No effect |
| Localisation (GNSS/IMU + map matching) | B | T1 · CCU; map tiles from T2 | ≤ 20 ms | Falls back to on-board tile cache and dead reckoning |
| Driver monitoring | B | T1 · CCU | ≤ 100 ms | No effect |
| Low-speed park assist / summon | B | T1 · CCU; parking-lot map hints from T2 | ≤ 50 ms | Works with on-board sensors only; loses lot-level guidance |
| Cooperative perception (V2X objects) | QM, decomposed | T2 → hint channel into Decide | ≤ 100 ms to be useful | Silently absent; on-board perception unaffected |
| HD map ahead-of-vehicle streaming | QM | T2 | ≤ 500 ms | Uses cached tiles; degrades to SD map |

## Powertrain and energy

| Function | ASIL | Placement | Latency budget | Loss of link |
|---|---|---|---|---|
| Engine / hybrid control | C/D | T1 · dedicated ECU on a zone | ≤ 5 ms | No effect |
| Traction inverter control | D | T1 · dedicated inverter ECU | ≤ 1 ms current loop | No effect |
| Battery management (cell balancing, contactors, thermal) | C/D | T1 · dedicated BMS | ≤ 10 ms | No effect |
| On-board charging, DC-DC | B/C | T1 · dedicated ECU | ≤ 10 ms | No effect |
| Route-aware energy strategy (predictive hybrid strategy, pre-conditioning plan) | QM | T2 proposes; T1 applies within safe envelope | ≤ 1 s | Reverts to on-board default strategy |
| Range prediction and charging-stop planning | QM | T3 | seconds | Last plan cached on board |
| Powertrain optimisation learning (fleet) | QM | T3 → OTA calibration | offline | n/a |

## Body and comfort

| Function | ASIL | Placement | Latency budget | Loss of link |
|---|---|---|---|---|
| Exterior lighting (head/tail/brake/turn) | B | T1 · zone, wired | ≤ 20 ms | No effect |
| Door locks, latches, child lock | A | T1 · zone, wired | ≤ 50 ms | No effect |
| Windows, sunroof, tailgate (pinch protection) | A | T1 · zone; motor node wired | ≤ 50 ms | No effect |
| Seat position, heating, massage | QM | T0 wireless node → zone | ≤ 100 ms | No effect |
| Cabin climate control loop | QM | T1 · zone; setpoints from HMI | ≤ 200 ms | No effect; loses remote pre-conditioning |
| Ambient lighting, rear-seat displays | QM | T0 wireless (Wi-Fi 6) → CCU IVI | ≤ 100 ms | No effect |
| Tyre pressure monitoring | A | T0 wireless (already RF today) → zone | seconds | No effect |
| Occupant / child presence detection | B | T0 UWB → zone | ≤ 500 ms | No effect |
| Left-belongings notification | QM | T0 UWB/BLE → CCU → T3 push | seconds | Local chime only |
| Digital key (UWB/BLE/NFC, CCC Digital Key 3.0) | A + security | T1 · gateway/zone; credential back-end in T3 | ≤ 300 ms unlock | Works with provisioned keys; no new-key issuance |

## Infotainment, connectivity and services

| Function | ASIL | Placement | Latency budget | Loss of link |
|---|---|---|---|---|
| HMI rendering, cluster, warnings | QM (warnings ASIL B path separate) | T1 · CCU IVI partition; ASIL warnings drawn by safety island overlay | ≤ 50 ms input→pixel | No effect |
| Navigation routing and search | QM | T3; on-board fallback router | ≤ 1 s | On-board SD-map routing |
| Voice assistant (wake word on board, NLU in farm) | QM | T1 wake word; T3 NLU | ≤ 700 ms | Command grammar on board only |
| Media streaming, app store | QM | T3 | best effort | Local media only |
| In-vehicle payments at fuel/charging | QM + security | T3 with T1 secure element | seconds | Unavailable |
| Software-unlockable hardware / feature entitlements | QM + security | T3 issues signed entitlements; T1 enforces | minutes | Cached entitlements remain valid to expiry |

## Diagnostics, updates and fleet

| Function | ASIL | Placement | Latency budget | Loss of link |
|---|---|---|---|---|
| UDS diagnostics (legal access) | QM | T1 over DoIP; mirrored to T3 | ≤ 50 ms per request | Local only |
| Telemetry and event logging | QM | T1 buffer → T3 | minutes | Store-and-forward on board |
| Predictive maintenance, warranty analytics | QM | T3 digital twin | offline | n/a |
| OTA download and staging | QM | T3 campaign → T1 A/B partitions | hours | Resumes when connected |
| OTA activation | QM, gated | T1 only, vehicle parked and safe state | n/a | n/a |
| Intrusion detection sensors | QM | T1 gateway and CCU | ≤ 100 ms local response | Local blocking rules remain |
| Fleet IDS correlation, SOC | QM | T3 | minutes | n/a |
| Model training and validation | QM | T3 | offline | n/a |
| Workload placement (proposer / arbiter) | QM proposer, B arbiter | T2 proposes; T1 arbiter decides | ≤ 1 s to re-place | Arbiter pins all workloads on board |

## What moved off the vehicle

Compared with the baseline, the following historically had their own ECU or head-unit compute and are now farm-hosted with a thin on-board client: navigation back-end, voice NLU, telematics application server, diagnostics analytics, HD map server, fleet and driver-behaviour analytics, entitlement server, OTA orchestration, payments, owner-app back-end, digital-twin simulation. None of them was ever in an ASIL path, which is precisely why they can go.
