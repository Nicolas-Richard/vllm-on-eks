data "aws_iam_policy_document" "keda_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "keda_amp" {
  name               = "platform-apps-keda-amp"
  assume_role_policy = data.aws_iam_policy_document.keda_assume.json
}

# KEDA's prometheus scaler queries AMP with sigv4 signing via the operator's
# Pod Identity. Same surface as grafana — read-only AMP access, scoped to
# the platform-apps workspace.
resource "aws_iam_role_policy" "keda_amp" {
  name = "amp-query"
  role = aws_iam_role.keda_amp.id
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

resource "kubernetes_namespace_v1" "keda" {
  metadata {
    name = "keda"
    labels = {
      "app.kubernetes.io/managed-by" = "platform-apps"
    }
  }
}

resource "aws_eks_pod_identity_association" "keda" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = kubernetes_namespace_v1.keda.metadata[0].name
  service_account = "keda-operator"
  role_arn        = aws_iam_role.keda_amp.arn
}
