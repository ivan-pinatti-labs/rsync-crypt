# Development container

Everything this repository needs to develop and verify it locally, inside a
container: every hook in `.pre-commit-config.yaml` (including
`hadolint-docker`, `actionlint-docker` and dotenv-linter, which start
containers of their own), the whole test suite (`pytest tests`, which builds
the image through `make build`, mounts gocryptfs through FUSE and copies the
encrypted view with rsync to a throwaway sshd container), `gh`, and `git`
pushes over SSH. It is built on the
organization's base image from
[ivan-pinatti-labs/devcontainer-images](https://github.com/ivan-pinatti-labs/devcontainer-images),
whose `docs/IMAGES.md` explains the image itself.

It runs with rootless Podman, and SELinux stays enforcing the whole time.

## Before the first start

Three things live on the host, outside any container, and are set up once.
The first two are shared by every repository that uses this development
container, so a machine that already has them skips to the third.

### A GitHub token for `gh`

A fine-grained personal access token for the `ivan-pinatti-labs` organization
with read and write access to pull requests and issues, and read access to
actions, commit statuses and contents. Nothing else: no administration,
secrets or organization permissions. Store it as a Podman secret:

```shell
podman secret create gh-devcontainer /path/to/a/file/holding/the/token
```

Delete that file afterwards. The container receives the token as `GH_TOKEN`;
it never appears in `podman inspect`.

### An SSH agent for `git push`

A dedicated SSH key, not your usual one, held by an ssh-agent that runs in its
own container. The development container can ask that agent to sign a GitHub
login but never sees the key, and the key is restricted to GitHub, so nothing
running in the development container can use it anywhere else.

Create the key with a passphrase and add its public half to your GitHub
account as an authentication key:

```shell
ssh-keygen -t ed25519 -C devcontainer -f ~/.ssh/devcontainer/id_ed25519
chmod 700 ~/.ssh/devcontainer
```

Start the agent container. It has no network, no capabilities and a read
only filesystem, and it runs in the same SELinux domain and category as the
development container, which is what allows the two to talk:

```shell
mkdir -p "${XDG_RUNTIME_DIR}/devcontainer-ssh"
chmod 700 "${XDG_RUNTIME_DIR}/devcontainer-ssh"
curl -fsS https://api.github.com/meta \
  | jq -r '.ssh_keys[] | "github.com " + .' \
  > "${XDG_RUNTIME_DIR}/devcontainer-ssh/known_hosts"
podman run -d --name devcontainer-ssh-agent \
  --network=none --cap-drop=all --read-only \
  --security-opt no-new-privileges \
  --userns=keep-id \
  --security-opt label=type:container_engine_t \
  --security-opt label=level:s0:c555,c666 \
  -v "${XDG_RUNTIME_DIR}/devcontainer-ssh:/sock:Z" \
  -v "${HOME}/.ssh/devcontainer:/key:ro,Z" \
  ghcr.io/ivan-pinatti-labs/devcontainer-base@sha256:422d159cc15e46e4ae806bcf718d63ea5bd080fa701ab7a980e45b55d955b602 \
  ssh-agent -D -a /sock/agent.sock
```

Then unlock the key once per login, typing the passphrase:

```shell
podman exec -it -e SSH_AUTH_SOCK=/sock/agent.sock devcontainer-ssh-agent \
  ssh-add -H /sock/known_hosts -h github.com /key/id_ed25519
```

GitHub's host keys come from GitHub's own API rather than from a first
connection, so the development container verifies the host strictly.

### An SELinux policy module, for the test suite

The test suite mounts gocryptfs through FUSE inside containers that this
development container starts, and the host policy refuses to pass `/dev/fuse`
on to a nested container (`crun: set propagation for dev/fuse: Permission
denied`). `host/selinux/devcontainer_nested_devices.te` in
[devcontainer-images](https://github.com/ivan-pinatti-labs/devcontainer-images)
grants exactly that one permission, for the confined domain this container
runs in, on the FUSE and TUN devices only. Build and install it once, as root
on this machine:

```shell
checkmodule -M -m -o devcontainer_nested_devices.mod host/selinux/devcontainer_nested_devices.te
semodule_package -o devcontainer_nested_devices.pp -m devcontainer_nested_devices.mod
sudo semodule -i devcontainer_nested_devices.pp
```

`sudo semodule -r devcontainer_nested_devices` removes it again. The hooks
work without it; the roundtrip tests do not.

## Starting it

Open the repository's main clone as a folder of its own, and choose **Reopen
in Container**.

- Not a worktree. Worktrees created inside the container work normally, but a
  worktree opened directly does not, because its git metadata lives in the
  main clone, outside the folder that gets mounted.
- Not inside a multi-root workspace (a window titled "Untitled (Workspace)").
  There the Dev Containers extension does not find this configuration, and
  offers to add a new one to the workspace or to the user data folder
  instead. Neither is this one; open the folder by itself.

## Why each run argument

| Argument | Why |
| --- | --- |
| `--userns=keep-id` | Files in the mounted clone keep your own user id on the host. |
| `label=type:container_engine_t` | The confined SELinux domain that allows a container engine to run inside, so the hooks and tests that start containers work without turning SELinux labeling off. |
| `label=level:s0:c555,c666` | The same SELinux category as the ssh-agent container; a different category cannot connect to its socket. |
| `--device /dev/fuse` | The nested container storage driver needs it, and the test suite passes it on to the containers that mount gocryptfs. |
| `--device /dev/net/tun` | Rootless networking for nested containers with a network of their own. |
| `unmask=/proc/sys` | The nested network backend writes `net.ipv4.ip_forward` in the network namespace it creates. Only namespaced network settings are writable; kernel, vm and fs settings stay refused. |
| `CONTAINERS_CONF_OVERRIDE` | Makes a bridge network the default for nested containers, so the test suite's sshd container has an address of its own to rsync to. |
| `--secret gh-devcontainer,...` | The GitHub token, as `GH_TOKEN`. |
| The workspace mount at the clone's own path | Paths inside the container match the host, so git worktree metadata and the bind mounts the Makefile and hooks make resolve the same way in both. |
| The `devcontainer-ssh` mount | The ssh-agent socket and GitHub's host keys. |

## What it runs

```shell
pre-commit install
pre-commit run --all-files
pytest tests
make build ENV_FILE=.env.example
```

Tool versions come only from this repository's `.tool-versions`, and the test
dependencies from `tests/requirements.txt`, the same file CI installs. A tool
this repository does not pin is missing in the container rather than borrowed
from somewhere else, which is how a missing pin shows up.

The asdf plugins that install those tools are pinned too, in
`.devcontainer/asdf-plugins`: each from its repository URL, at a commit
someone has read. A plugin is a set of scripts that `asdf install` runs, so a
new tool needs its plugin added there (the build fails without it), and
Renovate proposes new plugin commits as pull requests for a person to review,
never merged automatically.
