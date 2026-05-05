variable "region" {
  description = "AWS region (must match Sub-projects A and B)"
  type        = string
  default     = "us-east-1"
}

variable "namespace_name" {
  description = "Kubernetes namespace for the benchmarks runner"
  type        = string
  default     = "benchmarks"
}
