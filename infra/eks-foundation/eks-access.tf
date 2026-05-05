# Resolve the SSO-generated role ARN dynamically (the random suffix is
# account-specific). Filtering by the well-known path narrows it to the
# one Identity-Center-provisioned role.
data "aws_iam_roles" "okta_admin" {
  name_regex  = "AWSReservedSSO_Okta-Administrator_.*"
  path_prefix = "/aws-reserved/sso.amazonaws.com/"
}

locals {
  okta_admin_role_arn = tolist(data.aws_iam_roles.okta_admin.arns)[0]
}

resource "aws_eks_access_entry" "admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = local.okta_admin_role_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_eks_access_entry.admin.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}
