#!/usr/bin/env bash
# Drive parallel `vllm bench serve` per tenant against the in-cluster
# FastAPI gateway. Each tenant runs as one bench process inside the
# benchmarks-runner pod. Output JSONs land in /results/<run-id>/ in the
# pod and are mirrored back via `kubectl cp` after the run.
#
# Usage:
#   ./bench/run_scenario.sh \
#     bench/scenarios/noisy-neighbor.yaml \
#     --caps-enabled true|false \
#     [--run-id <string>]
#
# --caps-enabled is METADATA ONLY. The operator must flip the
# `caps_enabled` field in the in-cluster ConfigMap and roll the gateway
# pod separately.
set -euo pipefail

SCENARIO=""
CAPS_FLAG=""
RUN_ID=""

usage() { sed -n '2,15p' "$0"; }

if [[ $# -lt 1 ]]; then usage >&2; exit 2; fi

# Handle --help/-h before consuming the positional scenario argument.
case "$1" in
  -h|--help) usage; exit 0 ;;
esac

SCENARIO="$1"; shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --caps-enabled) CAPS_FLAG="${2:?--caps-enabled requires a value}"; shift 2 ;;
    --run-id)       RUN_ID="${2:?--run-id requires a value}"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$CAPS_FLAG" in
  true|false) ;;
  "") echo "missing --caps-enabled" >&2; usage >&2; exit 2 ;;
  *)  echo "invalid --caps-enabled: $CAPS_FLAG (want true|false)" >&2; exit 2 ;;
esac

if [[ ! -f "$SCENARIO" ]]; then
  echo "scenario file not found: $SCENARIO" >&2; exit 2
fi

command -v yq  >/dev/null || { echo "yq required (brew install yq)" >&2; exit 2; }
command -v jq  >/dev/null || { echo "jq required (brew install jq)" >&2; exit 2; }

SCENARIO_NAME="$(basename "$SCENARIO" .yaml)"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
: "${RUN_ID:=${SCENARIO_NAME}-caps-${CAPS_FLAG}-${TIMESTAMP}}"

KUBE_CONTEXT="${KUBE_CONTEXT:-arn:aws:eks:us-east-1:<AWS_ACCOUNT_ID>:cluster/nico-sdbx}"
NS="benchmarks"
DEPLOY="deploy/benchmarks-runner"
GATEWAY_URL="http://fastapi-gateway.vllm.svc.cluster.local:80"
MODEL="Qwen/Qwen2.5-7B-Instruct"

LOCAL_DIR="bench/runs/${RUN_ID}"
POD_DIR="/results/${RUN_ID}"
mkdir -p "$LOCAL_DIR"

TENANT_COUNT="$(yq -r '.tenants | length' "$SCENARIO")"

# Reset gateway in-process state (AIMD cap, histogram window, scheduler queues)
# so each scenario starts from the bootstrap config. Without this, AIMD's cap
# from a prior burst persists into the next run and pollutes the baseline.
echo "[reset] rolling fastapi-gateway to clear AIMD/scheduler state..."
kubectl --context "$KUBE_CONTEXT" -n vllm rollout restart deploy/fastapi-gateway >/dev/null
kubectl --context "$KUBE_CONTEXT" -n vllm rollout status deploy/fastapi-gateway --timeout=120s

T_START="$(date -u +%FT%TZ)"
START_EPOCH="$(date +%s)"

echo "run_id=$RUN_ID"
echo "scenario=$SCENARIO"
echo "caps_enabled=$CAPS_FLAG (metadata only — confirm cluster matches)"
echo "tenants=$TENANT_COUNT"
echo "start=$T_START"

# Launch one background bench per tenant.
PIDS=()
for i in $(seq 0 $((TENANT_COUNT - 1))); do
  TID="$(yq -r ".tenants[$i].id" "$SCENARIO")"
  OFFSET="$(yq -r ".tenants[$i].start_offset_s" "$SCENARIO")"
  DURATION="$(yq -r ".tenants[$i].duration_s" "$SCENARIO")"   # informational; vllm bench runs to num_prompts, not wall time
  : "$DURATION"  # suppress SC2034 — kept for manifest/logging parity with scenario fields
  RATE="$(yq -r ".tenants[$i].request_rate" "$SCENARIO")"
  NPROMPTS="$(yq -r ".tenants[$i].num_prompts" "$SCENARIO")"
  ENV_KEY="$(echo "$TID" | tr '[:lower:]-' '[:upper:]_')_KEY"   # tenant-a → TENANT_A_KEY

  OUT_POD="${POD_DIR}/${TID}.json"
  echo "[launch] ${TID} offset=${OFFSET}s rate=${RATE}rps n=${NPROMPTS} env=${ENV_KEY}"

  (
    kubectl --context "$KUBE_CONTEXT" -n "$NS" exec "$DEPLOY" -- bash -c "
      set -euo pipefail
      mkdir -p '${POD_DIR}'
      sleep ${OFFSET}
      export OPENAI_API_KEY=\"\$${ENV_KEY}\"
      vllm bench serve \
        --backend openai-chat --endpoint /v1/chat/completions \
        --base-url '${GATEWAY_URL}' \
        --model '${MODEL}' \
        --dataset-name random --random-input-len 512 --random-output-len 128 \
        --request-rate ${RATE} --num-prompts ${NPROMPTS} \
        --metric-percentiles 50,99 \
        --save-result --result-filename '${OUT_POD}'
    " >"${LOCAL_DIR}/${TID}.log" 2>&1
  ) &
  PIDS+=($!)
done

# Wait for all benches; collect non-zero exits.
EXITS=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    EXITS=$((EXITS + 1))
  fi
done

T_END="$(date -u +%FT%TZ)"
END_EPOCH="$(date +%s)"

cat >"${LOCAL_DIR}/manifest.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "scenario_path": "${SCENARIO}",
  "caps_enabled_claimed": ${CAPS_FLAG},
  "t_start": "${T_START}",
  "t_end": "${T_END}",
  "start_epoch": ${START_EPOCH},
  "end_epoch": ${END_EPOCH},
  "tenant_count": ${TENANT_COUNT},
  "failed_processes": ${EXITS},
  "pod_results_dir": "${POD_DIR}",
  "kube_context": "${KUBE_CONTEXT}"
}
EOF

echo "manifest=${LOCAL_DIR}/manifest.json"
echo "Run complete: ${LOCAL_DIR}"
echo
echo "Pull JSON output to laptop with:"
echo "  kubectl --context ${KUBE_CONTEXT} -n ${NS} cp \\"
echo "    ${DEPLOY##deploy/}:${POD_DIR} ${LOCAL_DIR}"

if (( EXITS > 0 )); then
  echo "WARNING: ${EXITS} bench process(es) exited non-zero — check ${LOCAL_DIR}/*.log" >&2
  exit 1
fi
