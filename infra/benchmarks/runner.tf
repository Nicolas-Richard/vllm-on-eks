resource "kubernetes_deployment_v1" "runner" {
  metadata {
    name      = "benchmarks-runner"
    namespace = kubernetes_namespace_v1.benchmarks.metadata[0].name

    labels = {
      app = "benchmarks-runner"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "benchmarks-runner"
      }
    }

    template {
      metadata {
        labels = {
          app = "benchmarks-runner"
        }
      }

      spec {
        node_selector = {
          workload = "cpu"
        }

        container {
          name    = "runner"
          image   = "vllm/vllm-openai:v0.19.1"
          command = ["sleep", "infinity"]

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.tenant_keys_local.metadata[0].name
            }
          }

          # Optional HF auth — if a secret named `huggingface-token` exists in
          # this namespace with key `HF_TOKEN`, vllm bench serve will pick it up
          # for tokenizer downloads (avoids anonymous rate limits when 3
          # concurrent bench processes race on the same vocab file). See README
          # for setup. Marked optional so the pod schedules even before the
          # secret is created.
          env_from {
            secret_ref {
              name     = "huggingface-token"
              optional = true
            }
          }

          volume_mount {
            name       = "results"
            mount_path = "/results"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "2"
              memory = "4Gi"
            }
          }
        }

        volume {
          name = "results"
          empty_dir {}
        }
      }
    }
  }
}
