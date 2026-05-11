# Rolling warm pool. Two low-priority pods that:
#   1. Hold GPU nodes that Karpenter has already provisioned.
#   2. Carry the vLLM ECR image so the node's containerd cache is pre-warmed
#      when an engine pod preempts the headroom and lands on the freed node.
#
# Engine pods (priorityClassName=vllm-engine) preempt headroom on burst →
# scale-up to *serving* is preemption-fast (~30s), not provisioning-slow.
# The evicted headroom becomes Pending; Karpenter provisions a replacement
# behind the demand curve.
#
# `model = "qwen25-7b"` label is shared with the engine pods (chart-set),
# so the chart's existing pod anti-affinity selector
# (`matchLabels: model=qwen25-7b`) catches headroom too — one vLLM-class pod
# per node, full stop.
resource "kubernetes_deployment_v1" "vllm_headroom" {
  # The first headroom pod on a fresh node spends ~9 min pulling the 21 GB
  # ECR vLLM image. That's longer than terraform's default 10-min rollout
  # wait, and pod readiness is a runtime concern (Karpenter + kubelet
  # handle it) — not something terraform's state model needs to gate on.
  wait_for_rollout = false

  metadata {
    name      = "vllm-headroom"
    namespace = kubernetes_namespace_v1.vllm.metadata[0].name
    labels = {
      app   = "vllm-headroom"
      model = "qwen25-7b"
    }
  }

  spec {
    replicas = var.headroom_replicas

    selector {
      match_labels = {
        app = "vllm-headroom"
      }
    }

    template {
      metadata {
        labels = {
          app   = "vllm-headroom"
          model = "qwen25-7b"
        }
      }

      spec {
        priority_class_name = kubernetes_priority_class_v1.vllm_headroom.metadata[0].name
        # Near-instant eviction so an engine pod doesn't wait the default 30s
        # for a graceful headroom shutdown.
        termination_grace_period_seconds = 1

        node_selector = {
          workload = "gpu"
        }

        toleration {
          key      = "nvidia.com/gpu"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        affinity {
          pod_anti_affinity {
            required_during_scheduling_ignored_during_execution {
              topology_key = "kubernetes.io/hostname"
              label_selector {
                match_labels = {
                  model = "qwen25-7b"
                }
              }
            }
          }
        }

        container {
          name              = "holder"
          image             = "${aws_ecr_repository.vllm_qwen25_7b.repository_url}:${local.vllm_image_tag}"
          command           = ["sleep", "infinity"]
          image_pull_policy = "IfNotPresent"

          resources {
            requests = { "nvidia.com/gpu" = 1 }
            limits   = { "nvidia.com/gpu" = 1 }
          }
        }
      }
    }
  }

  depends_on = [
    terraform_data.vllm_image,
  ]
}
