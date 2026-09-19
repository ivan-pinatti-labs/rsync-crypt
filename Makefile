ENV_FILE ?= .env
.DEFAULT_GOAL := help

# The directory holding ENV_FILE, absolute and with a trailing slash. Every
# relative config path in that file is resolved against this rather than
# against make's working directory, so an env file kept next to its conf
# files keeps working when make runs from somewhere else entirely.
#
# $(abspath) is deliberate over $(realpath): it is pure string manipulation
# and does not require the file to exist, which matters because the missing
# ENV_FILE error below has to be reachable with a useful message rather than
# collapsing to an empty directory first.
_env_dir := $(dir $(abspath $(ENV_FILE)))

# Resolve one env file value to a path make can hand to 'docker run
# --volume'. Both branches strip the env file's own quotes and the result is
# wrapped in exactly one pair, so the shell sees a single argument whether or
# not the author quoted the value. This is the same reasoning as the
# positional arguments (see the AGENTS.md section on why Makefile expansions
# are wrapped in subst), and the reason --volume sites are otherwise left
# alone does not apply once a value is being rewritten anyway.
#
# Quoting on both branches rather than passing an absolute value through
# untouched: an unquoted absolute value is legal in an env file, and
# '/mnt/my backups/rules.txt' written without quotes would otherwise reach
# the shell bare and split into two arguments, mounting neither path.
#
# $(filter /%,...) has to see past the quotes to judge absoluteness, hence
# the inner $(subst ",,...). $(filter) splits on whitespace, so that same
# value arrives as two words, but only the first word can carry the leading
# slash and matching any word is enough, so a space in an absolute path is
# still recognised. A relative value never has a first word starting with /,
# so the two cases stay distinguishable.
#
# The relative branch also collapses the /./ that joining a trailing-slash
# directory to a leading ./ value produces. $(subst) rather than $(patsubst)
# for that collapse: patsubst operates on whitespace-separated words and
# would mangle a path with a space, the same trap documented in AGENTS.md.
env_rel = "$(if $(filter /%,$(subst ",,$(1))),$(subst ",,$(1)),$(subst /./,/,$(_env_dir)$(subst ",,$(1))))"

# Fail before 'docker run' when a resolved config path is not a readable
# file. Without this the container runtime silently creates an empty
# directory at the source path and bind mounts that, so the container gets a
# directory where it expects a file and the real cause never surfaces. The
# Makefile already guards GOCRYPTFS_PASSKEY_FILE against the same artifact.
# $(1) is the variable name, $(2) its resolved and quoted path.
#
# -r as well as -f, so the check matches what the error message claims. A
# regular file the invoking user cannot read passes -f and then fails inside
# the container, which is the late and unclear failure this exists to
# prevent: under rootless Podman or Docker the container's root maps back to
# that same user, so it has no more access to the file than this check does.
define _require_config_file
if [ ! -f $(2) ] || [ ! -r $(2) ]; then \
	printf '%s\n' \
		"Error: $(1) does not name a readable file." \
		"  resolved to: "$(2) \
		"  env file:    $(abspath $(ENV_FILE))" \
		"" \
		"A relative value in the env file resolves against that file's own" \
		"directory, not against the directory make was run from." \
		"" \
		"To create a profile's env file and its conf copies together:" \
		"  make new-profile NAME=<profile>" >&2; \
	exit 1; \
fi
endef

# The eight version pins that live in the Dockerfile now, as ARG defaults,
# not in the env file (see 'build' below). Listed once, here, and reused by
# both the pre-include snapshot immediately below and the 'build' recipe
# itself, so adding a ninth pin only means updating this one list.
_BUILD_ARG_VARS := ALPINE_VERSION GOCRYPTFS_VERSION BASH_VERSION LESS_VERSION \
                    OPENSSH_VERSION RSYNC_VERSION SSHFS_VERSION VIM_VERSION

# Expands to $(1)'s value only when $(1) was set to something by the
# caller, on the command line or in the invoking shell's environment, as
# opposed to merely being defined in the included ENV_FILE (origin "file"),
# left unset entirely (origin "undefined"), or set to an empty string on
# the command line itself ('make build ALPINE_VERSION='). $(origin) alone
# cannot see the value, only where it came from, hence the trailing
# $($(1)) check: without it, an explicit-but-empty command-line override
# would still report a qualifying origin and forward '--build-arg VAR='
# with nothing after the '=', which is the exact failure the old (removed)
# emptiness guard existed to catch. $(findstring ...) rather than
# $(filter ...): $(origin) can return "command line", which $(filter)
# would split on the space into two separate patterns instead of matching
# it literally. $(and ...) short-circuits to empty the moment either half
# is empty, and otherwise expands to its last argument, which is what lets
# this double as both the yes/no check and the value to forward.
pin_override = $(and $(or $(findstring command line,$(origin $(1))),$(findstring environment,$(origin $(1)))),$($(1)))

# A snapshot of pin_override's result for each of the eight, taken before
# ENV_FILE is included below, while $(origin) can still tell a caller's own
# override apart from the file's. This has to happen first: a plain '='
# assignment in an included file unconditionally overrides a same-named
# value already set in the calling shell's environment (a command-line
# assignment is the one thing no makefile assignment can ever override, but
# an environment one is not immune the same way). A caller upgrading from
# before these eight pins moved into the Dockerfile who still has one of
# them sitting in their real .env would otherwise have
# 'ALPINE_VERSION=x make build' silently lose both its value and its
# "environment" origin the moment the file is included: not forwarding x,
# not forwarding the file's leftover value either (pin_override, evaluated
# *after* include, correctly refuses a "file" origin), just silently
# building the Dockerfile's own default instead of what was actually
# typed. $(eval) here runs immediately, at parse time, which is what makes
# '_pin_snapshot_<VAR> :=' capture the pre-include state as a fixed string
# rather than something re-evaluated later against the post-include world.
$(foreach v,$(_BUILD_ARG_VARS),$(eval _pin_snapshot_$(v) := $(call pin_override,$(v))))

define _missing_env_file_message

Missing ENV_FILE '$(ENV_FILE)'.

Usage, either of these:
  ENV_FILE=.env.myconfig make <target>
  make <target> ENV_FILE=.env.myconfig

If the file does not exist yet, create it first:
  make new-profile NAME=myconfig    (env file plus its own conf copies)
  cp .env.example .env.myconfig     (env file only)
endef

# 'new-profile' joins 'help' in not requiring an env file: it is the target
# that creates one, so demanding one first would make it unreachable on a
# fresh clone, which is exactly when it is most useful.
ifneq ($(filter-out help new-profile,$(MAKECMDGOALS)),)
ifeq ($(wildcard $(ENV_FILE)),)
$(error $(_missing_env_file_message))
endif

include $(ENV_FILE)

# Resolved once, here, rather than at each of the twelve --volume sites, so
# the path a target mounts and the path _require_config_file reports are the
# same string by construction and cannot drift apart.
_backup_filter_rules  := $(call env_rel,${BACKUP_FILTER_RULES})
_restore_exclude_list := $(call env_rel,${RESTORE_EXCLUDE_LIST})
_restore_paths_file   := $(call env_rel,${RESTORE_PATHS_FILE})

# Say so when the env file is not in the directory make is running from.
# That combination is supported and documented, but it is also the one where
# a relative config path used to resolve somewhere nobody intended, so the
# resolution is stated rather than left to be inferred.
#
# The wording carries no colon on purpose. checkmake parses any line holding
# one as a target declaration, so '$(info Note: ...)' is reported as an
# undeclared phony target named '$(info Note'.
ifneq ($(_env_dir),$(CURDIR)/)
$(info Using env file $(abspath $(ENV_FILE)), which is outside $(CURDIR).)
$(info Relative config paths in it resolve against $(_env_dir))
endif
endif

SHELL := /bin/bash

# Allow: make restore RESTORE_PATHS="Documents/ .config/Code/User/"
RESTORE_PATHS ?=

# Passkey check: resolves passphrase mode and sets shell vars _pv and _paranoid.
# _pv     : --volume flag for the passkey file (empty in paranoid mode)
# _paranoid: PARANOID_MODE value to pass into the container
# Usage: $(call _passkey_check,/container/path/to/passfile)
define _passkey_check
if [ "${PARANOID_MODE}" = "true" ]; then \
	_pv=""; _paranoid="true"; \
else \
	if [ -d "${GOCRYPTFS_PASSKEY_FILE}" ]; then \
		echo "Removing stale directory '${GOCRYPTFS_PASSKEY_FILE}' (Docker artifact)..."; \
		rmdir "${GOCRYPTFS_PASSKEY_FILE}" || { echo "Error: '${GOCRYPTFS_PASSKEY_FILE}' is a non-empty directory."; exit 1; }; \
	fi; \
	if [ ! -f "${GOCRYPTFS_PASSKEY_FILE}" ]; then \
		echo "Passkey file '${GOCRYPTFS_PASSKEY_FILE}' not found."; \
		read -r -p "Enter passphrase interactively without saving to disk? [y/N] " _choice; \
		if [ "$$_choice" = "y" ] || [ "$$_choice" = "Y" ]; then \
			echo "Switching to interactive passphrase for this run."; \
			_pv=""; _paranoid="true"; \
		else \
			read -r -p "Enter a passphrase to create it: " passphrase; \
			printf '%s' "$$passphrase" > "${GOCRYPTFS_PASSKEY_FILE}"; \
			echo "Passkey file created at ${GOCRYPTFS_PASSKEY_FILE}"; \
			chmod 600 "${GOCRYPTFS_PASSKEY_FILE}"; \
			_pv="--volume ${GOCRYPTFS_PASSKEY_FILE}:$(1)"; _paranoid="false"; \
		fi; \
	else \
		chmod 600 "${GOCRYPTFS_PASSKEY_FILE}"; \
		_pv="--volume ${GOCRYPTFS_PASSKEY_FILE}:$(1)"; _paranoid="false"; \
	fi; \
fi
endef

# Why every `docker run` below passes `--user root`, and why the image's own
# `USER 1000` is not a contradiction. Issue #69 asked for this to be written
# down rather than inferred, having found `USER 1000` inert.
#
# The reason people reach for first is wrong. Root is NOT needed for the FUSE
# mount: /bin/fusermount and /usr/bin/fusermount3 are both setuid root in the
# image, so the mount helper escalates on its own and its caller does not have
# to be privileged. A full reverse-mode init, mount, list and unmount cycle as
# a non-root uid was run to confirm that before this comment was written. So
# `--cap-add SYS_ADMIN --device /dev/fuse` is what the mount needs, not the
# uid.
#
# The real reason is that under this project's default runtime, rootless
# Podman, the container's root IS the invoking user, mapped through a user
# namespace. Nothing runs as real root at any point, and the container gets
# exactly the access that user already has. `--user root` is therefore both
# the unprivileged choice and the only one that works: passing `--user $(id
# -u)` maps that uid to a subuid instead, and the container can then no longer
# read the user's own files. Measured, not reasoned: a 0600 file owned by the
# invoking user reads back as root-owned inside the container, and uid 1000
# gets "Permission denied" on it. The same inversion applies to rootless
# Docker.
#
# The `_as_root` family is a separate case and is named for what it backs up,
# not for the uid it runs as: /etc, /home, /opt, /root and /srv, which is
# other users' files and root-owned configuration by definition. Those targets
# need real host root, which under rootless Podman means `sudo make
# backup_as_root`, not a different `--user`. See docs/PODMAN.md's "the benefit
# is full" and "weaker" sections, which split the two families on exactly this
# line.
#
# `USER 1000` in the Dockerfile therefore governs one thing only: what someone
# running the published image directly, without this Makefile, gets by
# default. That is worth keeping unprivileged, and it is why the line stays.

# One .PHONY per line, not a backslash continuation. checkmake reads only
# the first physical line of a .PHONY declaration and silently drops the
# rest, so a continuation makes it report r, ro, rr, rro, v and vr as
# undeclared and clean as missing, seven false findings for targets that
# are right here. Upstream: checkmake#280, fix open as checkmake#281.
# That fix reaches us only once it merges, checkmake releases it, and
# pre-commit-checklists moves its own checkmake pin to that release. Until
# then this form is load bearing. Reverting it afterwards is optional;
# make treats the two forms identically.
.PHONY: all help build backup backup_as_root bb bbr brr
.PHONY: restore restore_to_origin restore_as_root restore_as_root_to_origin
.PHONY: r ro rr rro view view_as_root v vr
.PHONY: run_container run_container_as_root check-passkey clean new-profile
.PHONY: third-party-licenses third-party-licenses-check

all: build run_container

help:
	@printf '%s\n' \
		'Usage:' \
		'  make <target>' \
		'  ENV_FILE=.env.myconfig make <target>' \
		'  make <target> ENV_FILE=.env.myconfig' \
		'' \
		'Targets:' \
		'  all                         Build the image and start a user-backup container.' \
		'  build                       Build the Docker image.' \
		'  backup                      Encrypt and sync user data.' \
		'  backup_as_root              Encrypt and sync system data.' \
		'  bb                          Build and back up user data.' \
		'  bbr                         Build and back up system data.' \
		'  restore (r)                 Restore user data to staging.' \
		'  restore_to_origin (ro)      Restore user data to its original location.' \
		'  restore_as_root (rr)        Restore system data to staging.' \
		'  restore_as_root_to_origin (rro) Restore system data to original paths.' \
		'  brr                         Build and restore system data to staging.' \
		'  view (v)                    Browse the decrypted user backup over SFTP.' \
		'  view_as_root (vr)           Browse the decrypted system backup over SFTP.' \
		'  run_container               Start an interactive user-backup container.' \
		'  run_container_as_root       Start an interactive system-backup container.' \
		'  check-passkey               Create or verify the passkey file.' \
		'  new-profile NAME=<profile>  Create .env.<profile> and its own conf copies.' \
		'  clean                       Remove backup state and image (prompts; destructive).' \
		'  third-party-licenses        Regenerate THIRD_PARTY_LICENSES.md from the built image.' \
		'  third-party-licenses-check  Fail if THIRD_PARTY_LICENSES.md has drifted from it.' \
		'  help                        Show this help.' \
		'' \
		'If the env file does not exist, create it first:' \
		'  make new-profile NAME=myconfig    (env file plus its own conf copies)' \
		'  cp .env.example .env.myconfig     (env file only)'

# build and backup
bb: build backup

# build and root backup
bbr: build backup_as_root

# build and root restore (staging)
brr: build restore_as_root

# restore shorthands
r:   restore
ro:  restore_to_origin
rr:  restore_as_root
rro: restore_as_root_to_origin
v:   view
vr:  view_as_root

# Create a named profile: an env file plus its own copy of each shipped conf
# example, every one carrying the profile's name. The point is that local
# rules never touch the tracked examples, so 'git pull' cannot conflict with
# them and a customised filter list is never at risk of being committed.
#
# The generated env file points at the generated copies with relative paths.
# Those resolve against the env file's own directory rather than make's
# working directory (see env_rel at the top), so the profile keeps working
# when it is moved elsewhere or used from another checkout.
#
# Runs from the repository root, because that is where the conf examples it
# copies live.
new-profile:
	@if [ -z "$(NAME)" ]; then \
		printf '%s\n' \
			"Usage: make new-profile NAME=<profile>" \
			"" \
			"Creates .env.<profile> and a conf/<file>.<profile>.txt copy of each" \
			"shipped example, then points the env file at those copies." >&2; \
		exit 1; \
	fi
	@case "$(NAME)" in \
		""|.|..|*[!A-Za-z0-9._-]*) \
			printf '%s\n' \
				"Error: NAME '$(NAME)' is not usable as a file name suffix." \
				"Allowed: letters, digits, dot, underscore and hyphen." >&2; \
			exit 1 ;; \
	esac
	@# -L as well as -e: -e is false for a dangling symbolic link, and the
	@# writes below do not all refuse one. GNU cp declines ("not writing
	@# through dangling symlink"), but the shell redirection that writes the
	@# env file follows the link and creates its target, so a link planted in
	@# the working tree redirects the write to any path the user can write.
	@# Measured on #112: 179 lines landed outside the repository while this
	@# target reported success. -L catches the link itself, whatever it points
	@# at, and a link to a file that does exist is already caught by -e.
	@for f in .env.$(NAME) \
			conf/backup-filter-rules.$(NAME).txt \
			conf/restore-exclude-list.$(NAME).txt \
			conf/restore-paths.$(NAME).txt; do \
		if [ -e "$$f" ] || [ -L "$$f" ]; then \
			printf '%s\n' \
				"Error: $$f already exists." \
				"Remove it first, or choose another NAME." >&2; \
			exit 1; \
		fi; \
	done
	@cp conf/backup-filter-rules.example.txt conf/backup-filter-rules.$(NAME).txt && \
	cp conf/restore-exclude-list.example.txt conf/restore-exclude-list.$(NAME).txt && \
	cp conf/restore-paths.example.txt conf/restore-paths.$(NAME).txt && \
	sed \
		-e 's|^BACKUP_FILTER_RULES=.*|BACKUP_FILTER_RULES="./conf/backup-filter-rules.$(NAME).txt"|' \
		-e 's|^RESTORE_EXCLUDE_LIST=.*|RESTORE_EXCLUDE_LIST="./conf/restore-exclude-list.$(NAME).txt"|' \
		-e 's|^RESTORE_PATHS_FILE=.*|RESTORE_PATHS_FILE="./conf/restore-paths.$(NAME).txt"|' \
		.env.example > .env.$(NAME) && \
	printf '%s\n' \
		"Created:" \
		"  .env.$(NAME)" \
		"  conf/backup-filter-rules.$(NAME).txt" \
		"  conf/restore-exclude-list.$(NAME).txt" \
		"  conf/restore-paths.$(NAME).txt" \
		"" \
		"All four are gitignored. Fill in .env.$(NAME), then run:" \
		"  ENV_FILE=.env.$(NAME) make backup"

# The eight version pins live in the Dockerfile now, as ARG defaults, not in
# the env file. So this target passes no --build-arg at all by default and the
# Dockerfile's own defaults apply, which is what keeps 'make build' and a bare
# 'docker build .' producing the same image.
#
# Each $(if ...) below emits its --build-arg from _pin_snapshot_<VAR> (see the
# top of this file), the pre-include, caller-only snapshot of that pin, not
# from $(VAR) itself, which is how a one-off override still works:
#
#   ALPINE_VERSION=3.20 make build     (command-line variable)
#   make build ALPINE_VERSION=3.20     (same thing, other spelling)
#
# A snapshot that is empty, whether because nothing overrode it or because
# the override itself was blank ('make build ALPINE_VERSION='), makes $(if
# ...) drop the flag entirely, rather than passing '--build-arg
# ALPINE_VERSION=' and building 'alpine:'. That empty-pin failure is why the
# old guard here refused to build when any of the eight was blank; with the
# pins in the Dockerfile there is no blank to refuse, so the guard is gone
# rather than kept as a check on nothing.
build:
	@echo "Building Docker image..."
	@docker build . \
		$(if $(_pin_snapshot_ALPINE_VERSION),--build-arg ALPINE_VERSION=$(_pin_snapshot_ALPINE_VERSION)) \
		$(if $(_pin_snapshot_GOCRYPTFS_VERSION),--build-arg GOCRYPTFS_VERSION=$(_pin_snapshot_GOCRYPTFS_VERSION)) \
		$(if $(_pin_snapshot_BASH_VERSION),--build-arg BASH_VERSION=$(_pin_snapshot_BASH_VERSION)) \
		$(if $(_pin_snapshot_LESS_VERSION),--build-arg LESS_VERSION=$(_pin_snapshot_LESS_VERSION)) \
		$(if $(_pin_snapshot_OPENSSH_VERSION),--build-arg OPENSSH_VERSION=$(_pin_snapshot_OPENSSH_VERSION)) \
		$(if $(_pin_snapshot_RSYNC_VERSION),--build-arg RSYNC_VERSION=$(_pin_snapshot_RSYNC_VERSION)) \
		$(if $(_pin_snapshot_SSHFS_VERSION),--build-arg SSHFS_VERSION=$(_pin_snapshot_SSHFS_VERSION)) \
		$(if $(_pin_snapshot_VIM_VERSION),--build-arg VIM_VERSION=$(_pin_snapshot_VIM_VERSION)) \
		--tag ${DOCKER_IMAGE_TAG_NAME} \
		--tag ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION}

# THIRD_PARTY_LICENSES.md's package inventory is generated, not written, and
# these two targets are the generating and the enforcing half of that. Both
# read an already-built image rather than building one: the inventory is a
# statement about a specific image, so building here would quietly answer a
# question about a different one than the caller had in mind. Run `make build`
# first.
#
# ${DOCKER_IMAGE_TAG_NAME} carries no version suffix and is one of the two tags
# `build` applies, so this follows a plain `make build` without the caller
# having to restate DOCKER_IMAGE_TAG_VERSION.
#
# No $(subst ",,...) wrapper on the tag: this is not one of the seven
# expansions that reach a script's positional arguments, and the env file's own
# quotes are the only quoting the shell sees here, so a value with a space
# already parses as one word.
third-party-licenses:
	@python3 scripts/generate-third-party-licenses.py --image ${DOCKER_IMAGE_TAG_NAME}

third-party-licenses-check:
	@python3 scripts/generate-third-party-licenses.py --image ${DOCKER_IMAGE_TAG_NAME} --check

# WARNING: permanently deletes the passkey, gocryptfs config files, and Docker image.
# Make sure the master key is backed up before running this.
clean:
	@echo "WARNING: this will permanently delete the passkey file, gocryptfs config files, and Docker image."
	@echo "If you lose the passkey without a master key backup, the encrypted backup becomes unrecoverable."
	@read -r -p "Type YES to continue: " confirm && [ "$$confirm" = "YES" ] || { echo "Aborted."; exit 1; }
	@if docker inspect --type container gocryptfs > /dev/null 2>&1; then docker rm -f gocryptfs; fi
	@if docker inspect --type image ${DOCKER_IMAGE_TAG_NAME} > /dev/null 2>&1; then \
		docker rmi ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} ${DOCKER_IMAGE_TAG_NAME}; \
	fi
	@rm -f ${GOCRYPTFS_PASSKEY_FILE}
	@rm -f ${BACKUP_SOURCE_FOLDER}/.gocryptfs.reverse.conf \
	       ${BACKUP_SOURCE_FOLDER}/.gocryptfs.reverse.conf.original \
	       ${BACKUP_ENCRYPTION_CONF}
	@echo "Done."

# Standalone passkey setup utility (useful for verifying or pre-creating the passkey file).
check-passkey:
	@if [ "${PARANOID_MODE}" = "true" ]; then \
		echo "PARANOID_MODE is enabled: passphrase will be entered interactively. No passkey file needed."; \
	else \
		if [ -d "${GOCRYPTFS_PASSKEY_FILE}" ]; then \
			echo "Removing stale directory '${GOCRYPTFS_PASSKEY_FILE}' (Docker artifact)..."; \
			rmdir "${GOCRYPTFS_PASSKEY_FILE}" || { echo "Error: '${GOCRYPTFS_PASSKEY_FILE}' is a non-empty directory."; exit 1; }; \
		fi; \
		if [ ! -f "${GOCRYPTFS_PASSKEY_FILE}" ]; then \
			echo "Passkey file '${GOCRYPTFS_PASSKEY_FILE}' not found."; \
			read -r -p "Enter a passphrase to create it: " passphrase && \
			printf '%s' "$$passphrase" > "${GOCRYPTFS_PASSKEY_FILE}" && \
			echo "Passkey file created at ${GOCRYPTFS_PASSKEY_FILE}"; \
		fi; \
		if [ -f "${GOCRYPTFS_PASSKEY_FILE}" ]; then \
			chmod 600 "${GOCRYPTFS_PASSKEY_FILE}"; \
		else \
			echo "Error: passkey file '${GOCRYPTFS_PASSKEY_FILE}' could not be created."; \
			exit 1; \
		fi; \
	fi

backup:
	@$(call _require_config_file,BACKUP_FILTER_RULES,$(_backup_filter_rules)); \
	$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/backup/src \
		--volume $(_backup_filter_rules):/backup/brave-filter-rules.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--env BACKUP_EXCLUDE_NETWORK_MOUNTS='$(subst ",,${BACKUP_EXCLUDE_NETWORK_MOUNTS})' \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/backup.sh \
			"/backup/src" \
			"/backup/enc" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/backup/passfile" \
			"$(subst ",,${REMOTE_SERVER})" \
			"/backup/brave-filter-rules.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"$(subst ",,${GOCRYPTFS_CIPHER})" \
			"$(subst ",,${GOCRYPTFS_SCRYPT_N})" \
			"$(subst ",,${GOCRYPTFS_ENCRYPT_NAMES})"

backup_as_root:
	@$(call _require_config_file,BACKUP_FILTER_RULES,$(_backup_filter_rules)); \
	$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume /etc:/backup/src/etc \
		--volume /home:/backup/src/home \
		--volume /opt:/backup/src/opt \
		--volume /root:/backup/src/root \
		--volume /srv:/backup/src/srv \
		--volume $(_backup_filter_rules):/backup/brave-filter-rules.txt \
		--volume ${BACKUP_ENCRYPTION_CONF}:/backup/src/.gocryptfs.reverse.conf.original \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--env BACKUP_EXCLUDE_NETWORK_MOUNTS='$(subst ",,${BACKUP_EXCLUDE_NETWORK_MOUNTS})' \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/backup.sh \
			"/backup/src" \
			"/backup/enc" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/backup/passfile" \
			"$(subst ",,${REMOTE_SERVER})" \
			"/backup/brave-filter-rules.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"$(subst ",,${GOCRYPTFS_CIPHER})" \
			"$(subst ",,${GOCRYPTFS_SCRYPT_N})" \
			"$(subst ",,${GOCRYPTFS_ENCRYPT_NAMES})"

# Restore user backup to a staging directory (safe, review before moving)
restore:
	@$(call _require_config_file,RESTORE_EXCLUDE_LIST,$(_restore_exclude_list)); \
	$(call _require_config_file,RESTORE_PATHS_FILE,$(_restore_paths_file)); \
	$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${RESTORE_DESTINATION}:/restore/origin \
		--volume $(_restore_paths_file):/restore/restore-paths.txt \
		--volume $(_restore_exclude_list):/restore/restore-exclude-list.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env RESTORE_PATHS='${RESTORE_PATHS}' \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/restore.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/restore/enc" \
			"/restore/dec" \
			"/restore/passfile" \
			"/restore/restore-exclude-list.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"/restore/origin" \
			"/restore/restore-paths.txt"

# Restore user backup directly to original home directory
restore_to_origin:
	@$(call _require_config_file,RESTORE_EXCLUDE_LIST,$(_restore_exclude_list)); \
	$(call _require_config_file,RESTORE_PATHS_FILE,$(_restore_paths_file)); \
	$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/restore/origin \
		--volume $(_restore_paths_file):/restore/restore-paths.txt \
		--volume $(_restore_exclude_list):/restore/restore-exclude-list.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env RESTORE_PATHS='${RESTORE_PATHS}' \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/restore.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/restore/enc" \
			"/restore/dec" \
			"/restore/passfile" \
			"/restore/restore-exclude-list.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"/restore/origin" \
			"/restore/restore-paths.txt"

# Restore root backup to a staging directory (safe, review before moving)
restore_as_root:
	@$(call _require_config_file,RESTORE_EXCLUDE_LIST,$(_restore_exclude_list)); \
	$(call _require_config_file,RESTORE_PATHS_FILE,$(_restore_paths_file)); \
	$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${RESTORE_DESTINATION}:/restore/origin \
		--volume $(_restore_paths_file):/restore/restore-paths.txt \
		--volume $(_restore_exclude_list):/restore/restore-exclude-list.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env RESTORE_PATHS='${RESTORE_PATHS}' \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/restore.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/restore/enc" \
			"/restore/dec" \
			"/restore/passfile" \
			"/restore/restore-exclude-list.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"/restore/origin" \
			"/restore/restore-paths.txt"

# Restore root backup directly to original system paths (/etc, /home, /opt, /root, /srv)
restore_as_root_to_origin:
	@$(call _require_config_file,RESTORE_EXCLUDE_LIST,$(_restore_exclude_list)); \
	$(call _require_config_file,RESTORE_PATHS_FILE,$(_restore_paths_file)); \
	$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume /etc:/restore/origin/etc \
		--volume /home:/restore/origin/home \
		--volume /opt:/restore/origin/opt \
		--volume /root:/restore/origin/root \
		--volume /srv:/restore/origin/srv \
		--volume $(_restore_paths_file):/restore/restore-paths.txt \
		--volume $(_restore_exclude_list):/restore/restore-exclude-list.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env RESTORE_PATHS='${RESTORE_PATHS}' \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/restore.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/restore/enc" \
			"/restore/dec" \
			"/restore/passfile" \
			"/restore/restore-exclude-list.txt" \
			"$(subst ",,${RSYNC_RATE_LIMIT})" \
			"$(subst ",,${RSYNC_LOOP})" \
			"/restore/origin" \
			"/restore/restore-paths.txt"

# Serves the decrypted backup read-only over SFTP on host port 2222 (user backup).
# Connect your file manager to: sftp://root@localhost:2222/gocrypt-view/decrypted
view:
	@$(call _passkey_check,/gocrypt-view/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--publish 127.0.0.1:2222:22 \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/view.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/gocrypt-view/passfile" \
			"/gocrypt-view/encrypted" \
			"/gocrypt-view/decrypted"

# Serves the decrypted backup read-only over SFTP on host port 2222 (root backup).
# Connect your file manager to: sftp://root@localhost:2222/gocrypt-view/decrypted
view_as_root:
	@$(call _passkey_check,/gocrypt-view/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--publish 127.0.0.1:2222:22 \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION} \
		/app/view.sh \
			"$(subst ",,${REMOTE_SERVER})" \
			"$(subst ",,${REMOTE_SERVER_BACKUP_FOLDER})" \
			"/gocrypt-view/passfile" \
			"/gocrypt-view/encrypted" \
			"/gocrypt-view/decrypted"

run_container:
	@$(call _require_config_file,BACKUP_FILTER_RULES,$(_backup_filter_rules)); \
	$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/backup/src \
		--volume $(_backup_filter_rules):/backup/brave-filter-rules.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION}

run_container_as_root:
	@$(call _require_config_file,BACKUP_FILTER_RULES,$(_backup_filter_rules)); \
	$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume /etc:/backup/src/etc \
		--volume /home:/backup/src/home \
		--volume /opt:/backup/src/opt \
		--volume /root:/backup/src/root \
		--volume /srv:/backup/src/srv \
		--volume $(_backup_filter_rules):/backup/brave-filter-rules.txt \
		--volume ${BACKUP_ENCRYPTION_CONF}:/backup/src/.gocryptfs.reverse.conf.original \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION}
