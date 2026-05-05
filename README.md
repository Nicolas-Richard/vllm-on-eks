# vllm-on-eks

Companion repo for the blog post:
**[Streaming LLM inference on EKS](https://nicolas-richard.github.io/posts/streaming-llm-inference-on-eks.html)**

The infra splits into two Terraform sub-projects (`infra/eks-foundation` and
`infra/platform-apps`). Day-to-day workflows are wrapped in the root `Makefile`.

## Makefile targets

| Target | What it does |
| --- | --- |
| `make deploy` | Bootstraps the ECR repo, then runs the full `platform-apps` apply (builds and pushes the FastAPI gateway image, installs the Helm releases). |
| `make ecr-bootstrap` | Targeted apply that only creates `aws_ecr_repository.fastapi`. Needed once before the first full apply, since the image push depends on the repo existing. |
| `make terraform-apply` | Full `terraform apply` in `infra/platform-apps`. Image rebuild/push is handled inside Terraform via a `terraform_data` resource keyed on a content hash of `apps/fastapi-gateway`. |
| `make destroy` | `terraform destroy` in `infra/platform-apps` (leaves the EKS cluster from `eks-foundation` intact). |
| `make gateway-url` | Prints the public NLB URL for the gateway. |
| `make gateway-token` | Prints the bearer token. |
| `make gateway-info` | Prints both URL and token. |
| `make gateway-test` | Streams a chat completion and dumps the raw SSE chunks (debugging). |
| `make gateway-chat` | Streams a chat completion and prints just the assistant text to stdout. Override the prompt with `PROMPT="..."`. |

The `PROFILE` and `REGION` variables at the top of the `Makefile` are the
AWS profile and region used by the targets — adjust them to match your
environment before running anything.
