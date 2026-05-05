variable "region" {
  description = "AWS region (must match Sub-project A)"
  type        = string
  default     = "us-east-1"
}

variable "loadbalancer_source_ranges" {
  description = "CIDRs allowed to hit the public NLB (your laptop's public IP /32)"
  type        = list(string)
}

