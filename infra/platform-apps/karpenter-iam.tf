data "aws_iam_policy_document" "karpenter_controller_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "karpenter_controller" {
  name               = "platform-apps-karpenter-controller"
  assume_role_policy = data.aws_iam_policy_document.karpenter_controller_assume.json
}

# Permissions Karpenter needs to discover capacity, launch / terminate nodes,
# and manage instance profiles. This is the un-scoped sandbox version: in
# production these statements would be tightened with `Condition` clauses
# scoping ec2:RunInstances and ec2:TerminateInstances to resources tagged
# `karpenter.sh/nodepool` (see Karpenter's getting-started CFN template).
data "aws_iam_policy_document" "karpenter_controller_policy" {
  statement {
    sid    = "EC2WriteOps"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
      "ec2:CreateLaunchTemplate",
      "ec2:DeleteLaunchTemplate",
      "ec2:TerminateInstances",
      "ec2:CreateTags",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EC2Read"
    effect = "Allow"
    actions = [
      "ec2:DescribeImages",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeSpotPriceHistory",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "SSMReadAMIParameters"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:*::parameter/aws/service/*"]
  }

  statement {
    sid       = "PricingRead"
    effect    = "Allow"
    actions   = ["pricing:GetProducts"]
    resources = ["*"]
  }

  statement {
    sid       = "PassNodeRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [data.terraform_remote_state.foundation.outputs.node_iam_role_arn]
  }

  statement {
    sid       = "EKSDescribe"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster"]
    resources = [data.terraform_remote_state.foundation.outputs.cluster_arn]
  }

  # Karpenter creates / manages an instance profile per EC2NodeClass.
  # `List*` are needed by the instance-profile garbage collector that scans
  # for orphaned profiles tagged with the cluster name.
  statement {
    sid    = "InstanceProfileLifecycle"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:ListInstanceProfiles",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "karpenter_controller" {
  name   = "karpenter-controller"
  role   = aws_iam_role.karpenter_controller.id
  policy = data.aws_iam_policy_document.karpenter_controller_policy.json
}

resource "aws_eks_pod_identity_association" "karpenter_controller" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = "kube-system"
  service_account = "karpenter"
  role_arn        = aws_iam_role.karpenter_controller.arn
}
