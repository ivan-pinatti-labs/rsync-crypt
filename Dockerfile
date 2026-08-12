ARG ALPINE_VERSION
FROM alpine:${ALPINE_VERSION}

ARG GOCRYPTFS_VERSION

RUN apk update \
    && apk upgrade \
    && apk add --no-cache \
        bash \
        gocryptfs~=${GOCRYPTFS_VERSION} \
        less \
        openssh \
        rsync \
        sshfs \
        vim \
    && rm -rf /var/cache/apk/* \
    && adduser -D crypt \
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

USER crypt
WORKDIR /app
ENTRYPOINT ["/usr/bin/gocryptfs"]
