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

RUN apk update \
    && apk upgrade \
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

COPY --chown=crypt:crypt scripts/* /app/
COPY --chown=root:root files/bash/* /root/
COPY --chown=crypt:crypt files/bash/* /home/crypt/

# known_hosts is created empty above rather than copied in: files/ssh/ is
# gitignored, so copying it broke the build on a fresh clone. The Makefile
# mounts the real known_hosts over these at run time.

# Numeric, so the id resolves without the container's passwd database (DL3066).
# Note every `docker run` in the Makefile overrides this with `--user root`.
USER 1000
WORKDIR /app
ENTRYPOINT ["/usr/bin/gocryptfs"]
