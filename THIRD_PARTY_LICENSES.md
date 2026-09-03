# Third-Party Licenses

rsync-crypt's own source (this repository) is licensed under the
[Apache License 2.0](LICENSE). The Docker image built from it, however, also
bundles Alpine Linux's own compiled packages: the base image plus everything
`Dockerfile` installs with `apk add` (and their transitive dependencies). Each
of those packages carries its own upstream license, independent of this
repository's own.

None of these packages are vendored, patched, or modified here; the image
uses Alpine's unmodified builds as-is. In particular, `rsync` is GPLv3, which
imposes a source-availability obligation on binary redistribution. Since
these are Alpine's own unmodified builds, that obligation is satisfied by
pointing to Alpine's own `aports` source tree for the exact package and
version shipped, rather than rehosting source in this repository.

## How this list was built

The list below is the complete, exact output of `apk info -v` run inside a
container built from this repository's `Dockerfile` pinned to the versions
below (`ALPINE_VERSION=3.24`, and the `*_VERSION` build-args in
`.env.example`), so it includes the Alpine base image's own packages as well
as everything `apk add` installs, transitively. Every license was read from
Alpine's own package metadata (the `license:` field `apk info -a` reports,
sourced from each package's `APKBUILD`) for Alpine branch `3.24-stable`, not
guessed or inferred from the package name.

**This list is tied to the versions above, and only loosely pinned even
then.** `Dockerfile` runs `apk update && apk upgrade` before installing
anything, so patch-level releases of packages this repository does not pin
with a `~=` constraint (everything except `bash`, `gocryptfs`, `less`,
`openssh`, `rsync`, `sshfs`, `vim`) can drift between one `docker build` and
the next even with `ALPINE_VERSION` and every `*_VERSION` unchanged, as
Alpine backports security fixes within the `3.24-stable` branch. Regenerate
this list whenever any of those change, and periodically otherwise:

```console
make build
docker run --rm --entrypoint apk local/gocryptfs:1.0.0 info -v | sort
```

...and re-check the license and source link for anything added, removed, or
whose version changed, against `https://pkgs.alpinelinux.org/package/v<ALPINE_VERSION>/<repo>/x86_64/<package>`.

## License key

Most entries carry a single [SPDX](https://spdx.org/licenses/) identifier.
Where a package's own license metadata combines more than one:

- **`AND`** — the package contains code under more than one license; all of
  them apply (typically because the package bundles files from more than one
  upstream project, e.g. `musl-utils`).
- **`OR`** — the upstream project dual-licenses the whole package; the
  recipient may choose either (e.g. `less`, `zstd-libs`).

`SSH-OpenSSH` is OpenSSH's own SPDX identifier for its license: a BSD-style
license with several copyright holders, none of them copyleft.

## Packages in the image

| Package                | Version          | License                                   | Source                                                                                 |
| ---------------------- | ---------------- | ----------------------------------------- | -------------------------------------------------------------------------------------- |
| acl-libs               | 2.3.2-r1         | LGPL-2.1-or-later AND GPL-2.0-or-later    | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/acl>             |
| alpine-baselayout      | 3.7.2-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/master/main/alpine-baselayout>    |
| alpine-baselayout-data | 3.7.2-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/master/main/alpine-baselayout>    |
| alpine-keys            | 2.6-r0           | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/alpine-keys>     |
| alpine-release         | 3.24.1-r0        | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/alpine-base>     |
| apk-tools              | 3.0.8-r0         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/apk-tools>       |
| bash                   | 5.3.9-r1         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/bash>            |
| busybox                | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/busybox>         |
| busybox-binsh          | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/busybox>         |
| ca-certificates-bundle | 20260611-r0      | MPL-2.0 AND MIT                           | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/ca-certificates> |
| fuse                   | 2.9.9-r7         | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/community/fuse>       |
| fuse3                  | 3.18.2-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/fuse3>           |
| fuse3-libs             | 3.18.2-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/fuse3>           |
| fuse-common            | 3.18.2-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/fuse3>           |
| glib                   | 2.88.1-r1        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/glib>            |
| gocryptfs              | 2.6.1-r5         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/community/gocryptfs>  |
| less                   | 702-r0           | GPL-3.0-or-later OR BSD-2-Clause          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/less>            |
| libapk                 | 3.0.8-r0         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/apk-tools>       |
| libblkid               | 2.42.1-r0        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/util-linux>      |
| libcrypto3             | 3.5.8-r0         | Apache-2.0                                | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssl>         |
| libeconf               | 0.8.3-r0         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/libeconf>        |
| libedit                | 20260508.3.1-r1  | BSD-3-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/libedit>         |
| libffi                 | 3.5.2-r1         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/libffi>          |
| libintl                | 1.0-r0           | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/gettext>         |
| libmount               | 2.42.1-r0        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/util-linux>      |
| libncursesw            | 6.6_p20260516-r0 | X11                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/ncurses>         |
| libssl3                | 3.5.8-r0         | Apache-2.0                                | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssl>         |
| libxxhash              | 0.8.3-r1         | BSD-2-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/xxhash>          |
| lz4-libs               | 1.10.0-r1        | BSD-2-Clause AND GPL-2.0-or-later         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/lz4>             |
| musl                   | 1.2.6-r2         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/musl>            |
| musl-utils             | 1.2.6-r2         | MIT AND BSD-2-Clause AND GPL-2.0-or-later | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/musl>            |
| ncurses-terminfo-base  | 6.6_p20260516-r0 | X11                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/ncurses>         |
| openssh                | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-client-common  | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-client-default | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-keygen         | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-server         | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-server-common  | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| openssh-sftp-server    | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/openssh>         |
| pcre2                  | 10.48-r0         | BSD-3-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/pcre2>           |
| popt                   | 1.19-r4          | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/popt>            |
| readline               | 8.3.3-r1         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/readline>        |
| rsync                  | 3.5.0-r0         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/rsync>           |
| scanelf                | 1.3.9-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/pax-utils>       |
| sshfs                  | 3.7.6-r0         | GPL-2.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/sshfs>           |
| ssl_client             | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/busybox>         |
| vim                    | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/community/vim>        |
| vim-common             | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/community/vim>        |
| xxd                    | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/community/vim>        |
| zlib                   | 1.3.2-r0         | Zlib                                      | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/zlib>            |
| zstd-libs              | 1.5.7-r2         | BSD-3-Clause OR GPL-2.0-or-later          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3.24-stable/main/zstd>            |

## Notes on specific packages

- **rsync** (GPL-3.0-or-later): the package this documentation exists for.
  Alpine's unmodified rsync build is used as-is; its complete corresponding
  source is Alpine's own `aports` tree, linked above, at the commit that
  built `rsync-3.5.0-r0` for Alpine `3.24-stable`.
- **openssh**, **openssh-server**, **openssh-client-\*\***, **openssh-keygen**,
  **openssh-sftp-server** (`SSH-OpenSSH`): all built from the single
  `main/openssh` aports directory; listed separately because they are
  installed as separate apk packages by the same `openssh~=${OPENSSH_VERSION}`
  constraint in `Dockerfile`.
- **vim**, **vim-common**, **xxd** (`Vim`): the Vim license is a permissive,
  GPL-compatible license (charityware); all three are built from the single
  `community/vim` aports directory.
- **libapk**, **apk-tools** and **scanelf** are not explicitly requested by
  `Dockerfile`, but ship as part of the Alpine base image and its own package
  manager; included here because they end up in the final image regardless.
