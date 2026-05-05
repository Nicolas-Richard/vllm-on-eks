resource "kubernetes_config_map_v1" "gateway_tenants" {
  metadata {
    name      = "fastapi-gateway-tenants"
    namespace = kubernetes_namespace_v1.vllm.metadata[0].name
  }

  data = {
    "tenants.yaml" = file("${path.module}/../../config/tenants.yaml")
  }
}
