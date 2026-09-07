# anet guardian, for EigenCompute (Intel TDX).
#
# The key is generated inside this container at runtime and never leaves, so
# nothing secret is baked into the image or injected at deploy. Only the
# public key and the attestation quote are ever served.
FROM python:3.12-slim

WORKDIR /app

# Install anet (the guardian imports anet.crypto and anet.attest).
COPY anet/ /app/anet/
COPY pyproject.toml README.md /app/
RUN pip install --no-cache-dir . && pip install --no-cache-dir httpx

# The guardian service and its in-enclave policy/attestation seam.
COPY enclave/guardian.py enclave/policy.py enclave/attestation_platform.py /app/enclave/

# EigenCompute routes to the port the app listens on.
ENV PORT=8080
EXPOSE 8080

# In the enclave, set ANET_TEE=eigencompute and ANET_TEE_QUOTE_ENDPOINT to the
# platform's quote mechanism (see EigenCompute attested-API docs). Policy via
# ANET_GUARDIAN_OPS / ANET_GUARDIAN_RATE / ANET_GUARDIAN_MAX_VALUE.
CMD ["python", "-m", "enclave.guardian"]
