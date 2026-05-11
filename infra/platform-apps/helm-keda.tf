resource "helm_release" "keda" {
  name       = "keda"
  namespace  = kubernetes_namespace_v1.keda.metadata[0].name
  repository = "https://kedacore.github.io/charts"
  chart      = "keda"
  version    = "2.19.0"

  values = [yamlencode({
    serviceAccount = {
      operator = {
        create = true
        name   = "keda-operator"
      }
    }

    # Single replica: the chart defaults to leader-elected HA, but for a
    # sandbox demo with no reliability requirement, one replica halves the
    # CPU/memory footprint and avoids zone-spread scheduling friction (we
    # are single-AZ).
    operator = {
      replicaCount = 1
    }
    metricsServer = {
      replicaCount = 1
    }
    webhooks = {
      replicaCount = 1
    }

    # Pin to the CPU node group; KEDA controllers don't need GPU access.
    nodeSelector = {
      workload = "cpu"
    }

    resources = {
      operator = {
        requests = { cpu = "100m", memory = "128Mi" }
        limits   = { cpu = "500m", memory = "512Mi" }
      }
      metricServer = {
        requests = { cpu = "100m", memory = "128Mi" }
        limits   = { cpu = "500m", memory = "512Mi" }
      }
      webhooks = {
        requests = { cpu = "50m", memory = "64Mi" }
        limits   = { cpu = "200m", memory = "256Mi" }
      }
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.keda,
  ]
}
