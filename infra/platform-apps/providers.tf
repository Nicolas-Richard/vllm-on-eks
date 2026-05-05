provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Owner     = "nicolas.richard"
      Project   = "nico-sdbx"
      ManagedBy = "terraform-local"
      Component = "platform-apps"
    }
  }
}

provider "kubernetes" {
  host                   = data.terraform_remote_state.foundation.outputs.cluster_endpoint
  cluster_ca_certificate = base64decode(data.terraform_remote_state.foundation.outputs.cluster_ca_certificate)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  kubernetes = {
    host                   = data.terraform_remote_state.foundation.outputs.cluster_endpoint
    cluster_ca_certificate = base64decode(data.terraform_remote_state.foundation.outputs.cluster_ca_certificate)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}
