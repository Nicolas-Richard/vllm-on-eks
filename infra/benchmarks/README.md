# benchmarks

Sub-project C of the vLLM-on-EKS effort. Deploys a long-running CPU-pinned
runner pod (`vllm/vllm-openai:v0.19.1`, `sleep infinity`) in a new
`benchmarks` namespace, with B's `gateway-auth` Secret replicated as
`$BEARER_TOKEN` in the runner's env. The pod is the workbench from
which `vllm bench serve` drives experiments against the cluster B built.

## Prerequisites

- Sub-projects A (`infra/eks-foundation`) and B (`infra/platform-apps`)
  applied; `terraform.tfstate` files present in each.
- `kubectl` context pointing at the cluster.
- GPU workers Ready (`gpu_desired_size=2` in A) before running experiments.

## Apply

```bash
cd infra/benchmarks
terraform init
terraform apply
kubectl wait -n benchmarks deploy/benchmarks-runner --for=condition=Available --timeout=10m
```

First image pull takes several minutes (image is ~8 GB).

## Exec into the runner

```bash
$(terraform output -raw runner_exec_cmd)
# inside the pod:
echo "$BEARER_TOKEN" | head -c 8 ; echo
which vllm
```

## Run experiments

From the repo root:

```bash
./bench/run_sweep.sh router-direct   # ~10 min
./bench/run_sweep.sh gateway         # ~10 min
./bench/run_routing_demo.sh          # ~5 min; prints START_TS / END_TS
./bench/cp_results.sh                # pulls /results out of the pod
uv run --project bench python bench/plot_sweep.py
```

See `bench/README.md` for screenshot capture and asset commit steps.

## Scale-to-zero between sessions

GPU node group → 0 in A. Runner pod stays Running on the CPU node
(cents per day). Experiments will fail until GPUs return.

## Destroy

```bash
cd infra/benchmarks
terraform destroy
```

Removes runner Deployment, replicated secret, and namespace. ~30 s.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `vllm bench serve` connection-refused | GPU workers Pending / scaled to zero | `cd infra/eks-foundation && terraform apply -var gpu_desired_size=2`; wait for workers Ready |
| Runner pod ImagePullBackOff | Cold node, slow pull of 8 GB image | Wait — first pull only |
| Gateway run gets 401 | Replicated secret out of sync (token rotated in B) | Re-run `terraform apply` here to refresh the replica |
| `vllm bench serve` rejects `--header` | Older CLI build in pinned image | Use the alternate spelling from Task 10, or fall back to `OPENAI_API_KEY=$BEARER_TOKEN` |
