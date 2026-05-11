resource "helm_release" "karpenter" {
  name       = "karpenter"
  namespace  = "kube-system"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = "1.12.0"

  values = [yamlencode({
    # Single replica: the chart defaults to 2 with zone-topology-spread + pod
    # anti-affinity, but our cluster is single-AZ with desired_size=1 on the
    # CPU node group, so the second pod stays Pending forever. HA Karpenter is
    # a production concern; for a sandbox demo the controller restarting on
    # crash is acceptable.
    replicas = 1

    serviceAccount = {
      name = "karpenter"
    }

    settings = {
      clusterName     = data.terraform_remote_state.foundation.outputs.cluster_name
      clusterEndpoint = data.terraform_remote_state.foundation.outputs.cluster_endpoint
      # No SQS queue: spot interruption / scheduled maintenance handling is a
      # production concern; this is a sandbox demo using on-demand only.
      interruptionQueue = ""
    }

    controller = {
      resources = {
        requests = { cpu = "200m", memory = "256Mi" }
        limits   = { cpu = "1", memory = "1Gi" }
      }
    }

    # Pin to the existing CPU node group (system / gateway / Karpenter
    # controller live there). Without this, the controller could land on a
    # GPU node it just provisioned and create a self-eviction loop.
    nodeSelector = {
      workload = "cpu"
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.karpenter_controller,
  ]
}
