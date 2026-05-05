Here’s a clean, execution-ready version you can hand off.

⸻

Context & North Star

Build a production-shaped, low-latency LLM inference system on AWS (EKS) using vLLM Production Stack as the routing layer, with two GPU-backed vLLM workers to demonstrate intelligent routing (prefix/KV-cache aware) and real-time streaming.

The system must:

* Stream tokens end-to-end with minimal latency
* Clearly demonstrate routing behavior across two GPU workers
* Expose observable metrics (latency, queueing, cache usage)
* Be cheap to operate and easy to tear down/rebuild
* Be structured and documented well enough to support a high-quality engineering blog post

The final request flow is:

Client → FastAPI service (streaming + logging) → vLLM Production Stack router → vLLM GPU worker → stream back → FastAPI → Client


⸻
Sub-project A — EKS foundation (PROJECT.md milestones 1–2) — BUILT
VPC (single AZ for workloads, public subnets only), EKS cluster (v1.35), CPU + GPU (G6/L4) node groups, EKS-managed add-ons, IAM/OIDC, EKS Pod Identity agent, EKS Access Entries (API auth mode), NVIDIA device plugin. Output: a cluster you can kubectl into with GPU nodes ready. Scale-to-zero supported via `gpu_desired_size` Terraform variable.

Sub-project B — Platform apps (milestones 3–5)
Helm releases for vLLM Production Stack with 2 workers, FastAPI gateway, DCGM exporter, stateless Grafana. Observability storage via Amazon Managed Prometheus (AMP) workspace + AMP scraper (managed AWS service, not in-cluster Prometheus). Output: end-to-end streaming working with GPU metrics visible.

Sub-project C — Benchmarks + routing demo (milestones 6–7)
Workload generator, repeated-prefix experiment, DynamoDB request log for prompt/response capture, dashboard captures. Output: data + screenshots showing routing effectiveness.

Sub-project D — Lifecycle + blog artifacts (milestones 8–9)
Scale-to-zero workflow, teardown automation, architecture diagram, narrative. Output: shippable blog post.

⸻

Milestone 1 — Provision AWS foundation and EKS cluster

Tasks:

* Create VPC, subnets, IAM roles, and networking required for EKS.
* Provision an EKS cluster.
* Create two node groups:
    * CPU node group for application services
    * GPU node group using G6 (L4) instances
* Configure GPU node group with NVIDIA-enabled AMI (EKS optimized or Bottlerocket).
* Verify GPU resources are available in Kubernetes and schedulable.

⸻

Milestone 2 — Deploy base Kubernetes platform components

Tasks:

* Install core EKS-managed add-ons (vpc-cni, coredns, kube-proxy, aws-ebs-csi-driver, eks-pod-identity-agent).
* Install NVIDIA device plugin via Helm so GPUs are schedulable as `nvidia.com/gpu` resources.
* Configure node labeling/taints to ensure:
    * GPU workloads land on GPU nodes (`workload=gpu` label, `nvidia.com/gpu=true:NoSchedule` taint)
    * CPU services stay on CPU nodes (`workload=cpu` label)
* Validate cluster scheduling behavior with test workloads.

Note: ingress/gateway layer is deferred to Sub-project B (it's a platform-app concern, not foundation).

⸻

Milestone 3 — Deploy vLLM Production Stack

Tasks:

* Install vLLM Production Stack via Helm.
* Configure routing with 2 vLLM instances (1 per GPU node).
* Deploy a single model across both workers (7B–8B class).
* Enable one routing strategy:
    * start with prefix-aware routing (preferred for demo clarity)
* Expose the Production Stack router endpoint inside the cluster.
* Validate:
    * both workers receive traffic
    * routing layer is active
    * vLLM /metrics endpoints are reachable

⸻

Milestone 4 — Deploy FastAPI gateway service

Tasks:

* Build and containerize a FastAPI service.
* Implement:
    * request forwarding to Production Stack router
    * full streaming passthrough (no buffering)
    * `/metrics` endpoint for AMP scraper
    * `/healthz` endpoint
    * static-bearer auth (HF-token-style header check)
* Expose FastAPI as the public entrypoint (LoadBalancer Service).
* Validate end-to-end streaming:
    * client → FastAPI → router → worker → streamed response back

Note: persistent request/response logging (DynamoDB capture of prompts, completions, latencies) is deferred to Sub-project C, where it pairs with the benchmark workload.

⸻

Milestone 5 — Enable observability (AMP + DCGM + Grafana)

Tasks:

* Provision an Amazon Managed Service for Prometheus (AMP) workspace via Terraform.
* Provision an AMP scraper (managed AWS scrape agent) targeting:
    * vLLM worker /metrics
    * Production Stack router components
    * FastAPI service /metrics
    * DCGM exporter on GPU nodes
* Authenticate the AMP scraper to the AMP workspace via EKS Pod Identity (no IRSA).
* Install DCGM exporter as a Helm release / DaemonSet on GPU nodes (provides GPU metrics that AMP scrapes).
* Deploy Grafana stateless: dashboards baked into ConfigMaps via Helm values; data source = AMP via sigv4 auth.
* Build initial dashboards:
    * per-worker metrics (A vs B)
    * system-level latency and throughput
    * GPU utilization, memory, temperature (from DCGM)
* Verify visibility of:
    * TTFT
    * inter-token latency
    * queue time
    * running/waiting requests
    * KV cache usage
    * prefix cache metrics

Note: this stack is intentionally PVC-free. AMP holds metrics, Grafana is stateless. Sub-project A's EBS CSI add-on is therefore optional from B's perspective.

⸻

Milestone 6 — Define and run benchmark workloads

Tasks:

* Create benchmark scripts or use existing tooling to generate:
    * single-request baseline traffic
    * increasing concurrency traffic (sweep)
    * repeated-prefix workload (for routing demo)
* Run benchmarks through:
    * direct router endpoint
    * full FastAPI path
* Capture metrics:
    * TTFT (p50/p95)
    * inter-token latency
    * end-to-end latency
    * throughput (requests/sec, tokens/sec)
    * queue time
    * worker utilization
    * cache usage metrics

⸻

Milestone 7 — Demonstrate routing effectiveness

Tasks:

* Execute repeated-prefix workload to trigger routing behavior.
* Observe distribution of traffic across the two workers.
* Compare:
    * latency metrics between workers
    * cache utilization and prefix hit behavior
* Correlate routing patterns with performance outcomes.
* Capture dashboard screenshots showing:
    * worker divergence (A vs B)
    * improved latency under routing
    * stable streaming behavior

⸻

Milestone 8 — Implement teardown and cost control

Tasks:

* Keep Terraform structure simple: one root module per sub-project (`infra/eks-foundation`, `infra/platform-apps`), each with its own state. Sub-project A's state owns VPC + cluster + node groups; B's state owns Helm releases + AMP. C's persistent resources (DynamoDB for request log, ECR for FastAPI image) live in their own root module.
* Implement:
    * scale GPU node group to zero via `terraform apply -var gpu_desired_size=0` in `infra/eks-foundation` (already built in A). Cluster, CPU node, AMP, and Helm releases stay up; vLLM pods sit pending until GPUs return.
    * full teardown via `terraform destroy` in reverse dependency order (platform-apps → eks-foundation).
* Document operational workflow:
    * bring system up
    * run experiments
    * scale to zero between sessions
    * tear system down
* Validate teardown reduces cost footprint effectively.

⸻

Milestone 9 — Prepare artifacts for blog post

Tasks:

* Create architecture diagram showing full request flow.
* Capture Grafana dashboards:
    * latency
    * routing behavior
    * worker comparison
* Document:
    * design decisions (EKS, G6 GPUs, Production Stack)
    * routing strategy used
    * benchmark methodology
    * observed results
* Prepare clear narrative:
    * problem → architecture → benchmarks → insights

⸻

Final Expected Outcome

At completion, the system should:

* Run on EKS with 2 GPU-backed vLLM workers
* Use vLLM Production Stack for routing
* Support low-latency streaming responses end-to-end
* Expose clear, visual evidence of routing behavior
* Be cheap to run and easy to shut down
* Provide enough data and structure to support a strong technical blog post

⸻

If you want next, I can turn this into a repo structure + Terraform + Helm layout so an agent can go straight from plan → implementation.