# platform-apps

Sub-project B of the vLLM-on-EKS effort. Deploys the inference + observability
stack on top of the EKS foundation built by `infra/eks-foundation`.

## Prerequisites

- Sub-project A (`infra/eks-foundation`) applied successfully.
- `aws sso login --profile ChimeSandbox-Administrator`.
- Docker running locally.
- A `terraform.tfvars` filled in (see `terraform.tfvars.example`).

## Apply

From the repo root:

```bash
make deploy
```

This runs:

1. `terraform apply -target=aws_ecr_repository.fastapi` — creates the ECR repo.
2. `terraform apply` — full module. Terraform builds and pushes
   `apps/fastapi-gateway` to ECR via a `terraform_data` resource keyed on a
   content hash of the gateway sources, so the image tag (and helm upgrade)
   only churns when those files actually change.

Total: ~10–15 min cold start.

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

Done in **A**, not here:

```bash
cd ../eks-foundation
terraform apply -var gpu_desired_size=0
```

vLLM worker pods will Pend. NLB stays up. Prometheus agent sees fewer targets.
Bring back: `terraform apply -var gpu_desired_size=2`.

## Destroy

```bash
make destroy
```

Deletes everything except the EKS cluster (that's owned by A).
