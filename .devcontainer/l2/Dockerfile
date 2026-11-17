# This repository's L2 image: where its hooks and its test suite run, with no
# credentials (see docs/LAYERS.md in devcontainer-airlock). The suite builds
# the product image and starts sshd and gocryptfs containers of its own, so it
# runs with `l2 --engine`, which points `docker` at the L2 engine.
ARG L2_IMAGE=ghcr.io/ivan-pinatti-labs/airlock-l2@sha256:fe5ed9b8840c4abc8cd531788440a106f5b943828d5344522a52e1daf82ce2ab
# hadolint ignore=DL3006
FROM ${L2_IMAGE}
USER 0:0
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3-pytest openssh-client \
  && rm -rf /var/lib/apt/lists/*
USER 1000:1000
