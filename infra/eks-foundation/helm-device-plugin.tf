# Required for any GPU pod that sets `runtimeClassName: nvidia` (e.g. the
# vLLM Production Stack workers in platform-apps). EKS GPU AMIs already
# wire `nvidia` as a containerd runtime handler; this just registers the
# matching RuntimeClass object so the API server lets such pods schedule.
resource "kubernetes_runtime_class_v1" "nvidia" {
  metadata {
    name = "nvidia"
  }
  handler = "nvidia"
}

resource "helm_release" "nvidia_device_plugin" {
  name       = "nvidia-device-plugin"
  namespace  = "kube-system"
  repository = "https://nvidia.github.io/k8s-device-plugin"
  chart      = "nvidia-device-plugin"
  version    = "0.16.2"

  values = [yamlencode({
    nodeSelector = {
      "nvidia.com/gpu.present" = "true"
    }
    tolerations = [
      {
        key      = "nvidia.com/gpu"
        operator = "Exists"
        effect   = "NoSchedule"
      }
    ]
  })]

  depends_on = [
    aws_eks_addon.vpc_cni,
    aws_eks_addon.coredns,
    aws_eks_addon.kube_proxy,
  ]
}
