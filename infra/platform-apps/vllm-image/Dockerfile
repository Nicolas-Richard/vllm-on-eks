# syntax=docker/dockerfile:1.7
FROM vllm/vllm-openai:v0.19.1

ARG MODEL_REPO=Qwen/Qwen2.5-7B-Instruct
ARG MODEL_DIR=/models/qwen25-7b

# Trust the host's CA bundle. Required when building on a corp-Mac whose VPN
# does TLS interception (corp egress re-signs traffic with a private root CA
# that the upstream vLLM image's certifi store doesn't know about). The bundle
# is extracted from the host keychain by `make vllm-image` before invoking
# `docker build` and dropped into the build context as `corp-ca.crt`.
COPY corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN update-ca-certificates && \
    cp /etc/ssl/certs/ca-certificates.crt \
       /usr/local/lib/python3.12/dist-packages/certifi/cacert.pem

# BuildKit `--secret` keeps the HF token out of the image's layer history.
# Token is only present on the filesystem during this RUN step.
#
# `hf` is the current CLI; the older `huggingface-cli` was removed in
# huggingface_hub 1.11+, which is what vllm/vllm-openai:v0.19.1 ships.
RUN --mount=type=secret,id=hf_token \
    if [ ! -s /run/secrets/hf_token ]; then \
      echo "FATAL: /run/secrets/hf_token is empty in container" >&2; \
      ls -la /run/secrets/ >&2; \
      exit 1; \
    fi && \
    HF_TOKEN="$(tr -d '[:space:]' < /run/secrets/hf_token)" && \
    echo "in-container token length: ${#HF_TOKEN}" && \
    hf download "$MODEL_REPO" \
        --local-dir "$MODEL_DIR" \
        --token "$HF_TOKEN"
