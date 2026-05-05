resource "aws_ecr_repository" "fastapi" {
  name                 = "platform-apps/fastapi-gateway"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}
