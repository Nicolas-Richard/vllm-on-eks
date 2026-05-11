locals {
  gateway_src_dir = "${path.module}/../../apps/fastapi-gateway"

  # Files baked into the image (matches the Dockerfile's COPY set; tests/ is excluded).
  gateway_image_inputs = sort(setunion(
    fileset(local.gateway_src_dir, "Dockerfile"),
    fileset(local.gateway_src_dir, "pyproject.toml"),
    fileset(local.gateway_src_dir, "uv.lock"),
    fileset(local.gateway_src_dir, "app/**"),
  ))

  gateway_image_tag = substr(sha256(join("", [
    for f in local.gateway_image_inputs : filesha256("${local.gateway_src_dir}/${f}")
  ])), 0, 12)
}

resource "terraform_data" "fastapi_image" {
  triggers_replace = {
    tag  = local.gateway_image_tag
    repo = aws_ecr_repository.fastapi.repository_url
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      REPO='${aws_ecr_repository.fastapi.repository_url}'
      TAG='${local.gateway_image_tag}'
      REGISTRY="$${REPO%%/*}"
      aws ecr get-login-password --region '${var.region}' \
        | docker login --username AWS --password-stdin "$REGISTRY"
      docker build --platform linux/amd64 -t "$REPO:$TAG" '${local.gateway_src_dir}'
      docker push "$REPO:$TAG"
    EOT
  }
}

resource "helm_release" "fastapi_gateway" {
  name      = "fastapi-gateway"
  namespace = kubernetes_namespace_v1.vllm.metadata[0].name
  chart     = "${path.module}/../../charts/fastapi-gateway"

  values = [yamlencode({
    image = {
      repository = aws_ecr_repository.fastapi.repository_url
      tag        = local.gateway_image_tag
      pullPolicy = "IfNotPresent"
    }

    replicaCount = 1

    tenantsConfigMap = {
      name      = kubernetes_config_map_v1.gateway_tenants.metadata[0].name
      key       = "tenants.yaml"
      mountPath = "/etc/gateway"
    }

    tenantKeysSecret = {
      name = kubernetes_secret_v1.gateway_tenant_keys.metadata[0].name
    }

    router = {
      url = "http://vllm-stack-router-service.vllm.svc.cluster.local:80"
    }

    nodeSelector = {
      workload = "cpu"
    }

    # Sized for the noisy-neighbor demo: caps-OFF lets thousands of requests
    # become concurrent, each holding an httpx connection + buffers. 1Gi was
    # OOMKilled mid-run; 2Gi survives. (A future refactor reusing one
    # AsyncClient across requests would lower the steady-state footprint.)
    resources = {
      # Doubled CPU request from 200m → 400m. Under sustained load the
      # gateway's asyncio event loop genuinely needs more guaranteed CPU than
      # 200m can provide — kubelet was throttling the process enough that
      # /healthz couldn't get scheduled within the probe timeout.
      requests = { cpu = "400m", memory = "256Mi" }
      limits   = { cpu = "2", memory = "2Gi" }
    }

    service = {
      type       = "ClusterIP"
      port       = 80
      targetPort = 8000
    }
  })]

  depends_on = [
    kubernetes_secret_v1.gateway_tenant_keys,
    kubernetes_config_map_v1.gateway_tenants,
    helm_release.vllm_stack,
    terraform_data.fastapi_image,
  ]
}
