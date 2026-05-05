data "kubernetes_secret_v1" "tenant_keys_source" {
  metadata {
    name      = "fastapi-gateway-tenant-keys"
    namespace = "vllm"
  }
}

resource "kubernetes_secret_v1" "tenant_keys_local" {
  metadata {
    name      = "fastapi-gateway-tenant-keys"
    namespace = kubernetes_namespace_v1.benchmarks.metadata[0].name
  }

  data = {
    TENANT_A_KEY = data.kubernetes_secret_v1.tenant_keys_source.data["TENANT_A_KEY"]
    TENANT_B_KEY = data.kubernetes_secret_v1.tenant_keys_source.data["TENANT_B_KEY"]
    TENANT_C_KEY = data.kubernetes_secret_v1.tenant_keys_source.data["TENANT_C_KEY"]
  }

  type = "Opaque"
}
