# Every version this image is built from lives here, as an ARG default, and
# nowhere else. They used to live in .env.example, which meant the Makefile's
# `build` target had to pass all eight as --build-arg for the Dockerfile to
# build at all, and `docker build .` on its own produced `alpine:` and
# `bash~=`. An ARG default makes the Dockerfile self-contained: a plain
# `docker build .`, a `docker/build-push-action` step with no build-args, and
# `make build` all produce the same image, and an override is still one
# `--build-arg` away.
#
# The Alpine base image tag. This is the one pin with a real Renovate
# datasource behind it (a Docker tag), so it carries Renovate's own
# annotation comment, in the exact shape .github/renovate.json5's custom
# regex manager is anchored to: a `# renovate:` line immediately above the
# `ARG NAME=value` line it annotates, which is Renovate's documented
# convention for this pattern.
# renovate: datasource=docker depName=alpine versioning=docker
ARG ALPINE_VERSION=3.24
FROM alpine:${ALPINE_VERSION}

# The seven apk `~=` constraints below are deliberately NOT `# renovate:`
# annotated. Each is a version constraint resolved against whichever Alpine
# release ALPINE_VERSION pins, and no Renovate datasource models "the version
# of this apk package available in Alpine release X", so a bump Renovate
# proposed on its own could easily name a version that release's repo does not
# carry and fail the build (see .github/renovate.json5's customManagers
# comment for the same reasoning from Renovate's side).
#
# They are re-resolved automatically instead, by
# .github/workflows/resolve-apk-pins.yml, which runs `apk policy <pkg>` inside
# the proposed `alpine:<version>` whenever a Renovate ALPINE_VERSION pull
# request lands and pushes a second commit onto it if anything moved. The
# `# apk-pin: resolved-from=ALPINE_VERSION` marker above each one is what makes
# it eligible for that: scripts/assert-pin-only-diff.py's APK_PIN_ANNOTATION
# reads these exact lines to decide that a bump to one of them is a pin bump
# rather than a dependency bot reaching outside its lane. The marker is
# distinct from `# renovate:` on purpose, so Renovate's own regex manager never
# matches it and never puts these seven back under the independent tracking
# they are kept out of Renovate to avoid.
#
# ALPINE_VERSION is deliberately not redeclared here. An ARG declared before
# the first FROM is in scope for the FROM line and nowhere else, and nothing
# in this stage interpolates it, so a second declaration would only be a
# second line for `resolve-apk-pins.yml`'s `^ARG ALPINE_VERSION=` grep and
# Renovate's own regex to disagree about.
# apk-pin: resolved-from=ALPINE_VERSION
ARG GOCRYPTFS_VERSION=2.6
# apk-pin: resolved-from=ALPINE_VERSION
ARG BASH_VERSION=5.3
# apk-pin: resolved-from=ALPINE_VERSION
ARG LESS_VERSION=702
# apk-pin: resolved-from=ALPINE_VERSION
ARG OPENSSH_VERSION=10.3
# apk-pin: resolved-from=ALPINE_VERSION
ARG RSYNC_VERSION=3.5
# apk-pin: resolved-from=ALPINE_VERSION
ARG SSHFS_VERSION=3.7
# apk-pin: resolved-from=ALPINE_VERSION
ARG VIM_VERSION=9.2

# BuildKit caches this RUN layer by its exact command text and build context,
# with no notion that "apk upgrade" means something different today than it
# did when the layer was last cached. A rebuild with an unchanged Dockerfile
# (a scheduled nightly, or a release that bumps nothing in this file) hits
# that cache and skips the layer entirely, silently shipping whatever apk
# versions were current the last time it actually ran, not whatever Alpine
# has published since. This is exactly what happened to v1.6.0: the layer
# cache-hit in ~2 seconds (confirmed from the build log, far too fast for a
# real apk fetch) and shipped util-linux 2.42.1-r0 days after Alpine's
# security fix (2.42.3-r1) was already published.
#
# APK_CACHE_BUST breaks that cache hit on demand. It has no effect on the
# packages installed; it only appears inside a `:` no-op so changing its
# value changes the layer's cache key without changing what the command
# does. `docker build .` and `make build` get the default (0) and keep
# normal caching for fast local iteration. publish-image.yml and
# nightly-build.yml pass a fresh value (the workflow run id) on every
# invocation, so every published or nightly image re-resolves apk packages
# against Alpine's live repo instead of trusting a cached layer's idea of
# "current".
ARG APK_CACHE_BUST=0

RUN apk update \
    && apk upgrade \
    && : "cache-bust=${APK_CACHE_BUST}" \
    && apk add --no-cache \
        bash~=${BASH_VERSION} \
        gocryptfs~=${GOCRYPTFS_VERSION} \
        less~=${LESS_VERSION} \
        openssh~=${OPENSSH_VERSION} \
        rsync~=${RSYNC_VERSION} \
        sshfs~=${SSHFS_VERSION} \
        vim~=${VIM_VERSION} \
    && rm -rf /var/cache/apk/* \
    && adduser -D -u 1000 crypt \
    && mkdir -p \
        /app \
        /backup/enc \
        /backup/src \
        /restore/dec \
        /restore/enc \
        /restore/origin \
        /gocrypt-view/decrypted \
        /gocrypt-view/encrypted \
        /root/.ssh \
        /home/crypt/.ssh \
    && chmod 700 /root/.ssh /home/crypt/.ssh \
    && touch /root/.ssh/known_hosts /home/crypt/.ssh/known_hosts \
    && chmod 644 /root/.ssh/known_hosts /home/crypt/.ssh/known_hosts \
    && chown -R root:root /root \
    && chown -R crypt:crypt \
        /app \
        /backup \
        /home/crypt \
        /restore \
        /gocrypt-view

# Named individually, not 'scripts/*'. The glob also copied in every
# repository-tooling script that happens to live under scripts/
# (assert-pin-only-diff.py, audit-security-ignores.py,
# coderabbit-review-verdict.py, resolve-apk-pins.py), all of which exist to
# grade pull requests and audit the ignore list in CI and have no business in
# a published backup image. They were inert there, since this image installs
# no Python interpreter at all, but they were still shipped: confirmed present
# under /app/ in ghcr.io/ivan-pinatti-labs/rsync-crypt:1.6.1. Listing the
# three scripts the container actually runs keeps the next tooling script from
# silently joining them.
COPY --chown=crypt:crypt scripts/backup.sh scripts/restore.sh scripts/view.sh /app/
COPY --chown=root:root files/bash/* /root/
COPY --chown=crypt:crypt files/bash/* /home/crypt/

# known_hosts is created empty above rather than copied in: files/ssh/ is
# gitignored, so copying it broke the build on a fresh clone. The Makefile
# mounts the real known_hosts over these at run time.

# Numeric, so the id resolves without the container's passwd database (DL3066).
#
# Every `docker run` in the Makefile overrides this with `--user root`, which
# makes this line govern exactly one case: someone running the published image
# directly, without the Makefile. Keeping that case unprivileged is the whole
# of its job, and issue #69 settled that it earns its place on those terms
# rather than being dead weight.
#
# It is not a contradiction of the Makefile, and the reason is written out in
# full above that file's targets. In short: under this project's default
# runtime, rootless Podman, the container's root is already the invoking user
# mapped through a user namespace, so `--user root` there is the unprivileged
# choice and the only one that can read that user's own files. Root was never
# what the FUSE mount needed either; fusermount is setuid in this image, which
# is why `--cap-add SYS_ADMIN --device /dev/fuse` is what the Makefile passes
# rather than relying on the uid.
USER 1000
WORKDIR /app
ENTRYPOINT ["/usr/bin/gocryptfs"]
