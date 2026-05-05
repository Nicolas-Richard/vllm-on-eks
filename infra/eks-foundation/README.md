# eks-foundation

Sub-project A of the vLLM-on-EKS effort. See `../../docs/superpowers/specs/2026-04-25-eks-foundation-design.md` for the full design.

## Prerequisites

- AWS profile `sandbox-admin` (account `123456789012`)
- Terraform `~> 1.7`
- `kubectl`, `aws` CLI

## Bring up

```bash
aws sso login --profile sandbox-admin
export AWS_PROFILE=sandbox-admin

terraform init
terraform apply
```

After apply, configure kubectl:

```bash
$(terraform output -raw kubeconfig_command)
kubectl get nodes
```

Expect: 1 CPU node + 2 GPU nodes, all `Ready`.

## Scale GPUs to zero (saves ~$1.50/hr)

```bash
terraform apply -var gpu_desired_size=0
```

The cluster, CPU node, and Helm releases stay up. Device-plugin
DaemonSet pods will sit pending until GPUs return — harmless.

To bring GPUs back:

```bash
terraform apply -var gpu_desired_size=2
```

## Tear down completely

```bash
terraform destroy
```

Takes ~10–15 minutes. Helm releases are uninstalled before the
cluster is removed (handled by Terraform's reverse-graph traversal).

## Validation checklist

```bash
# 1. Nodes ready
kubectl get nodes

# 2. GPUs schedulable
kubectl get nodes -l workload=gpu \
  -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}'
# Expect: "1 1"

# 3. Device plugin running
kubectl get pods -n kube-system -l app.kubernetes.io/name=nvidia-device-plugin

# 4. Add-ons ACTIVE
aws eks list-addons --cluster-name $(terraform output -raw cluster_name)

# 5. End-to-end GPU smoke
kubectl run gpu-smoke --rm -it --restart=Never \
  --image=nvidia/cuda:12.4.0-base-ubuntu22.04 \
  --overrides='{"spec":{"tolerations":[{"key":"nvidia.com/gpu","operator":"Exists"}],"containers":[{"name":"x","image":"nvidia/cuda:12.4.0-base-ubuntu22.04","command":["nvidia-smi"],"resources":{"limits":{"nvidia.com/gpu":1}}}]}}'
```
