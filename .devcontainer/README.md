# Developing in containers

This repository is developed with
[devcontainer-airlock](https://github.com/ivan-pinatti-labs/devcontainer-airlock):
you and the coding agents work in a workbench that holds no GitHub token and
no ssh key, and every hook, the test suite and every package install run in
an L2 container that gets the working tree and nothing else. Its
[docs/LAYERS.md](https://github.com/ivan-pinatti-labs/devcontainer-airlock/blob/main/docs/LAYERS.md)
explains the layers and the one time setup on the host.

## Daily use

Clone devcontainer-airlock next to this repository's main clone (or point
`WORKBENCH_HOME` at a clone elsewhere), and the Makefile here gains its
targets:

```shell
make unlock          # the ssh key, for eight hours
make claude          # Claude Code in its workbench, started if needed
make codex           # Codex in its own workbench
make claude-shell    # a terminal in that workbench (or codex-shell)
```

None of them needs a backup env file. Inside a workbench:

```shell
l2-hooks-install                                # once: git hooks run in L2
l2 --engine -- python3 -m pytest tests          # the test suite
```

The test suite builds the product image and starts sshd and gocryptfs
containers of its own, so it runs with `l2 --engine`, which gives it the L2
engine behind `docker`. The hooks that build images run there too
(`hooks-engine` in `workbench-profile`).

## What is in here

| File | What |
| --- | --- |
| `l2/Dockerfile` | This repository's L2 image, on the shared one pinned by digest, plus pytest and the ssh client the test suite uses. `l2` builds it in the L2 engine the first time and whenever it changes; Renovate keeps the digest current. |
| `egress-sets` | The network services the egress proxy allows for this repository: the Alpine packages and the Docker Hub images the product image builds from, and trivy's database for the pre-push scan. |
| `workbench-profile` | `nested-network`, so the test suite's containers get a bridge network of their own and the sshd container an address to rsync to, and `hooks-engine`, for the hooks that build images. |

## The round trip tests

They mount gocryptfs through FUSE inside containers the L2 engine starts,
and the host policy refuses to pass `/dev/fuse` on to such a nested container
(`crun: set propagation for dev/fuse: Permission denied`). devcontainer-airlock
carries a host SELinux module that grants exactly that one permission, on the
FUSE and TUN devices only, installed once as root; its `docs/IMAGES.md` has
the steps, and `nested-devices` in `workbench-profile` then passes the
devices on. The hooks and the rest of the suite work without it.
