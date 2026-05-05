Yes — for v1, the easiest path is:

Use Terraform as the single entrypoint and let it do all of this:

* create the EKS cluster
* install EKS-managed add-ons
* install Helm charts
* deploy your apps

You do not need to introduce GitOps yet.

Terraform’s Helm provider is explicitly meant to deploy applications into Kubernetes, and HashiCorp documents provisioning the cluster and deploying Helm charts in the same Terraform workflow. AWS also supports managing many cluster components as EKS add-ons. vLLM Production Stack itself is installed via Helm.  ￼

So the simplest operating model is:

Recommended v1 deployment model

Use one Terraform repo with three layers:

1. Foundation (Sub-project A — built)

Terraform creates:

* VPC
* subnets (single AZ for workloads, public-only — no NAT)
* IAM
* EKS cluster (v1.35)
* CPU + GPU node groups
* IAM OIDC provider (for IRSA) and EKS Pod Identity agent

DynamoDB and ECR are deferred to a later sub-project (C), where they pair with the benchmark workload and FastAPI image build.

2. Cluster add-ons (Sub-project A — built)

Terraform installs:

* EKS-managed add-ons: vpc-cni, coredns, kube-proxy, aws-ebs-csi-driver, eks-pod-identity-agent
* NVIDIA device plugin via Helm (so `nvidia.com/gpu` is schedulable)
* EKS Access Entries (API auth mode — replaces aws-auth ConfigMap)

3. Apps (Sub-project B)

Terraform deploys:

* vLLM Production Stack via Helm
* DCGM exporter via Helm (GPU metrics)
* Grafana via Helm (stateless — dashboards as ConfigMaps, data source = AMP via sigv4)
* your FastAPI service via Helm
* AMP workspace + AMP scraper as AWS resources (managed Prometheus storage and scrape agent — replaces self-hosted Prometheus)

That means your lifecycle is just:

* terraform apply → bring everything up
* terraform destroy or scale-down variables → tear down compute

How to install the add-ons

Use two mechanisms, not one:

Use EKS add-ons for AWS-native cluster components

Examples:

* VPC CNI
* CoreDNS
* kube-proxy
* EBS CSI driver (installed; may become optional in B if no PVCs are needed)
* EKS Pod Identity agent (preferred auth pattern — see note below)

AWS documents EKS add-ons as the managed way to install and operate common operational software on EKS clusters.  ￼

In Terraform terms, these are the things I would prefer to manage with AWS/EKS-native resources.

Pod Identity vs IRSA: A uses Pod Identity (newer, simpler, cluster-independent trust policies) for the EBS CSI controller. B will reuse the same pattern for the AMP scraper, HF token retrieval, etc. IRSA is still available (the OIDC provider is created) for any workload that needs it.

Use Helm for platform software and app software

Examples:

* NVIDIA device plugin (already installed in A)
* DCGM exporter (GPU metrics — what AMP scrapes)
* Grafana (stateless, queries AMP via sigv4)
* vLLM Production Stack
* your own chart for FastAPI

Observability storage is managed AWS, not Helm: AMP workspace + AMP scraper are Terraform AWS resources. There is no in-cluster Prometheus.

HashiCorp's Helm provider supports this directly, and vLLM Production Stack is installed as a Helm chart.  ￼

How to run your apps

The easiest answer is:

* package FastAPI as a Docker image
* deploy it to Kubernetes as a Deployment + Service
* manage that deployment with either:
    * a small Helm chart, or
    * Terraform Kubernetes resources directly

For v1, I would choose:

Helm for everything app-like, including your FastAPI service.

Why:

* keeps one mental model
* easier upgrades and rollback behavior
* cleaner values-based config
* consistent with Production Stack

Terraform can call Helm, so you still keep the “single terraform apply” workflow.  ￼

Should you use GitOps?

Not for this first version.

GitOps is great when:

* multiple people are changing the cluster often
* you want automatic drift reconciliation
* you already have Argo CD or Flux operating standards

But for a sandbox prototype, GitOps is more moving parts:

* another controller
* another deployment path
* another thing to debug
* another repo structure decision

You said you want the easiest solution possible. That strongly points to:

Terraform only for v1.

Later, if this becomes a shared internal platform or long-lived environment, you can move to:

* Terraform for infrastructure
* GitOps for in-cluster apps

But that is a maturity step, not a starting requirement.

My concrete recommendation

Use this split:

Managed by Terraform with AWS resources

* VPC
* EKS cluster
* node groups
* IAM roles + OIDC provider (for IRSA)
* Pod Identity associations (preferred auth for new workloads)
* EKS Access Entries (cluster admin assignment)
* security groups
* EKS add-ons
* AMP workspace + AMP scraper

DynamoDB and ECR are introduced in Sub-project C, not the foundation.

Managed by Terraform through Helm provider

* NVIDIA device plugin
* DCGM exporter
* Grafana (stateless)
* vLLM Production Stack
* FastAPI app chart

There is no in-cluster Prometheus. There is no ingress/gateway controller in the default plan — gateway exposure is a single LoadBalancer Service (NLB) for v1; revisit if multi-service routing is needed.

That gives you a single command path without GitOps.

One practical caveat

Even though you can do everything in one terraform apply, I would still structure it as two Terraform stages logically:

Stage A: cluster bootstrap

* create EKS
* create node groups
* install core EKS add-ons

Stage B: in-cluster software

* Helm releases
* apps
* Production Stack
* observability

Why: the Helm and Kubernetes providers need working cluster access and stable auth details, and Terraform’s dependency graph can handle ordering, but keeping bootstrap and apps somewhat separated makes failures easier to diagnose. HashiCorp documents configuring Helm/Kubernetes providers from the newly created cluster, and Terraform will order creation accordingly.  ￼

So I would say:

* one repo
* one workflow
* but probably two root modules or two apply targets

That is still much simpler than full GitOps.

What I would do for your project

I would implement it like this:

* infra/eks-foundation (BUILT)
    * VPC (single workload AZ, public subnets only)
    * EKS cluster (v1.35)
    * CPU node group (t3.large)
    * GPU node group (G6/L4, scale-to-zero via `gpu_desired_size` var)
    * EKS-managed add-ons (vpc-cni, coredns, kube-proxy, aws-ebs-csi-driver, eks-pod-identity-agent)
    * IAM/OIDC + Pod Identity association for ebs-csi-controller-sa
    * EKS Access Entries
    * NVIDIA device plugin (Helm)
* infra/platform-apps (Sub-project B)
    * AMP workspace + AMP scraper (AWS resources)
    * Pod Identity association for AMP scraper SA
    * DCGM exporter Helm release
    * Grafana Helm release (stateless)
    * vLLM Production Stack Helm release
    * FastAPI Helm release
    * LoadBalancer Service (NLB) for gateway
* infra/benchmarks (Sub-project C — later)
    * DynamoDB request log
    * ECR repo for FastAPI image (if not in B)
    * benchmark workload definitions

Then your operator flow becomes:

1. apply foundation
2. apply platform-apps
3. benchmark (apply Sub-project C)
4. scale GPU node group to zero between sessions: `terraform apply -var gpu_desired_size=0` in foundation
5. destroy when done (in reverse order)
