# Development container for rsync-crypt: everything this repository's hooks
# and test suite (pytest tests) need, on top of the organization's shared base
# image. See .devcontainer/README.md for how to start it and why each run
# argument is there.
#
# The base image is pinned by digest. BASE_IMAGE is a build argument only so a
# change to the base image can be tested against this repository before it is
# published.
ARG BASE_IMAGE=ghcr.io/ivan-pinatti-labs/devcontainer-base@sha256:1b4d3db91675e9d150783f0123c10460f3dca1c1bcc6e594c08f5978ace48fab
FROM ${BASE_IMAGE}

USER 0:0

# github-cli comes from GitHub's own signed apt repository; the base image
# reviewed and installed its key and deliberately enabled nothing, so the
# source entry is added here. See the base image's docs/TOOL_SOURCES.md.
#
# pre-commit and python3-venv come from Ubuntu. python3-venv because the test
# dependencies go into a virtual environment: Ubuntu marks its own Python as
# externally managed and pip refuses to install into it, and Ubuntu's packaged
# pytest is older than tests/requirements.txt allows.
#
# Versions are deliberately unpinned. Ubuntu and GitHub both ship security
# fixes by moving a package's version within a release, so a pin here would
# hold this container on the superseded build until somebody edited it by
# hand.
#
# That makes this layer deliberately rolling, not reproducible, and the two
# should not be confused. The digest above fixes what this builds on; it
# says nothing about what apt resolves on top, and two builds of this file a
# month apart can and should install different package builds. Reproducing
# an exact container therefore means keeping the image, not re-running this
# Dockerfile. Rebuilding is what makes it current, which is the property
# being chosen here.
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/github-cli.gpg] https://cli.github.com/packages stable main' \
      > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    gh \
    pre-commit \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

USER 1000:1000

# The test suite's dependencies, from the same requirements file CI installs.
ENV VIRTUAL_ENV=/home/dev/.local/share/rsync-crypt/tests-venv
COPY --chown=1000:1000 tests/requirements.txt /tmp/tests/requirements.txt
RUN python3 -m venv "${VIRTUAL_ENV}" \
  && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --requirement /tmp/tests/requirements.txt \
  && rm -rf /tmp/tests
ENV PATH=${VIRTUAL_ENV}/bin:${PATH}

WORKDIR /workspace
