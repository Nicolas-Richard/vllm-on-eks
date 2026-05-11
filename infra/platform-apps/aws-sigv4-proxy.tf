# KEDA's prometheus scaler can't reliably sigv4-sign AMP requests via EKS
# Pod Identity (community-known: signature mismatches even with valid creds).
# Standard fix: aws-sigv4-proxy as a forwarder — KEDA hits it as plain HTTP,
# the proxy uses its own Pod Identity to sigv4-sign and forwards to AMP.

data "aws_iam_policy_document" "sigv4_proxy_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sigv4_proxy" {
  name               = "platform-apps-sigv4-proxy"
  assume_role_policy = data.aws_iam_policy_document.sigv4_proxy_assume.json
}

resource "aws_iam_role_policy" "sigv4_proxy_amp" {
  name = "amp-query"
  role = aws_iam_role.sigv4_proxy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "aps:QueryMetrics",
        "aps:GetSeries",
        "aps:GetLabels",
        "aps:GetMetricMetadata",
      ]
      Resource = aws_prometheus_workspace.main.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "sigv4_proxy" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = kubernetes_namespace_v1.keda.metadata[0].name
  service_account = "aws-sigv4-proxy-sa"
  role_arn        = aws_iam_role.sigv4_proxy.arn
}

resource "kubernetes_service_account_v1" "sigv4_proxy" {
  metadata {
    name      = "aws-sigv4-proxy-sa"
    namespace = kubernetes_namespace_v1.keda.metadata[0].name
  }
}

resource "kubernetes_deployment_v1" "sigv4_proxy" {
  metadata {
    name      = "aws-sigv4-proxy"
    namespace = kubernetes_namespace_v1.keda.metadata[0].name
    labels = {
      app = "aws-sigv4-proxy"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "aws-sigv4-proxy"
      }
    }

    template {
      metadata {
        labels = {
          app = "aws-sigv4-proxy"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.sigv4_proxy.metadata[0].name

        node_selector = {
          workload = "cpu"
        }

        container {
          name  = "proxy"
          image = "public.ecr.aws/aws-observability/aws-sigv4-proxy:1.10"

          args = [
            "--name=aps",
            "--region=${var.region}",
            "--host=aps-workspaces.${var.region}.amazonaws.com",
          ]

          port {
            name           = "http"
            container_port = 8080
          }

          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { cpu = "200m", memory = "256Mi" }
          }
        }
      }
    }
  }

  depends_on = [aws_eks_pod_identity_association.sigv4_proxy]
}

resource "kubernetes_service_v1" "sigv4_proxy" {
  metadata {
    name      = "aws-sigv4-proxy"
    namespace = kubernetes_namespace_v1.keda.metadata[0].name
  }

  spec {
    type     = "ClusterIP"
    selector = { app = "aws-sigv4-proxy" }

    port {
      name        = "http"
      port        = 8080
      target_port = "http"
    }
  }
}
