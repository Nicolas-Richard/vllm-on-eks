output "runner_namespace" {
  description = "Namespace hosting the benchmarks runner pod"
  value       = kubernetes_namespace_v1.benchmarks.metadata[0].name
}

output "runner_pod_label_selector" {
  description = "Label selector for the runner pod (use with kubectl -l)"
  value       = "app=benchmarks-runner"
}

output "router_internal_url" {
  description = "In-cluster vLLM router URL (router-direct path) — passthrough from platform-apps"
  value       = data.terraform_remote_state.platform_apps.outputs.router_internal_url
}

output "gateway_internal_url" {
  description = "In-cluster FastAPI gateway URL (ClusterIP, not the public NLB)"
  value       = "http://fastapi-gateway.vllm.svc.cluster.local:80"
}

output "runner_exec_cmd" {
  description = "One-liner to drop into a shell inside the runner pod"
  value       = "kubectl -n ${kubernetes_namespace_v1.benchmarks.metadata[0].name} exec -it deploy/benchmarks-runner -- bash"
}
