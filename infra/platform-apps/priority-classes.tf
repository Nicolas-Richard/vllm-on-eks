# Engine: highest non-system priority. Preempts headroom (and any other pod
# with priority < 1000). Below k8s system-cluster-critical (2000000000) so we
# never compete with kube-system.
resource "kubernetes_priority_class_v1" "vllm_engine" {
  metadata {
    name = "vllm-engine"
  }
  value             = 1000
  preemption_policy = "PreemptLowerPriority"
  description       = "vLLM engine pods. Preempts headroom to claim a GPU node when capacity is tight."
}

# Headroom: lowest priority. Never preempts (fills, doesn't fight).
resource "kubernetes_priority_class_v1" "vllm_headroom" {
  metadata {
    name = "vllm-headroom"
  }
  value             = -1000
  preemption_policy = "Never"
  description       = "Low-priority placeholder pods that hold GPU nodes warm; engine pods preempt these on demand."
}
