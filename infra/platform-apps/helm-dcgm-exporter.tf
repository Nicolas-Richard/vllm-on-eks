resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      "app.kubernetes.io/managed-by" = "platform-apps"
    }
  }
}

resource "helm_release" "dcgm_exporter" {
  name       = "dcgm-exporter"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  repository = "https://nvidia.github.io/dcgm-exporter/helm-charts"
  chart      = "dcgm-exporter"
  version    = "4.8.1"

  values = [yamlencode({
    image = {
      repository = "nvcr.io/nvidia/k8s/dcgm-exporter"
      tag        = "4.5.2-4.8.1-distroless"
    }

    nodeSelector = {
      workload = "gpu"
    }

    tolerations = [{
      key      = "nvidia.com/gpu"
      operator = "Exists"
      effect   = "NoSchedule"
    }]

    service = {
      enabled = true
      type    = "ClusterIP"
      port    = 9400
    }

    serviceMonitor = {
      enabled = false
    }

    resources = {
      requests = { cpu = "100m", memory = "128Mi" }
      limits   = { cpu = "500m", memory = "512Mi" }
    }
  })]
}
