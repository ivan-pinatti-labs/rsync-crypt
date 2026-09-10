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
version shipped, rather than rehosting source in this repository. Every
source link below is pinned to the specific commit that built the listed
version, not to a branch: Alpine's `<version>-stable` branches keep moving as
fixes are backported into them, so a branch link could point at different
source than what was actually shipped by the time anyone follows it.

## How this list is built

**It is generated, not hand-maintained.** `scripts/generate-third-party-licenses.py`
reads the metadata out of an image built from this repository's `Dockerfile` and
rewrites the inventory below:

```console
make build
make third-party-licenses
```

Everything it needs is already inside the image, in apk's own installed
database (`/lib/apk/db/installed`), one stanza per package: the exact version
with its `-rN` revision, the licence Alpine's own `APKBUILD` declares, the
aports subdirectory the package was built from, and the aports commit that
built it. So the licences are read from Alpine's package metadata rather than
guessed from a package name, and the source links are pinned to a commit
rather than to `<version>-stable`, which keeps moving as Alpine backports
fixes into it. The one thing that database does not record is whether a
package came from `main` or `community`, which the link path needs, so the
script reads that from `apk policy` and joins the two on the package name.

The inventory covers the Alpine base image's own packages as well as
everything `apk add` installs, transitively, because every one of them ends up
in the published image regardless of who asked for it.

`make third-party-licenses-check` regenerates in memory and fails if the
committed file has drifted from the image. That check is what gives
regeneration a trigger: `nightly-build.yml` runs it against the image it has
just built and opens or updates a pull request when the answer is that the
inventory has moved (see `.github/workflows/nightly-build.yml`). Which is the
point, because the drift is not driven by anything in this repository.
`Dockerfile` runs `apk update && apk upgrade` before installing anything, so
packages this repository does not pin with a `~=` constraint can move between
one `docker build` and the next with `ALPINE_VERSION` and every `*_VERSION`
unchanged, as Alpine backports security fixes within its stable branch.
Nothing a person edits here would ever say so.

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

<!-- BEGIN GENERATED INVENTORY -->

> **Generated on 2026-09-10 from an image built on Alpine 3.24.1.**
> Every row below is a dated observation, not a standing fact: Alpine bumps a
> package's `-rN` revision without changing its upstream version, and `Dockerfile`
> runs `apk update && apk upgrade` before installing anything, so a rebuild that
> changes no file in this repository can still move these versions. Regenerate with
> `make third-party-licenses`. The licences themselves do not move with a revision,
> and each source link stays valid for the revision it names.

51 packages:

| Package                | Version          | License                                   | Source                                                                                                                |
| ---------------------- | ---------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| acl-libs               | 2.3.2-r1         | LGPL-2.1-or-later AND GPL-2.0-or-later    | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/8bac74716d06c84627ab02d101fd9d2bafd0a34c/main/acl>               |
| alpine-baselayout      | 3.7.2-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/60a7585bbab2fa0f762504eb617dbca90216e31f/main/alpine-baselayout> |
| alpine-baselayout-data | 3.7.2-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/60a7585bbab2fa0f762504eb617dbca90216e31f/main/alpine-baselayout> |
| alpine-keys            | 2.6-r0           | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/b9f23becced4d7b3ccc0fa0f28530243ccd314a0/main/alpine-keys>       |
| alpine-release         | 3.24.1-r0        | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/0934530484bbcde7498e2c694c710a49616a450e/main/alpine-base>       |
| apk-tools              | 3.0.8-r0         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/4588b452722bd4800efdc6cce4f6e980e02a997f/main/apk-tools>         |
| bash                   | 5.3.9-r1         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/1522c3193610902d8493f9790a2755c11f21f26d/main/bash>              |
| busybox                | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/c3ef5d10e6ef6528852c51f0564963e2f8c1be19/main/busybox>           |
| busybox-binsh          | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/c3ef5d10e6ef6528852c51f0564963e2f8c1be19/main/busybox>           |
| ca-certificates-bundle | 20260611-r0      | MPL-2.0 AND MIT                           | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/e41cbd2ae991adfe8df298ba8e8e777e90bd0e03/main/ca-certificates>   |
| fuse                   | 2.9.9-r7         | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/7831ef13a9d4ad46cbf4962aa573f81225e8220d/community/fuse>         |
| fuse-common            | 3.18.3-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/0adb5f52202c5e2d5029f50a27a972479b3d98b5/main/fuse3>             |
| fuse3                  | 3.18.3-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/0adb5f52202c5e2d5029f50a27a972479b3d98b5/main/fuse3>             |
| fuse3-libs             | 3.18.3-r0        | GPL-2.0-only AND LGPL-2.1-only            | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/0adb5f52202c5e2d5029f50a27a972479b3d98b5/main/fuse3>             |
| glib                   | 2.88.1-r1        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/bd96e2db11c9ba7d7795454d16af620d577614c7/main/glib>              |
| gocryptfs              | 2.6.1-r6         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/1e1aed58b7720fcb6b1859043d543b33019d8c4f/community/gocryptfs>    |
| less                   | 702-r0           | GPL-3.0-or-later OR BSD-2-Clause          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/58c8a1ed3dcfa0680c6ad25e2edd6512bcb0ff93/main/less>              |
| libapk                 | 3.0.8-r0         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/4588b452722bd4800efdc6cce4f6e980e02a997f/main/apk-tools>         |
| libblkid               | 2.42.3-r1        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/d98c55af59055e6ca60fbe36e171546918709965/main/util-linux>        |
| libcrypto3             | 3.5.8-r0         | Apache-2.0                                | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/013edf8b29199933e8ea34dde460b5584b979042/main/openssl>           |
| libeconf               | 0.8.3-r0         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/62db33049768522f381410614d59c7d732f72c3a/main/libeconf>          |
| libedit                | 20260508.3.1-r1  | BSD-3-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/7c805161e588c028cdee3f19f500849fb2c75d42/main/libedit>           |
| libffi                 | 3.5.2-r1         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/2c2a7bb4a8b16066834e90402567b2c19403a790/main/libffi>            |
| libintl                | 1.0-r0           | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/a0b12dbf67fb047e79a1b76c6dfe6f2904d0d391/main/gettext>           |
| libmount               | 2.42.3-r1        | LGPL-2.1-or-later                         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/d98c55af59055e6ca60fbe36e171546918709965/main/util-linux>        |
| libncursesw            | 6.6_p20260516-r0 | X11                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/2cee8a7328d061418336ad327b512d96bcd7bc5e/main/ncurses>           |
| libssl3                | 3.5.8-r0         | Apache-2.0                                | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/013edf8b29199933e8ea34dde460b5584b979042/main/openssl>           |
| libxxhash              | 0.8.3-r1         | BSD-2-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3b2e469e223a5cb31932b1eae30c266e26b30148/main/xxhash>            |
| lz4-libs               | 1.10.0-r1        | BSD-2-Clause AND GPL-2.0-or-later         | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/1f16962f34234a77fab0f4651459c4381b4a0cd6/main/lz4>               |
| musl                   | 1.2.6-r2         | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/f5640d3a10f664c9119720c60515265d3d6f6d01/main/musl>              |
| musl-utils             | 1.2.6-r2         | MIT AND BSD-2-Clause AND GPL-2.0-or-later | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/f5640d3a10f664c9119720c60515265d3d6f6d01/main/musl>              |
| ncurses-terminfo-base  | 6.6_p20260516-r0 | X11                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/2cee8a7328d061418336ad327b512d96bcd7bc5e/main/ncurses>           |
| openssh                | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-client-common  | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-client-default | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-keygen         | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-server         | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-server-common  | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| openssh-sftp-server    | 10.3_p1-r1       | SSH-OpenSSH                               | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/da03ee144d27cece40d2a293c83214592415ae91/main/openssh>           |
| pcre2                  | 10.48-r0         | BSD-3-Clause                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/abc383a51f67922cd61e338b1461cddaa1814b28/main/pcre2>             |
| popt                   | 1.19-r4          | MIT                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/d3abe30d43524bed75a29a3006e703ab51836548/main/popt>              |
| readline               | 8.3.3-r1         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/a854c03acdac188901fb012f7acbee70a36e8041/main/readline>          |
| rsync                  | 3.5.0-r0         | GPL-3.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/8cc5798059868c71d4c2efa296afe835c6602689/main/rsync>             |
| scanelf                | 1.3.9-r1         | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/c61801eeacb3ffcd9c2025b09e402153bb93fb39/main/pax-utils>         |
| sshfs                  | 3.7.6-r0         | GPL-2.0-or-later                          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/30c459460190530ca7207892edb1726b840f7335/main/sshfs>             |
| ssl_client             | 1.37.0-r31       | GPL-2.0-only                              | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/c3ef5d10e6ef6528852c51f0564963e2f8c1be19/main/busybox>           |
| vim                    | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/b8686e0038af384390ccbe6ebaa1d3064f43f2fc/community/vim>          |
| vim-common             | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/b8686e0038af384390ccbe6ebaa1d3064f43f2fc/community/vim>          |
| xxd                    | 9.2.1014-r0      | Vim                                       | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/b8686e0038af384390ccbe6ebaa1d3064f43f2fc/community/vim>          |
| zlib                   | 1.3.2-r0         | Zlib                                      | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/f248b33b5943c7dc69bf691031d7612ab2e8ed93/main/zlib>              |
| zstd-libs              | 1.5.7-r2         | BSD-3-Clause OR GPL-2.0-or-later          | <https://gitlab.alpinelinux.org/alpine/aports/-/tree/3c6e2ee2b16f403d53eab39c4426eb61f003c322/main/zstd>              |

<!-- END GENERATED INVENTORY -->

## Notes on specific packages

- **rsync** (GPL-3.0-or-later): the package this documentation exists for.
  Alpine's unmodified rsync build is used as-is; its complete corresponding
  source is Alpine's own `aports` tree, linked above, at the commit that built
  the exact `rsync` revision the table names.
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
