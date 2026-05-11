SHELL          := /bin/bash
REGION         := us-east-1
PROFILE        := <AWS_PROFILE>
PLATFORM_DIR   := infra/platform-apps
FOUNDATION_DIR := infra/eks-foundation

.PHONY: deploy ecr-bootstrap vllm-image karpenter-up keda-up gpu-cutover terraform-apply destroy gateway-url gateway-token gateway-info gateway-test gateway-chat gpu-scale-down gpu-scale-up bench-sweep bench-sweep-gateway bench-c

PROMPT ?= Say hello in 5 words.
C      ?= 16

deploy: ecr-bootstrap terraform-apply

ecr-bootstrap:
	cd $(PLATFORM_DIR) && terraform apply \
	  -target=aws_ecr_repository.fastapi \
	  -auto-approve

# Build + push the custom vLLM image with Qwen2.5-7B-Instruct weights baked in.
# Requires huggingface_token in terraform.tfvars (or TF_VAR_huggingface_token).
# ~10–30 min on first run: ~14GB download from HF + ~20GB push to ECR.
#
# Extracts host CA bundle into the build context first so the in-container HF
# download trusts a corporate VPN's TLS-intercepted huggingface.co cert (corp
# root CA is in the macOS System keychain; containers don't inherit host trust).
vllm-image:
	security find-certificate -a -p /Library/Keychains/System.keychain \
	  > $(PLATFORM_DIR)/vllm-image/corp-ca.crt
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -target=aws_ecr_repository.vllm_qwen25_7b \
	  -target=terraform_data.vllm_image \
	  -auto-approve

# Step 2: install Karpenter alongside the existing static GPU node group.
# Three phases in one target:
#   (1) refresh foundation state so platform-apps can read the new
#       node_iam_role_* / cluster_security_group_id outputs;
#   (2) install the Karpenter controller + IAM (this lands the CRDs the
#       NodePool/EC2NodeClass manifests in (3) depend on);
#   (3) apply the NodePool + EC2NodeClass.
# Idempotent — re-running is a no-op once installed.
karpenter-up:
	cd $(FOUNDATION_DIR) && AWS_PROFILE=$(PROFILE) terraform apply -auto-approve
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -target=aws_iam_role.karpenter_controller \
	  -target=aws_iam_role_policy.karpenter_controller \
	  -target=aws_eks_pod_identity_association.karpenter_controller \
	  -target=helm_release.karpenter \
	  -auto-approve
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -target=kubernetes_manifest.karpenter_nodeclass_gpu_l4 \
	  -target=kubernetes_manifest.karpenter_nodepool_gpu_l4 \
	  -auto-approve

# Step 5: install KEDA + ScaledObject for the engine Deployment.
# Two phases: helm + IAM (lands the CRDs), then the ScaledObject /
# ClusterTriggerAuthentication manifests that depend on those CRDs.
# Idempotent. ScaledObject lands frozen at min=max=2 (no actual scaling).
keda-up:
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -target=kubernetes_namespace_v1.keda \
	  -target=aws_iam_role.sigv4_proxy \
	  -target=aws_iam_role_policy.sigv4_proxy_amp \
	  -target=kubernetes_service_account_v1.sigv4_proxy \
	  -target=aws_eks_pod_identity_association.sigv4_proxy \
	  -target=kubernetes_deployment_v1.sigv4_proxy \
	  -target=kubernetes_service_v1.sigv4_proxy \
	  -target=helm_release.keda \
	  -auto-approve
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -target=kubernetes_manifest.keda_vllm_scaler \
	  -auto-approve

# Step 2 cutover: drop the static GPU node group; Karpenter takes over.
# Prereqs: `make karpenter-up` succeeded; node-group-gpu.tf has been removed.
# Effect: existing GPU nodes drain → engine pods Pending → Karpenter provisions
# replacement g6.2xlarge nodes → engines come back. ~3–5 min downtime.
gpu-cutover:
	@if [ -f $(FOUNDATION_DIR)/node-group-gpu.tf ]; then \
	  echo "ERROR: $(FOUNDATION_DIR)/node-group-gpu.tf still exists. Delete it first, then re-run."; \
	  exit 1; \
	fi
	cd $(FOUNDATION_DIR) && AWS_PROFILE=$(PROFILE) terraform apply -auto-approve

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

# GPU capacity is Karpenter-driven now (scale-from-zero on pod demand).
# Scaling vllm + headroom replicas to 0 lets Karpenter consolidate the
# empty g6.2xlarge nodes; setting back to 2 brings them up.
#
# Engines are managed by KEDA's HPA with minReplicaCount=2, so a TF-only
# scale to 0 has KEDA reverting it within seconds. The annotation
# `autoscaling.keda.sh/paused-replicas` overrides the ScaledObject and
# pins the deployment at the given value; removing the annotation hands
# control back to KEDA.
gpu-scale-down:
	kubectl annotate scaledobject vllm-engine-scaler -n vllm \
	  autoscaling.keda.sh/paused-replicas="0" --overwrite
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -var=vllm_replicas=0 -var=headroom_replicas=0 -auto-approve

gpu-scale-up:
	kubectl annotate scaledobject vllm-engine-scaler -n vllm \
	  autoscaling.keda.sh/paused-replicas- --overwrite || true
	cd $(PLATFORM_DIR) && AWS_PROFILE=$(PROFILE) terraform apply \
	  -var=vllm_replicas=2 -var=headroom_replicas=2 -auto-approve

# Full router-direct concurrency sweep (4,8,16,32,64,128) with C=1 warmup.
bench-sweep:
	./bench/run_full_sweep.sh router-direct

# Full sweep against the FastAPI gateway instead of the router.
bench-sweep-gateway:
	./bench/run_full_sweep.sh gateway

# One-off run at C=$(C) (default 16) — useful for spot-checking dashboards.
bench-c:
	./bench/run_full_sweep.sh router-direct --single $(C)
