# eks-foundation

Sub-project A of the vLLM-on-EKS effort: VPC, EKS cluster, CPU node group,
core addons. GPU nodes are provisioned by Karpenter (installed in sub-project
B, `infra/platform-apps`), so a fresh apply here produces a CPU-only cluster.

## Prerequisites

- AWS profile `<AWS_PROFILE>` (account `<AWS_ACCOUNT_ID>`)
- Terraform `~> 1.7`
- `kubectl`, `aws` CLI

## Bring up

```bash
aws sso login --profile <AWS_PROFILE>
export AWS_PROFILE=<AWS_PROFILE>

terraform init
terraform apply
```

After apply, configure kubectl:

```bash
$(terraform output -raw kubeconfig_command)
kubectl get nodes
```

Expect: 1 CPU node, `Ready`. GPU nodes appear only after `infra/platform-apps`
is applied (Karpenter provisions them on demand for the vLLM engine + headroom
warm-pool pods).

## Scale GPUs to zero

GPU nodes are owned by Karpenter and driven by the vLLM engine + headroom
deployments in `infra/platform-apps`. Take GPU spend to ~$0 by scaling those
deployments to zero — see `make gpu-scale-down` / `gpu-scale-up` at the repo
root.

## Tear down completely

```bash
terraform destroy
```

Takes ~10–15 minutes. Helm releases are uninstalled before the
cluster is removed (handled by Terraform's reverse-graph traversal).

## Validation checklist

```bash
# 1. Node ready
kubectl get nodes

# 2. Add-ons ACTIVE
aws eks list-addons --cluster-name $(terraform output -raw cluster_name)
```

GPU validation (device-plugin DaemonSet, schedulable GPUs, CUDA smoke test)
lives with the Karpenter pool in `infra/platform-apps`.
