locals {
  grafana_dashboards = {
    "inference-latency" = jsondecode(file("${path.module}/dashboards/inference-latency.json"))
    "worker-comparison" = jsondecode(file("${path.module}/dashboards/worker-comparison.json"))
    "gpu-health"        = jsondecode(file("${path.module}/dashboards/gpu-health.json"))
    "gateway"           = jsondecode(file("${path.module}/dashboards/gateway.json"))
    "multi-tenant"      = jsondecode(file("${path.module}/dashboards/multi-tenant.json"))
    "drr-scheduler"     = jsondecode(file("${path.module}/dashboards/drr-scheduler.json"))
    "aimd"              = jsondecode(file("${path.module}/dashboards/aimd.json"))
    "overview"          = jsondecode(file("${path.module}/dashboards/overview.json"))
  }
}
