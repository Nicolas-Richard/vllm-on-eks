resource "aws_eks_node_group" "gpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "gpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [local.workload_subnet_id]

  instance_types = ["g6.2xlarge"]
  ami_type       = "AL2023_x86_64_NVIDIA"
  capacity_type  = "ON_DEMAND"
  disk_size      = 100

  scaling_config {
    desired_size = var.gpu_desired_size
    min_size     = var.gpu_desired_size
    max_size     = 2
  }

  labels = {
    "workload"               = "gpu"
    "nvidia.com/gpu.present" = "true"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
    aws_iam_role_policy_attachment.node_ssm,
  ]
}
