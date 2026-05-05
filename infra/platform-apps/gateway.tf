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

    replicaCount = 2

    bearerSecret = {
      name = kubernetes_secret_v1.gateway_auth.metadata[0].name
      key  = "bearer-token"
    }

    router = {
      url = "http://vllm-stack-router-service.vllm.svc.cluster.local:80"
    }

    nodeSelector = {
      workload = "cpu"
    }

    service = {
      type       = "ClusterIP"
      port       = 80
      targetPort = 8000
    }
  })]

  depends_on = [
    kubernetes_secret_v1.gateway_auth,
    helm_release.vllm_stack,
    terraform_data.fastapi_image,
  ]
}
