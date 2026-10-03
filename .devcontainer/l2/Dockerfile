# This repository's L2 image: where its hooks and its test suite run, with no
# credentials (see docs/LAYERS.md in devcontainer-airlock). The suite builds
# the product image and starts sshd and gocryptfs containers of its own, so it
# runs with `l2 --engine`, which points `docker` at the L2 engine.
ARG L2_IMAGE=ghcr.io/ivan-pinatti-labs/airlock-l2@sha256:2f9fd8c08e3ddf39abff9981f855b158cd12588ee7113c00ef5e83e2fa9d7a56
# hadolint ignore=DL3006
FROM ${L2_IMAGE}
USER 0:0
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3-pytest openssh-client \
  && rm -rf /var/lib/apt/lists/*
USER 1000:1000
