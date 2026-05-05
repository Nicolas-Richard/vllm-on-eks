output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_ca_certificate" {
  description = "Base64-encoded cluster CA cert"
  value       = aws_eks_cluster.this.certificate_authority[0].data
  sensitive   = true
}

output "cluster_oidc_issuer" {
  description = "OIDC issuer URL for IRSA"
  value       = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

output "oidc_provider_arn" {
  description = "ARN of the IAM OIDC provider for IRSA"
  value       = aws_iam_openid_connect_provider.this.arn
}

output "region" {
  description = "AWS region"
  value       = var.region
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet IDs (used for ELBs and node placement)"
  value       = module.vpc.public_subnets
}

output "workload_subnet_id" {
  description = "Single-AZ subnet that hosts both CPU and GPU node groups"
  value       = local.workload_subnet_id
}

output "cpu_node_group_arn" {
  description = "ARN of the CPU managed node group"
  value       = aws_eks_node_group.cpu.arn
}

output "gpu_node_group_arn" {
  description = "ARN of the GPU managed node group"
  value       = aws_eks_node_group.gpu.arn
}

output "kubeconfig_command" {
  description = "Command to write kubeconfig for this cluster"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name} --profile <AWS_PROFILE>"
}

output "cluster_arn" {
  description = "EKS cluster ARN — consumed by aws_prometheus_scraper in Sub-project B"
  value       = aws_eks_cluster.this.arn
}
