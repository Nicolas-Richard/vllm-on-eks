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

variable "gpu_desired_size" {
  description = "Number of GPU nodes. Set to 0 to scale the GPU node group down without destroying it."
  type        = number
  default     = 2

  validation {
    condition     = var.gpu_desired_size >= 0 && var.gpu_desired_size <= 2
    error_message = "gpu_desired_size must be between 0 and 2 (max_size is 2)."
  }
}

variable "workload_az" {
  description = "Single AZ where CPU and GPU node groups run. EKS still requires subnets in 2 AZs."
  type        = string
  default     = "us-east-1a"
}
