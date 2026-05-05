resource "random_password" "tenant_keys" {
  for_each = toset(["tenant-a", "tenant-b", "tenant-c"])
  length   = 32
  special  = false
}

resource "kubernetes_secret_v1" "gateway_tenant_keys" {
  metadata {
    name      = "fastapi-gateway-tenant-keys"
    namespace = kubernetes_namespace_v1.vllm.metadata[0].name
  }

  data = {
    TENANT_A_KEY = random_password.tenant_keys["tenant-a"].result
    TENANT_B_KEY = random_password.tenant_keys["tenant-b"].result
    TENANT_C_KEY = random_password.tenant_keys["tenant-c"].result
  }

  type = "Opaque"
}
