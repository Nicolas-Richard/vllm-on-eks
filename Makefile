SHELL          := /bin/bash
REGION         := us-east-1
PROFILE        := sandbox-admin
PLATFORM_DIR   := infra/platform-apps

.PHONY: deploy ecr-bootstrap terraform-apply destroy gateway-url gateway-token gateway-info gateway-test gateway-chat

PROMPT ?= Say hello in 5 words.

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
	@cd $(PLATFORM_DIR) && terraform output -raw gateway_bearer_token

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
