variable "region" {
  description = "AWS region (must match Sub-project A)"
  type        = string
  default     = "us-east-1"
}

variable "loadbalancer_source_ranges" {
  description = "CIDRs allowed to hit the public NLB (your laptop's public IP /32)"
  type        = list(string)
}

variable "huggingface_token" {
  description = "HuggingFace access token used at build time to download model weights into the vLLM image. Set via terraform.tfvars (gitignored) or TF_VAR_huggingface_token env. Build-time only; BuildKit `--secret` keeps it out of the image's layer history."
  type        = string
  sensitive   = true
}

# GPU scale knob: vLLM engine + headroom warm-pool replicas drive Karpenter
# GPU node count (one g6.2xlarge per pod via per-host anti-affinity). Set both
# to 0 to take GPU spend to ~$0; Karpenter consolidates empty nodes.
variable "vllm_replicas" {
  description = "Number of vLLM engine pods. Each consumes one g6.2xlarge."
  type        = number
  default     = 2
}

variable "headroom_replicas" {
  description = "Number of warm-pool placeholder pods that hold GPU nodes ready for engine preemption. Each consumes one g6.2xlarge."
  type        = number
  default     = 2
}

