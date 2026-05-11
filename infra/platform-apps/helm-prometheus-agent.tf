resource "helm_release" "prometheus_agent" {
  name       = "prometheus-agent"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "prometheus"
  version    = "27.39.0"

  values = [yamlencode({
    # Disable everything except the server. We only need scrape + remote_write.
    alertmanager               = { enabled = false }
    "prometheus-pushgateway"   = { enabled = false }
    "kube-state-metrics"       = { enabled = false }
    "prometheus-node-exporter" = { enabled = false }

    serviceAccounts = {
      server = {
        create = true
        name   = "prometheus-agent-sa"
      }
    }

    server = {
      # Native chart toggle — adds --agent AND strips the
      # --storage.tsdb.path / --storage.tsdb.retention.time flags
      # which prometheus rejects in agent mode. DO NOT replace with
      # extraArgs.agent="": that only adds --agent, the TSDB flags
      # remain, and the pod CrashLoops with
      #   "The following flag(s) can not be used in agent mode:
      #    [\"--storage.tsdb.retention.time\" \"--storage.tsdb.path\"]"
      agentMode = true

      # Set globals here, NOT under serverFiles."prometheus.yml" — the
      # chart auto-renders a `global:` block from `server.global`; adding
      # `global:` under serverFiles would produce duplicate keys in the
      # rendered configmap.
      global = {
        scrape_interval     = "30s"
        scrape_timeout      = "10s"
        evaluation_interval = "30s"
      }

      # No persistence in agent mode — WAL is ephemeral and that's fine
      # because remote_write retries from WAL on restart.
      persistentVolume = { enabled = false }

      # Don't expose a service externally. Grafana queries AMP directly.
      service = {
        type = "ClusterIP"
      }

      resources = {
        requests = { cpu = "100m", memory = "256Mi" }
        limits   = { cpu = "500m", memory = "512Mi" }
      }

      remoteWrite = [{
        url = "${aws_prometheus_workspace.main.prometheus_endpoint}api/v1/remote_write"
        sigv4 = {
          region = var.region
        }
        queue_config = {
          max_samples_per_send = 1000
          max_shards           = 200
          capacity             = 2500
        }
      }]
    }

    # No `global:` here (see comment above on server.global).
    serverFiles = {
      "prometheus.yml" = {
        scrape_configs = [
          {
            job_name = "vllm-workers"
            # 5s matches fastapi-gateway scrape; gives the dashboards
            # consistent temporal resolution end-to-end.
            scrape_interval       = "5s"
            scrape_timeout        = "4s"
            kubernetes_sd_configs = [{ role = "pod" }]
            relabel_configs = [
              {
                source_labels = ["__meta_kubernetes_pod_label_model"]
                regex         = "qwen25-7b"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_container_port_number"]
                regex         = "8000"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_name"]
                target_label  = "pod"
              },
            ]
            metrics_path = "/metrics"
          },
          {
            job_name              = "vllm-router"
            kubernetes_sd_configs = [{ role = "pod" }]
            relabel_configs = [
              {
                source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name"]
                regex         = "router"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_part_of"]
                regex         = "vllm-stack"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_name"]
                target_label  = "pod"
              },
            ]
          },
          {
            job_name = "fastapi-gateway"
            # Gateway-only override: 5s scrape so short bursts (queue depth
            # plateaus, in-flight transients) aren't sampling-aliased away.
            # Other targets stay at the global 30s.
            scrape_interval       = "5s"
            scrape_timeout        = "4s"
            kubernetes_sd_configs = [{ role = "pod" }]
            relabel_configs = [
              {
                source_labels = ["__meta_kubernetes_pod_label_app"]
                regex         = "fastapi-gateway"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_name"]
                target_label  = "pod"
              },
            ]
          },
          {
            job_name              = "dcgm-exporter"
            kubernetes_sd_configs = [{ role = "pod" }]
            relabel_configs = [
              {
                source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name"]
                regex         = "dcgm-exporter"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_container_port_number"]
                regex         = "9400"
                action        = "keep"
              },
              {
                source_labels = ["__meta_kubernetes_pod_name"]
                target_label  = "pod"
              },
            ]
          },
        ]
      }
    }
  })]

  depends_on = [
    aws_eks_pod_identity_association.prometheus_agent,
  ]
}
