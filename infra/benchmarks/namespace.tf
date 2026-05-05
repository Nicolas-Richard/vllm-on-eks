resource "kubernetes_namespace_v1" "benchmarks" {
  metadata {
    name = var.namespace_name

    labels = {
      "app.kubernetes.io/managed-by" = "benchmarks"
    }
  }
}
