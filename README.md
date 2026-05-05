# vllm-on-eks

A multi-tenant LLM inference stack on EKS: vLLM serving Qwen2.5-7B behind a
FastAPI gateway that does per-tenant weighted fair queueing (DRR) and an AIMD
admission controller that adapts concurrency to a p99 TTFT target. Drop-in
benchmark harness for reproducing the experiments.

## Layout

```
infra/
  eks-foundation/   VPC, EKS cluster, GPU + CPU node groups, addons
  platform-apps/    Helm releases: vllm-stack, dcgm-exporter, grafana, prometheus-agent + the gateway image and its K8s manifests
  benchmarks/       Long-running runner pod (vllm bench serve workbench)
apps/
  fastapi-gateway/  FastAPI gateway: DRR scheduler + AIMD admission controller
charts/
  fastapi-gateway/  Helm chart for the gateway
bench/              Scenario YAMLs + run_scenario.sh harness; results land in bench/runs/
docs/blog/          Posts walking through the design and the experiments
```

## Build the infra (one-time)

Apply the three Terraform sub-projects in order. Each has its own README with
details.

```bash
aws sso login --profile <AWS_PROFILE>
export AWS_PROFILE=<AWS_PROFILE>

# A) cluster + nodes
( cd infra/eks-foundation && terraform init && terraform apply )

# B) inference stack + gateway + observability
( cd infra/platform-apps  && terraform init && terraform apply )

# C) benchmarks runner pod
( cd infra/benchmarks     && terraform init && terraform apply )
```

Prereqs: Terraform `~> 1.13`, `kubectl`, AWS CLI, Docker, an AWS account where
you can spin up an EKS cluster and 2× GPU nodes.

After A applies, point `kubectl` at the cluster:

```bash
$(cd infra/eks-foundation && terraform output -raw kubeconfig_cmd)
```

## Run a bench scenario

```bash
./bench/run_scenario.sh bench/scenarios/work-conservation-priority.yaml \
  --caps-enabled true
```

Output lands at `bench/runs/<run-id>/{manifest.json,tenant-*.json,tenant-*.log}`.

See `bench/README.md` for the harness layout and the available scenarios under
`bench/scenarios/`.

## Optional: Hugging Face token

`vllm bench serve` re-downloads the model tokenizer on every invocation. Three
concurrent bench processes can race on the same anonymous download and one
will fail with `OSError: Unable to load vocabulary from file`. To avoid that,
set an `HF_TOKEN` (read-scoped is enough) on the runner pod:

```bash
kubectl -n benchmarks create secret generic huggingface-token \
  --from-literal=HF_TOKEN=hf_xxx
kubectl -n benchmarks rollout restart deploy/benchmarks-runner
```

The runner references the secret with `optional: true`, so this is not blocking
— skip if you're only running one bench at a time.
