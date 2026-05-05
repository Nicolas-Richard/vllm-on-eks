resource "kubernetes_secret_v1" "gateway_auth" {
  metadata {
    name      = "gateway-auth"
    namespace = kubernetes_namespace_v1.vllm.metadata[0].name
  }

  data = {
    "bearer-token" = var.gateway_bearer_token
  }

  type = "Opaque"
}
