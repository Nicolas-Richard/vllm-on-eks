resource "aws_ssm_parameter" "hello" {
  name        = "/nico-sdbx/hello"
  description = "First test parameter created from the standalone nico-sdbx repo"
  type        = "String"
  value       = "hello from terraform"
}
