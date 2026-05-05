output "gateway_url" {
  description = "Public NLB DNS for the FastAPI gateway"
  value       = "http://${kubernetes_service_v1.gateway_lb.status[0].load_balancer[0].ingress[0].hostname}"
}

output "gateway_bearer_token" {
  description = "Bearer token for Authorization header"
  value       = var.gateway_bearer_token
  sensitive   = true
}

output "ecr_url" {
  description = "ECR repository URL for FastAPI image push"
  value       = aws_ecr_repository.fastapi.repository_url
}

output "amp_workspace_id" {
  description = "AMP workspace ID"
  value       = aws_prometheus_workspace.main.id
}

output "amp_query_endpoint" {
  description = "AMP query endpoint URL (for awscurl debugging)"
  value       = aws_prometheus_workspace.main.prometheus_endpoint
}

output "grafana_port_forward_cmd" {
  description = "One-liner for the operator to open Grafana"
  value       = "kubectl port-forward -n monitoring svc/grafana 3000:80"
}

output "router_internal_url" {
  description = "In-cluster Production Stack router URL — used by Sub-project C for direct benchmarks"
  value       = "http://vllm-stack-router-service.vllm.svc.cluster.local:80"
}
