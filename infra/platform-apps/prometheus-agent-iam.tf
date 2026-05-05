data "aws_iam_policy_document" "prometheus_agent_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "prometheus_agent" {
  name               = "platform-apps-prometheus-agent"
  assume_role_policy = data.aws_iam_policy_document.prometheus_agent_assume.json
}

resource "aws_iam_role_policy" "prometheus_agent_amp_write" {
  name = "amp-remote-write"
  role = aws_iam_role.prometheus_agent.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["aps:RemoteWrite"]
      Resource = aws_prometheus_workspace.main.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "prometheus_agent" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = kubernetes_namespace_v1.monitoring.metadata[0].name
  service_account = "prometheus-agent-sa"
  role_arn        = aws_iam_role.prometheus_agent.arn
}
