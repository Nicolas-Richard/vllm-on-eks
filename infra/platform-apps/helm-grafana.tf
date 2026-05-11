resource "helm_release" "grafana" {
  name       = "grafana"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  repository = "https://grafana.github.io/helm-charts"
  chart      = "grafana"
  version    = "10.5.15"

  values = [yamlencode({
    image = {
      tag = "12.3.1"
    }

    persistence = {
      enabled = false
    }

    serviceAccount = {
      create = true
      name   = "grafana-sa"
    }

    # Enable SigV4 at the Grafana server level — required for the
    # AMP datasource's sigV4Auth flag to actually take effect.
    "grafana.ini" = {
      auth = {
        sigv4_auth_enabled = true
      }
    }

    datasources = {
      "datasources.yaml" = {
        apiVersion = 1
        datasources = [{
          name      = "AMP"
          uid       = "amp"
          type      = "prometheus"
          url       = aws_prometheus_workspace.main.prometheus_endpoint
          access    = "proxy"
          isDefault = true
          editable  = false
          jsonData = {
            httpMethod    = "POST"
            sigV4Auth     = true
            sigV4AuthType = "default"
            sigV4Region   = var.region
            # Default step Grafana asks AMP for. Was 15s by default —
            # capped queue-depth panels at 1 sample per 15s and hid the
            # gateway's 5s-scrape resolution. Drop to 5s to match the
            # gateway scrape interval.
            timeInterval = "5s"
          }
        }]
      }
    }

    dashboardProviders = {
      "dashboardproviders.yaml" = {
        apiVersion = 1
        providers = [{
          name            = "vllm"
          folder          = "vLLM"
          type            = "file"
          disableDeletion = false
          editable        = false
          options = {
            path = "/var/lib/grafana/dashboards/vllm"
          }
        }]
      }
    }

    dashboards = {
      vllm = {
        for name, body in local.grafana_dashboards :
        name => { json = jsonencode(body) }
      }
    }

    service = {
      type = "ClusterIP"
      port = 80
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.grafana,
  ]
}
