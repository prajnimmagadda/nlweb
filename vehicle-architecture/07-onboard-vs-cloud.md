# 07 · On-board vs cloud, one line each

Test: cloud only if no safety goal at ASIL B+, delay ≥ 50 ms is acceptable or cacheable, and it degrades gracefully without a link.

## On board

| Feature | Why |
|---|---|
| Brake, ABS, ESC | ASIL D, ≤ 5 ms loop |
| Electric power steering | ASIL D, ≤ 5 ms loop |
| Airbag / restraints | ASIL D, ≤ 1 ms, must fire mid-crash |
| Vehicle motion control | ASIL D, ≤ 5 ms |
| ADAS perception and fusion | ASIL B(D), raw sensor bandwidth stays local |
| ADAS decisions (AEB, LKA, ACC) | ASIL D, ≤ 5 ms |
| Driver monitoring | ASIL B, ≤ 100 ms |
| Localisation (map matching) | ASIL B, must work with no link |
| Engine / hybrid / inverter control | ASIL C/D, 1–5 ms loops |
| Battery management | ASIL C/D, contactor and thermal protection |
| Charging and DC-DC control | ASIL B/C, high-voltage safety |
| Exterior lighting | ASIL B, legal requirement |
| Locks, windows, pinch protection | ASIL A, must work anywhere |
| Cabin climate loop | Comfort loop, no reason to leave |
| HMI cluster, warnings, controls | ≤ 50 ms input to pixel; warnings ASIL B |
| Wake-word detection | Always-on, private, ≤ 50 ms |
| Digital key ranging and unlock | Must work in garages with no signal |
| Child presence detection | ASIL B |
| Tyre pressure monitoring | ASIL A, already RF on board |
| UDS diagnostics access | Legally required local access |
| Intrusion detection sensors | Must block in real time |
| OTA activation gate | Only the car knows it is parked and safe |
| Workload arbiter | Final authority when the link drops |

## Cloud (edge or regional ECU farm)

| Feature | Why |
|---|---|
| Navigation routing, search, traffic | Seconds are fine; SD-map fallback on board |
| Voice NLU and dialogue | ≤ 700 ms is fine; big models |
| HD map serving and building | Data heavy, fleet sourced |
| Route-aware energy strategy | Seconds; needs fleet and weather data |
| Charging-stop and range planning | Seconds; external data |
| Cooperative perception fusion (V2X) | Advisory hints only |
| Telematics apps, trip logs, eco-coaching | Not time critical |
| Driver-behaviour and insurance scoring | Offline analytics |
| Fleet management | Multi-vehicle by nature |
| Diagnostics analytics, predictive maintenance | Offline, fleet correlation |
| Digital twin | Simulation, no real-time need |
| OTA campaign and image staging | Hours; needs fleet view |
| Feature entitlements, unlockable hardware | Signed once, cached on board |
| Payments | Needs bank back-end anyway |
| Owner app, remote commands | Phone to cloud to car by nature |
| Media, app store, profiles | Content lives in the cloud |
| Dashcam / sentry clip analytics | Upload clips; heavy vision offline |
| Cabin analytics for personalisation | Non-safety, asynchronous |
| Fleet learning, model training | Needs fleet data and GPUs |
| Digital key issuance, sharing, revocation | Account operation |
| Security operations, certificate lifecycle | Fleet-wide correlation |
| Workload placement proposer | Needs fleet model; arbiter guards it |
| AR head-up and 3D map content rendering | Heavy GPU, streamed; local fallback |
