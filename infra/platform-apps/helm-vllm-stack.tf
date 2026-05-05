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
        name          = "qwen25-7b"
        repository    = "vllm/vllm-openai"
        tag           = "v0.19.1"
        modelURL      = "Qwen/Qwen2.5-7B-Instruct"
        replicaCount  = 2
        requestCPU    = 4
        requestMemory = "16Gi"
        requestGPU    = 1
        vllmConfig = {
          enablePrefixCaching = true
          maxModelLen         = 8192
          dtype               = "bfloat16"
          tensorParallelSize  = 1
          extraArgs           = ["--gpu-memory-utilization=0.90"]
        }
      }]

      containerSecurityContext = {
        runAsNonRoot = false
      }

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
      replicaCount         = 1
      routingLogic         = "prefixaware"
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
}
