locals {
  vllm_image_dir  = "${path.module}/vllm-image"
  vllm_model_repo = "Qwen/Qwen2.5-7B-Instruct"

  # Tag is content-addressed: hash of Dockerfile + model repo + corp-ca bundle.
  # A model swap, Dockerfile edit, or rotated corp CA produces a new tag and
  # triggers a rebuild; an unrelated apply does not. corp-ca.crt is regenerated
  # by `make vllm-image` from /Library/Keychains/System.keychain — fileexists()
  # gate keeps `terraform plan` working even if it hasn't been extracted yet.
  vllm_image_tag = substr(sha256(join("|", [
    filesha256("${local.vllm_image_dir}/Dockerfile"),
    local.vllm_model_repo,
    fileexists("${local.vllm_image_dir}/corp-ca.crt") ? filesha256("${local.vllm_image_dir}/corp-ca.crt") : "no-corp-ca",
  ])), 0, 12)
}

resource "aws_ecr_repository" "vllm_qwen25_7b" {
  name                 = "platform-apps/vllm-qwen25-7b"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "local_sensitive_file" "hf_token" {
  filename        = "${path.module}/.hf-token"
  content         = trimspace(var.huggingface_token)
  file_permission = "0600"
}

resource "terraform_data" "vllm_image" {
  triggers_replace = {
    tag  = local.vllm_image_tag
    repo = aws_ecr_repository.vllm_qwen25_7b.repository_url
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      REPO='${aws_ecr_repository.vllm_qwen25_7b.repository_url}'
      TAG='${local.vllm_image_tag}'
      MODEL_REPO='${local.vllm_model_repo}'
      TOKEN_FILE="$(realpath '${local_sensitive_file.hf_token.filename}')"
      REGISTRY="$${REPO%%/*}"

      {
        echo "DEBUG: token file=$TOKEN_FILE"
        if [ -e "$TOKEN_FILE" ]; then
          echo "DEBUG: token file size=$(wc -c < "$TOKEN_FILE")"
          echo "DEBUG: token file first char=$(head -c 1 "$TOKEN_FILE")"
          echo "DEBUG: pwd=$(pwd)"
        else
          echo "DEBUG: token file does not exist"
        fi
      } > /tmp/vllm-build-debug.log 2>&1

      if [ ! -s "$TOKEN_FILE" ]; then
        echo "ERROR: $TOKEN_FILE is empty/missing. Check huggingface_token in terraform.tfvars" >&2
        exit 1
      fi

      aws ecr get-login-password --region '${var.region}' \
        | docker login --username AWS --password-stdin "$REGISTRY"

      DOCKER_BUILDKIT=1 docker build \
        --platform linux/amd64 \
        --secret id=hf_token,src="$TOKEN_FILE" \
        --build-arg MODEL_REPO="$MODEL_REPO" \
        -t "$REPO:$TAG" \
        '${local.vllm_image_dir}'
      docker push "$REPO:$TAG"
    EOT
  }

  depends_on = [local_sensitive_file.hf_token]
}
