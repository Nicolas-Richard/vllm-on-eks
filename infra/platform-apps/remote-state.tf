data "terraform_remote_state" "foundation" {
  backend = "local"

  config = {
    path = "${path.module}/../eks-foundation/terraform.tfstate"
  }
}

data "aws_eks_cluster_auth" "this" {
  name = data.terraform_remote_state.foundation.outputs.cluster_name
}
