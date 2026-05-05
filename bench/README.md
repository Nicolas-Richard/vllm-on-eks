# bench/

Operator-driven scenario harness. Drives `vllm bench serve` from inside
the in-cluster `benchmarks-runner` pod with one bench process per
tenant, defined by a YAML scenario file.

## Layout

```
scenarios/                YAML scenarios + runbook
  work-conservation-priority.yaml
  noisy-neighbor.yaml
  aimd-baseline.yaml
  ...
run_scenario.sh           launch one scenario; rolls gateway, runs
                          per-tenant benches, mirrors JSON back
runs/                     per-run output: <run-id>/{manifest.json,tenant-*.json,tenant-*.log}
```

## Quickstart

```bash
./bench/run_scenario.sh bench/scenarios/work-conservation-priority.yaml \
  --caps-enabled true
# outputs land at bench/runs/<run-id>/
```

`--caps-enabled` is metadata only — flip the gateway ConfigMap and roll
the pod separately. See `scenarios/README.md` for the full procedure.
