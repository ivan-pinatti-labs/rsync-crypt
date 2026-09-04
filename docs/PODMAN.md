# Running rsync-crypt with Podman

Part of the [rsync-crypt](../README.md) documentation. Podman is the
recommended container runtime for this project; Docker works exactly as well
and nothing here is required.

The short version: install `podman` and `podman-docker`, and every `docker`
command in this repository (the `Makefile`, the README's Quick Start, the
test suite) keeps working with no modification at all. There is no
`CONTAINER_ENGINE` variable to set and no Podman branch in the `Makefile`,
deliberately: the compatibility package is the whole mechanism.

---

## Why Podman over Docker

Both runtimes can run containers as an unprivileged user. The difference is
architectural, and it matters more than usual for a tool whose entire job is
reading every file you own.

- **Daemonless.** Docker routes every command through `dockerd`, a
  long-running process owned by root that holds a Unix socket. Anything that
  can talk to that socket can start a container, and a container can be
  started with your whole filesystem mounted into it. Podman has no daemon:
  `podman run` forks the container as a direct child of your own shell, and
  when the container exits there is nothing left running.
- **Rootless by default.** Under Podman, "root inside the container" is your
  own unprivileged user on the host, mapped through a user namespace. The
  container's uid 0 owns nothing on the host that you do not already own.
- **Nothing privileged to leave behind.** No socket to misconfigure, no
  group membership that is effectively root equivalence (`docker` group), and
  no background service that survives a failed run.
- **Drop-in command compatibility.** `podman-docker` installs a `docker`
  shim that forwards to `podman`, so the `Makefile`'s `docker build`,
  `docker run` and `docker inspect` calls need no changes.

Rootless Docker exists and is a genuine improvement over rootful Docker, and
the README has recommended it for a long time. Podman's advantage over it is
that rootless is the default rather than an opt-in configuration, and that
there is no daemon in the picture either way.

---

## Install

### Fedora / RHEL

```bash
sudo dnf install podman podman-docker
```

### Debian / Ubuntu

```bash
sudo apt install podman podman-docker
```

That is the whole installation. Verify the shim is in place:

```bash
docker --version   # prints a podman version
```

`podman-docker` prints an "emulating Docker CLI using podman" notice on every
call. Silence it with:

```bash
sudo touch /etc/containers/nodocker
```

---

## What this project needs from the runtime

`rsync-crypt` mounts a FUSE filesystem inside the container, which is why
every `make` target passes the same three flags:

| Flag                                 | Why                                                     |
| ------------------------------------ | ------------------------------------------------------- |
| `--device /dev/fuse`                 | gocryptfs mounts a FUSE filesystem inside the container |
| `--cap-add SYS_ADMIN`                | `mount(2)` for that FUSE mount                          |
| `--security-opt apparmor:unconfined` | AppArmor's default profile denies the FUSE mount        |

Podman accepts all three with the same spelling. `--security-opt
label=disable` (SELinux, on Fedora and RHEL) is also already passed by every
target that mounts raw system paths (`/etc`, `/home`, `/opt`, `/root`,
`/srv`) read-write: `backup_as_root`, `restore_as_root_to_origin`,
`view_as_root` and `run_container_as_root`.

If `/dev/fuse` is missing on the host, install `fuse3` and load the module:

```bash
sudo modprobe fuse
```

---

## Rootless, specifically for this project

This is where the general "rootless is better" claim needs splitting, because
this repository has two very different families of targets and the benefit is
real for one and thin for the other.

### `make backup`, `make view`, `make restore`: the benefit is full

These read `BACKUP_SOURCE_FOLDER` (your home directory, by default), your SSH
key and your passphrase file. Every one of those is already owned by you.
Rootless Podman maps the container's root to your own uid, so the container
gets exactly the access you already have and not one file more. No daemon is
involved, nothing runs as real root at any point, and a bug or a malicious
image cannot reach past your own account.

This is the ordinary path and the one worth switching for.

### `make backup_as_root`, `view_as_root`, `restore_as_root_to_origin`: weaker

These mount `/etc`, `/home`, `/opt`, `/root` and `/srv`, which is the point of
them: they back up the system, including other users' files and root-owned
configuration. That needs privileged access to the host by definition, and no
container runtime changes that. Under rootless Podman these targets simply
cannot read most of what they are pointed at, so they have to be run as real
root: `sudo make backup_as_root` (and likewise for `view_as_root` and
`restore_as_root_to_origin`), not `make backup_as_root` on its own.
`podman-docker`'s `docker` shim is a system-wide symlink, not a per-user one,
so `sudo` reaching it still resolves to Podman, just no longer rootless, at
which point the container's root really is the host's root again.

So for the `_as_root` family, Podman's advantage narrows to "no daemon" and
"no `docker` group". Those still count, but the user-namespace isolation, the
part that does the real work, does not apply. Treat these targets as
privileged operations regardless of runtime, and prefer the user-mode targets
whenever they are enough.

One practical consequence: a backup made rootless and a backup made with
`sudo` write their gocryptfs config and passkey files as different owners.
Keep the two families pointed at separate `REMOTE_SERVER_BACKUP_FOLDER`
paths and separate `.env` profiles (see
[Multiple Configurations](USAGE.md#multiple-configurations)) rather than
alternating between them against one destination.

---

## Rootless specifics worth knowing

- **User namespaces need subuid/subgid ranges.** Most distributions
  configure these at install time. Check with `podman unshare cat
  /proc/self/uid_map`; if it is empty, run
  `sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535
  "$USER"` and then `podman system migrate`.
- **Files written inside the container come back owned by you.** That is the
  user namespace doing its job, and it is why the restore targets do not need
  a `chown` step under rootless Podman.
- **Ports below 1024 are not bindable by default.** `make view` publishes
  `127.0.0.1:2222`, which is above that line, so it is unaffected. Nothing in
  this project needs a privileged port.
- **`podman` and `sudo podman` keep separate image stores.** An image built
  rootless is invisible to a rootful run, so `make bbr` (build plus root
  backup) has to build under the same privilege it runs under.

---

## Going back to Docker

Nothing in this repository is Podman-specific, so there is nothing to undo.
Remove `podman-docker` and install Docker; every target keeps working. That
is the point of using the compatibility shim rather than teaching the
`Makefile` about two runtimes.
