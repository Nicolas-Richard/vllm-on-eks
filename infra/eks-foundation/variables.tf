variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "nico-sdbx"
}

variable "workload_az" {
  description = "Single AZ where CPU and GPU node groups run. EKS still requires subnets in 2 AZs."
  type        = string
  default     = "us-east-1a"
}
