resource "kubernetes_service_v1" "gateway_lb" {
  metadata {
    name      = "fastapi-gateway-lb"
    namespace = kubernetes_namespace_v1.vllm.metadata[0].name
    annotations = {
      "service.beta.kubernetes.io/aws-load-balancer-type"                              = "nlb"
      "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled" = "true"
    }
  }

  spec {
    type                        = "LoadBalancer"
    load_balancer_source_ranges = var.loadbalancer_source_ranges
    selector                    = { app = "fastapi-gateway" }

    port {
      port        = 80
      target_port = 8000
      protocol    = "TCP"
    }
  }

  depends_on = [helm_release.fastapi_gateway]
}
