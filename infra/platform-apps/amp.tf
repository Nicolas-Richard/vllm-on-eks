resource "aws_prometheus_workspace" "main" {
  alias = "platform-apps"

  # AWS auto-adds tag "AMPAgentlessScraper" historically; harmless to keep
  # ignoring tags so future managed tags don't churn the plan.
  lifecycle {
    ignore_changes = [tags, tags_all]
  }
}
