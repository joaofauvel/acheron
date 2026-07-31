# Phase 3a scenarios

All scenarios require `just runpod-sim` (or one of the Just targets that depends on it) and use the mock at `http://127.0.0.1:8999`. Each scenario resets the mock before making assertions.

| Scenario | Story reference | Exercise | JSON oracle |
|---|---|---|---|
| `pricing_outage` | `MAINT-002`, `MAINT-014`, `MAINT-015`, `OPS-005` | Assert fresh RunPod quotes remain `UNKNOWN`, a warm cache after outage is `CACHED` with positive age and retained metadata, a cold outage is `UNKNOWN`, and `price_source=zero` is the only `STUB` path. | `basis`: `unknown` → `cached` → `unknown`; explicit zero → `stub`. |
| `gpu_switch` | `MAINT-002`, `MAINT-014` | Patch `qwen-edge` from L4 to `NVIDIA A40`, assert refreshed GPU/rate metadata, then force outage and retain the A40 cache. | Fresh quote metadata: L4/$1.39 → A40/$2.49; outage: `CACHED` with positive age. |
| `cold_start` | `MAINT-009` | Exercise the health monitor while the RunPod provider reports a cold endpoint. | `during_cold_start=BOOTING`, then `after_cold_start=HEALTHY`. |

Story statuses remain unchanged by this harness-only phase.
