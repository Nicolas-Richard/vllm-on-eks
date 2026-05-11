locals {
  # The proxy strips this prefix and forwards the rest to AMP. KEDA
  # appends `/api/v1/query` on its own. End-to-end the request lands
  # at `https://aps-workspaces.<region>.amazonaws.com/workspaces/<ws-id>/api/v1/query`.
  amp_proxy_url = "http://aws-sigv4-proxy.${kubernetes_namespace_v1.keda.metadata[0].name}.svc.cluster.local:8080/workspaces/${aws_prometheus_workspace.main.id}"
}

# ScaledObject targeting the engine Deployment.
#
# Talks to AMP via the aws-sigv4-proxy in the `keda` namespace (see
# aws-sigv4-proxy.tf). The proxy handles sigv4 signing; KEDA treats this
# as a plain Prometheus endpoint — no authenticationRef, no awsRegion.
resource "kubernetes_manifest" "keda_vllm_scaler" {
  manifest = {
    apiVersion = "keda.sh/v1alpha1"
    kind       = "ScaledObject"
    metadata = {
      name      = "vllm-engine-scaler"
      namespace = kubernetes_namespace_v1.vllm.metadata[0].name
    }
    spec = {
      scaleTargetRef = {
        apiVersion = "apps/v1"
        kind       = "Deployment"
        name       = "vllm-stack-qwen25-7b-deployment-vllm"
      }
      minReplicaCount = 2
      maxReplicaCount = 4
      pollingInterval = 5
      cooldownPeriod  = 30

      # Damp HPA reactivity. Without these, scale-up fired in <60s on any
      # transient queue/shed spike from cold warmup — well before the real
      # sustained-load phase began.
      #
      #   scaleUp:
      #     stabilizationWindowSeconds: 60
      #       HPA keeps a 60s window of "desired replicas" and uses the MIN.
      #       Brief blips (queue=3 for 5s during a Poisson burst) don't move
      #       the floor; only persistent pressure does.
      #     policies: max +1 pod per 30s
      #       Scale 2 → 3 → 4 takes ≥60s of sustained pressure, not one tick.
      #   scaleDown: keep K8s defaults (5min stabilization, gentle 1-pod step).
      advanced = {
        horizontalPodAutoscalerConfig = {
          behavior = {
            scaleUp = {
              stabilizationWindowSeconds = 60
              policies = [{
                type          = "Pods"
                value         = 1
                periodSeconds = 30
              }]
            }
            scaleDown = {
              stabilizationWindowSeconds = 300
              policies = [{
                type          = "Pods"
                value         = 1
                periodSeconds = 60
              }]
            }
          }
        }
      }

      # Two triggers, OR'd by KEDA — HPA scales to the MAX desiredReplicas
      # any trigger computes. The pair composes the outer control loop:
      #
      #   - queue-depth (proactive): the gateway is queueing requests because
      #     the inner AIMD loop has saturated the current `num_workers`. Fires
      #     *before* the system starts shedding.
      #   - shed-rate (defensive backstop): users are getting 504s because the
      #     queue overflowed. The system has crossed from "tight" to "failing."
      #
      # Both are gateway-side metrics. Scaling on user-facing SLO signals (queue
      # backpressure + actual shedding) rather than on in-flight count — which
      # confounds capacity with workload shape (per-request time depends on
      # input/output token lengths, so in-flight = rps × per-request-time
      # measures workload shape, not user demand).
      triggers = [
        {
          type = "prometheus"
          name = "queue-depth"
          metadata = {
            serverAddress = local.amp_proxy_url
            # Total queued requests across all tenants. queue_max=2/1/1=4 in
            # the current config, so this maxes at 4. Threshold=1 means
            # KEDA wants desired = ceil(queue / 1) replicas — scale-up
            # kicks in once queue_depth exceeds current min replicas.
            # `clamp_min(..., 0)` guards against accounting bugs that let a
            # per-tenant queue gauge go negative — without it, HPA wraps a
            # negative external-metric value to int64.maxvalue and instantly
            # scales to max. Should never trigger; defense in depth.
            query      = "sum(clamp_min(gateway_queue_depth{job=\"fastapi-gateway\"}, 0))"
            threshold  = "1"
            metricName = "gateway_queue_depth_sum"
          }
        },
        {
          type = "prometheus"
          name = "shed-rate"
          metadata = {
            serverAddress = local.amp_proxy_url
            # 504s per second across all tenants. Window=60s smooths transient
            # spikes from Poisson burstiness in the bench client. Threshold=1
            # per replica: at 2 engines, sustained shed >2/s triggers; at
            # 4 engines, >4/s. Combined with the scaleUp stabilization above,
            # a real saturation event has to persist before HPA reacts.
            query      = "sum(rate(gateway_requests_total{status=\"504\",job=\"fastapi-gateway\"}[60s]))"
            threshold  = "1"
            metricName = "gateway_shed_rate"
          }
        },
      ]
    }
  }

  depends_on = [
    kubernetes_service_v1.sigv4_proxy,
    helm_release.keda,
  ]
}
