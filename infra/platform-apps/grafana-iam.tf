data "aws_iam_policy_document" "grafana_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "grafana_amp" {
  name               = "platform-apps-grafana-amp"
  assume_role_policy = data.aws_iam_policy_document.grafana_assume.json
}

resource "aws_iam_role_policy" "grafana_amp" {
  name = "amp-query"
  role = aws_iam_role.grafana_amp.id
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

resource "aws_eks_pod_identity_association" "grafana" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = kubernetes_namespace_v1.monitoring.metadata[0].name
  service_account = "grafana-sa"
  role_arn        = aws_iam_role.grafana_amp.arn
}
