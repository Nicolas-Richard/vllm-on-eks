output "ssm_parameter_arn" {
  value       = aws_ssm_parameter.hello.arn
  description = "ARN of the test SSM parameter"
}
