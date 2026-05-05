locals {
  grafana_dashboards = {
    "inference-latency" = jsondecode(file("${path.module}/dashboards/inference-latency.json"))
    "worker-comparison" = jsondecode(file("${path.module}/dashboards/worker-comparison.json"))
    "gpu-health"        = jsondecode(file("${path.module}/dashboards/gpu-health.json"))
    "gateway"           = jsondecode(file("${path.module}/dashboards/gateway.json"))
  }
}
