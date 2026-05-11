resource "kubernetes_namespace_v1" "vllm" {
  metadata {
    name = "vllm"
    labels = {
      "app.kubernetes.io/managed-by" = "platform-apps"
    }
  }
}

resource "helm_release" "vllm_stack" {
  name       = "vllm-stack"
  namespace  = kubernetes_namespace_v1.vllm.metadata[0].name
  repository = "https://vllm-project.github.io/production-stack"
  chart      = "vllm-stack"
  version    = "0.1.10"

  timeout = 900

  values = [yamlencode({
    servingEngineSpec = {
      modelSpec = [{
        name = "qwen25-7b"
        # ECR image with Qwen2.5-7B weights baked in. Cold-pod start drops
        # from ~10 min (image pull + HF download + load) to ~25–40 s
        # (image already cached on the node by the headroom pod, weights
        # load from local FS) — that's what makes the rolling-headroom
        # scale-up event visible inside a 5-min demo window.
        repository    = aws_ecr_repository.vllm_qwen25_7b.repository_url
        tag           = local.vllm_image_tag
        modelURL      = "/models/qwen25-7b" # local FS path inside the baked image
        replicaCount  = var.vllm_replicas
        requestCPU    = 4
        requestMemory = "16Gi"
        requestGPU    = 1
        vllmConfig = {
          enablePrefixCaching = true
          maxModelLen         = 8192
          dtype               = "bfloat16"
          tensorParallelSize  = 1
          extraArgs = [
            "--gpu-memory-utilization=0.90",
            # Keep the served model name stable across the Docker Hub →
            # baked-ECR cutover. Without this, vLLM advertises the load
            # path (`/models/qwen25-7b`) and the router 503s every request
            # whose `model:` field is the friendly HF name.
            "--served-model-name=Qwen/Qwen2.5-7B-Instruct",
          ]
        }
      }]

      containerSecurityContext = {
        runAsNonRoot = false
      }

      # Stricter engine probes so EndpointSlice ready=True doesn't fire
      # before the model is actually serving.
      #
      # Chart defaults all probes to GET /health on :8000, but vLLM's
      # /health returns 200 the moment uvicorn binds — *before* the
      # engine has loaded weights and registered the model. That made
      # WorkerCapacityWatcher grow num_workers ~30–60s prematurely;
      # gateway routed requests to a "Ready" pod that couldn't serve,
      # AIMD reacted to climbing TTFT, queue piled, system thrashed.
      #
      # /v1/models is registered only after the engine has the model
      # loaded — much closer to "actually serving." 60s readiness delay
      # adds margin for weights load + CUDA graph capture. Liveness
      # stays on /health (we don't want a busy engine liveness-killed
      # mid-inference).
      startupProbe = {
        httpGet             = { path = "/v1/models", port = 8000 }
        initialDelaySeconds = 30
        periodSeconds       = 5
        failureThreshold    = 60
      }
      readinessProbe = {
        httpGet             = { path = "/v1/models", port = 8000 }
        initialDelaySeconds = 30
        periodSeconds       = 5
        failureThreshold    = 3
      }

      # High-priority class so a new engine pod can preempt a low-priority
      # `vllm-headroom` placeholder when capacity is tight (rolling-headroom
      # scale-up). `vllm-headroom` PriorityClass has `preemptionPolicy: Never`
      # so headroom can never preempt anything — preemption only flows
      # downhill from engine to headroom.
      priorityClassName = kubernetes_priority_class_v1.vllm_engine.metadata[0].name

      nodeSelector = {
        workload = "gpu"
      }

      tolerations = [{
        key      = "nvidia.com/gpu"
        operator = "Exists"
        effect   = "NoSchedule"
      }]

      podAntiAffinity = {
        requiredDuringSchedulingIgnoredDuringExecution = [{
          topologyKey = "kubernetes.io/hostname"
          labelSelector = {
            matchLabels = {
              model = "qwen25-7b"
            }
          }
        }]
      }
    }

    routerSpec = {
      replicaCount = 1
      # Round-robin not prefix-aware: the lmcache router's prefix trie is
      # built at engine-discovery time and does not pick up engines that
      # join later, so prefix-aware routing strands traffic on the original
      # engines whenever HPA / Karpenter adds capacity. Round-robin is the
      # honest setting for an autoscaled multi-tenant demo; the post #5
      # writeup should call out the KV-cache-locality trade-off.
      routingLogic         = "roundrobin"
      engineScrapeInterval = 15
      requestStatsWindow   = 60
      serviceType          = "ClusterIP"
      resources = {
        requests = { cpu = "500m", memory = "512Mi" }
        limits   = { cpu = "2", memory = "2Gi" }
      }
      nodeSelector = {
        workload = "cpu"
      }

      # Chart 0.1.10 ships a too-tight router startup probe (5s/5s/3 — only
      # ~20s before kill), but the lmcache router needs ~15s to bind 8000.
      # Give it 5 min of grace, matching the engineSpec defaults.
      startupProbe = {
        initialDelaySeconds = 15
        periodSeconds       = 10
        failureThreshold    = 30
        httpGet = {
          path = "/health"
          port = 8000
        }
      }
    }
  })]

  depends_on = [
    terraform_data.vllm_image,
    kubernetes_priority_class_v1.vllm_engine,
  ]
}
