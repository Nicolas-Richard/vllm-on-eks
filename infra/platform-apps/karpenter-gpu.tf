# EC2NodeClass: AMI + IAM + networking for L4 GPU nodes Karpenter launches.
# Mirrors the labels / disk / instance-class shape of the static GPU node group
# this is replacing.
resource "kubernetes_manifest" "karpenter_nodeclass_gpu_l4" {
  manifest = {
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "gpu-l4"
    }
    spec = {
      amiFamily = "AL2023"

      # AL2023 NVIDIA-flavored AMI. The plain `al2023@latest` alias resolves
      # to the standard AMI which has no NVIDIA drivers — pods would fail to
      # start. Pin via the EKS-published SSM parameter for the NVIDIA variant
      # at the cluster's k8s version (1.35).
      amiSelectorTerms = [{
        ssmParameter = "/aws/service/eks/optimized-ami/1.35/amazon-linux-2023/x86_64/nvidia/recommended/image_id"
      }]

      role = data.terraform_remote_state.foundation.outputs.node_iam_role_name

      subnetSelectorTerms = [{
        id = data.terraform_remote_state.foundation.outputs.workload_subnet_id
      }]

      securityGroupSelectorTerms = [{
        id = data.terraform_remote_state.foundation.outputs.cluster_security_group_id
      }]

      blockDeviceMappings = [{
        deviceName = "/dev/xvda"
        ebs = {
          volumeSize          = "100Gi"
          volumeType          = "gp3"
          deleteOnTermination = true
        }
      }]

      tags = {
        Owner     = "nicolas.richard"
        Project   = "nico-sdbx"
        ManagedBy = "karpenter"
      }
    }
  }

  depends_on = [helm_release.karpenter]
}

# NodePool: the policy that decides when to provision an L4 node and what
# constraints to enforce. `limits.nvidia.com/gpu = 8` is the hard ceiling on
# demo spend.
resource "kubernetes_manifest" "karpenter_nodepool_gpu_l4" {
  manifest = {
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "gpu-l4"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            workload                 = "gpu"
            "nvidia.com/gpu.present" = "true"
          }
        }
        spec = {
          requirements = [
            {
              key      = "node.kubernetes.io/instance-type"
              operator = "In"
              values   = ["g6.2xlarge"]
            },
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["on-demand"]
            },
          ]

          # Same taint shape as the static GPU node group, so existing pod
          # tolerations (vLLM engine, dcgm-exporter) keep working unchanged.
          taints = [{
            key    = "nvidia.com/gpu"
            value  = "true"
            effect = "NoSchedule"
          }]

          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "gpu-l4"
          }

          # Don't recycle nodes on a TTL — hold them as long as a pod needs
          # them. Disruption is governed by `consolidationPolicy` below.
          expireAfter = "Never"
        }
      }

      limits = {
        "nvidia.com/gpu" = 8
      }

      disruption = {
        consolidationPolicy = "WhenEmpty"
        consolidateAfter    = "60s"
      }
    }
  }

  depends_on = [kubernetes_manifest.karpenter_nodeclass_gpu_l4]
}
