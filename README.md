# vllm-on-eks

The companion code for a blog series on running multi-tenant LLM inference on
EKS: vLLM serving Qwen2.5-7B behind a FastAPI gateway that does per-tenant
weighted fair queueing (DRR) and an AIMD admission controller that adapts
concurrency to a p99 TTFT target. Drop-in benchmark harness for reproducing the
experiments.

## The blog series

1. [Streaming LLM inference on EKS](https://nicolas-richard.github.io/posts/streaming-llm-inference-on-eks.html) — the build: VPC, EKS, vLLM Production Stack, and the streaming gateway.
2. [How much can two L4s serve? It depends on the prompt.](https://nicolas-richard.github.io/posts/how-much-can-two-nvidia-l4s-serve.html) — capacity, prefix caching, and the methodology trap.
3. [Per-tenant concurrency caps](https://nicolas-richard.github.io/posts/per-tenant-concurrency-caps.html) — protecting well-behaved tenants from a bursty neighbor.
4. [Adaptive concurrency on a multi-tenant vLLM gateway: WFQ + AIMD against a TTFT SLO](https://nicolas-richard.github.io/posts/adaptive-concurrency-wfq-aimd-ttft-slo.html) — the self-tuning gateway.
5. [Autoscaling a GPU fleet on inference-aware signals](https://nicolas-richard.github.io/posts/autoscaling-gpu-fleet.html) — Karpenter + KEDA driven by AMP queue depth and gateway shed rate.

## Layout

```
infra/
  eks-foundation/   VPC, EKS cluster, CPU node group, addons
  platform-apps/    Helm releases (vllm-stack, dcgm-exporter, grafana, prometheus-agent),
                    the gateway image + manifests, baked vLLM image, Karpenter GPU pool,
                    and KEDA autoscaler driven by an AMP queue-depth signal
  benchmarks/       Long-running runner pod (vllm bench serve workbench)
apps/
  fastapi-gateway/  FastAPI gateway: DRR scheduler + AIMD admission controller
charts/
  fastapi-gateway/  Helm chart for the gateway
bench/              Scenario YAMLs + run_scenario.sh harness; results land in bench/runs/
```

## Build the infra (one-time)

Apply the three Terraform sub-projects in order. Each has its own README with
details. GPU nodes are provisioned by Karpenter (installed by B), so a fresh
apply of A produces a CPU-only cluster.

```bash
aws sso login --profile <AWS_PROFILE>
export AWS_PROFILE=<AWS_PROFILE>

# A) cluster + CPU node group + addons
( cd infra/eks-foundation && terraform init && terraform apply )

# B) inference stack + gateway + observability + Karpenter + KEDA
make deploy            # ECR bootstrap + apply
make vllm-image        # bake the Qwen2.5-7B image (needs huggingface_token)
make karpenter-up      # GPU NodePool / NodeClass
make keda-up           # AMP-driven engine autoscaler

# C) benchmarks runner pod
( cd infra/benchmarks  && terraform init && terraform apply )
```

Prereqs: Terraform `~> 1.13`, `kubectl`, AWS CLI, Docker, an AWS account where
you can spin up an EKS cluster and a few `g6.2xlarge` GPU nodes.

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

