# Phase 3a scenarios

All scenarios require `just runpod-sim` (or one of the Just targets that depends on it) and use the mock at `http://127.0.0.1:8999`. Each scenario resets the mock before making assertions.

| Scenario | Story reference | Exercise | JSON oracle |
|---|---|---|---|
| `pricing_outage` | `MAINT-002` | Toggle `pricing_api_down`, submit three jobs through the worker app, and restore pricing. | `cost_basis`: `measured` → `cached` → `measured`. |
| `gpu_switch` | `MAINT-002` | Patch `qwen-edge` from the L4 GPU to `NVIDIA A40` and submit before/after the cache TTL. | Implied hourly rate: approximately `$1.39` → `$2.49`. |
| `cold_start` | `MAINT-009` | Exercise the health monitor while the RunPod provider reports a cold endpoint. | `during_cold_start=BOOTING`, then `after_cold_start=HEALTHY`. |

Story statuses remain unchanged by this harness-only phase.
