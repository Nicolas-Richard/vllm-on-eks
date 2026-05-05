SHELL          := /bin/bash
REGION         := us-east-1
PROFILE        := <AWS_PROFILE>
PLATFORM_DIR   := infra/platform-apps
FOUNDATION_DIR := infra/eks-foundation

.PHONY: deploy ecr-bootstrap terraform-apply destroy gateway-url gateway-token gateway-info gateway-test gateway-chat gpu-scale-down gpu-scale-up bench-sweep bench-sweep-gateway bench-c

PROMPT ?= Say hello in 5 words.
C      ?= 16

deploy: ecr-bootstrap terraform-apply

ecr-bootstrap:
	cd $(PLATFORM_DIR) && terraform apply \
	  -target=aws_ecr_repository.fastapi \
	  -auto-approve

# Terraform handles `docker build`/`docker push` itself via terraform_data.fastapi_image,
# keyed on a content hash of apps/fastapi-gateway. No -var needed.
terraform-apply:
	cd $(PLATFORM_DIR) && terraform apply -auto-approve

destroy:
	cd $(PLATFORM_DIR) && terraform destroy -auto-approve

gateway-url:
	@cd $(PLATFORM_DIR) && terraform output -raw gateway_url

gateway-token:
	@cd $(PLATFORM_DIR) && terraform output -raw tenant_keys_export | grep TENANT_A_KEY | cut -d"'" -f2

gateway-info:
	@printf 'URL:   %s\n' "$$($(MAKE) -s gateway-url)"
	@printf 'TOKEN: %s\n' "$$($(MAKE) -s gateway-token)"

gateway-test:
	@URL=$$($(MAKE) -s gateway-url); \
	  TOKEN=$$($(MAKE) -s gateway-token); \
	  printf '> %s\n\n' "$(PROMPT)"; \
	  curl -sS -N -m 60 \
	    -H "Authorization: Bearer $$TOKEN" \
	    -H "Content-Type: application/json" \
	    -d '{"model":"Qwen/Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"$(PROMPT)"}],"stream":true,"max_tokens":120}' \
	    "$$URL/v1/chat/completions"

gateway-chat:
	@URL=$$($(MAKE) -s gateway-url); \
	  TOKEN=$$($(MAKE) -s gateway-token); \
	  printf '> %s\n' "$(PROMPT)"; \
	  curl -sS -N -m 60 \
	    -H "Authorization: Bearer $$TOKEN" \
	    -H "Content-Type: application/json" \
	    -d '{"model":"Qwen/Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"$(PROMPT)"}],"stream":true,"max_tokens":256}' \
	    "$$URL/v1/chat/completions" \
	  | jq -j --unbuffered -nR 'inputs | select(startswith("data: ")) | ltrimstr("data: ") | select(. != "[DONE]") | fromjson | .choices[0].delta.content // empty'; \
	  echo

gpu-scale-down:
	cd $(FOUNDATION_DIR) && AWS_PROFILE=$(PROFILE) terraform apply -var=gpu_desired_size=0 -auto-approve

gpu-scale-up:
	cd $(FOUNDATION_DIR) && AWS_PROFILE=$(PROFILE) terraform apply -var=gpu_desired_size=2 -auto-approve

# Full router-direct concurrency sweep (4,8,16,32,64,128) with C=1 warmup.
bench-sweep:
	./bench/run_full_sweep.sh router-direct

# Full sweep against the FastAPI gateway instead of the router.
bench-sweep-gateway:
	./bench/run_full_sweep.sh gateway

# One-off run at C=$(C) (default 16) — useful for spot-checking dashboards.
bench-c:
	./bench/run_full_sweep.sh router-direct --single $(C)
