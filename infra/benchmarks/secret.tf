data "kubernetes_secret_v1" "gateway_auth_source" {
  metadata {
    name      = "gateway-auth"
    namespace = "vllm"
  }
}

resource "kubernetes_secret_v1" "gateway_auth_local" {
  metadata {
    name      = "gateway-auth"
    namespace = kubernetes_namespace_v1.benchmarks.metadata[0].name
  }

  data = {
    "BEARER_TOKEN" = data.kubernetes_secret_v1.gateway_auth_source.data["bearer-token"]
  }

  type = "Opaque"
}
