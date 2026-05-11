# platform-apps

Sub-project B of the vLLM-on-EKS effort. Deploys the inference + observability
stack on top of the EKS foundation built by `infra/eks-foundation`.

## Prerequisites

- Sub-project A (`infra/eks-foundation`) applied successfully.
- `aws sso login --profile <AWS_PROFILE>`.
- Docker running locally.
- A `terraform.tfvars` filled in (see `terraform.tfvars.example`).

## Apply

From the repo root:

```bash
# 1. Inference stack + gateway + observability.
#    - bootstraps the gateway ECR repo
#    - builds + pushes apps/fastapi-gateway via terraform_data (hash-keyed,
#      so the image tag only churns when the gateway source changes)
#    - applies vllm-stack, dcgm-exporter, grafana, prometheus-agent
make deploy

# 2. Bake the custom vLLM image (Qwen2.5-7B weights baked in).
#    Requires huggingface_token in terraform.tfvars. ~10–30 min first run.
make vllm-image

# 3. Install Karpenter and the GPU NodePool / NodeClass.
#    GPU nodes are provisioned on demand from this point on.
make karpenter-up

# 4. Install KEDA + the ScaledObject driven by the AMP queue-depth signal.
make keda-up
```

`make deploy` alone is ~10–15 min cold start; the autoscaling steps add
another ~10–30 min depending on the vLLM image bake.

## Use the gateway

Streaming smoke test from the repo root:

```bash
make gateway-chat                              # clean text streamed to stdout
make gateway-chat PROMPT="Explain bfloat16."
make gateway-test                              # raw SSE chunks (debugging)
```

Just print the connection details:

```bash
make gateway-info
# URL:   http://...elb.amazonaws.com
# TOKEN: <hex>
```

Or hand-craft a request:

```bash
URL=$(make -s gateway-url)
TOKEN=$(make -s gateway-token)
curl -N -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"model":"Qwen/Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"hi"}],"stream":true}' \
     "$URL/v1/chat/completions"
```

## Open Grafana

```bash
kubectl port-forward -n monitoring svc/grafana 3000:80
# Browser: http://localhost:3000
# Admin password:
kubectl -n monitoring get secret grafana -o jsonpath='{.data.admin-password}' | base64 -d
```

## Scale GPUs to zero between sessions

Karpenter provisions GPU nodes (`gpu-l4` NodePool) in response to pod demand,
so taking GPU spend to ~$0 means scaling the vLLM engine and headroom
warm-pool deployments to 0; Karpenter consolidates the freed nodes.

```bash
make gpu-scale-down   # vllm_replicas=0, headroom_replicas=0
make gpu-scale-up     # back to 2 + 2
```

Under the hood: `terraform apply -var=vllm_replicas=… -var=headroom_replicas=…`
in this module. NLB stays up; Prometheus agent sees fewer targets while down.

## Destroy

```bash
make destroy
```

Deletes everything except the EKS cluster (that's owned by A).
