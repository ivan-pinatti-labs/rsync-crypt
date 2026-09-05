ENV_FILE ?= .env
.DEFAULT_GOAL := help

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

Usage:
  ENV_FILE=.env.myconfig make <target>

You can also use:
  make <target> ENV_FILE=.env.myconfig

If the file does not exist yet, create it first:
  cp .env.example .env.myconfig
endef

ifneq ($(filter-out help,$(MAKECMDGOALS)),)
ifeq ($(wildcard $(ENV_FILE)),)
$(error $(_missing_env_file_message))
endif

include $(ENV_FILE)
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

.PHONY: all help build backup backup_as_root bb bbr brr \
        restore restore_to_origin restore_as_root restore_as_root_to_origin \
        r ro rr rro view view_as_root v vr \
        run_container run_container_as_root check-passkey clean

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
		'  clean                       Remove backup state and image (prompts; destructive).' \
		'  help                        Show this help.' \
		'' \
		'If the env file does not exist, create it first:' \
		'  cp .env.example .env.myconfig'

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
	@$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/backup/src \
		--volume ${BACKUP_FILTER_RULES}:/backup/brave-filter-rules.txt \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
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
	@$(call _passkey_check,/backup/passfile); \
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
		--volume ${BACKUP_FILTER_RULES}:/backup/brave-filter-rules.txt \
		--volume ${BACKUP_ENCRYPTION_CONF}:/backup/src/.gocryptfs.reverse.conf.original \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		--volume ${SSH_KNOWN_HOSTS_FILE}:/root/.ssh/known_hosts \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
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
	@$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${RESTORE_DESTINATION}:/restore/origin \
		--volume ${RESTORE_PATHS_FILE}:/restore/restore-paths.txt \
		--volume ${RESTORE_EXCLUDE_LIST}:/restore/restore-exclude-list.txt \
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
	@$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/restore/origin \
		--volume ${RESTORE_PATHS_FILE}:/restore/restore-paths.txt \
		--volume ${RESTORE_EXCLUDE_LIST}:/restore/restore-exclude-list.txt \
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
	@$(call _passkey_check,/restore/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--entrypoint /bin/bash \
		--volume ${RESTORE_DESTINATION}:/restore/origin \
		--volume ${RESTORE_PATHS_FILE}:/restore/restore-paths.txt \
		--volume ${RESTORE_EXCLUDE_LIST}:/restore/restore-exclude-list.txt \
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
	@$(call _passkey_check,/restore/passfile); \
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
		--volume ${RESTORE_PATHS_FILE}:/restore/restore-paths.txt \
		--volume ${RESTORE_EXCLUDE_LIST}:/restore/restore-exclude-list.txt \
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
	@$(call _passkey_check,/backup/passfile); \
	docker run \
		--name gocryptfs \
		--user root \
		--cap-add SYS_ADMIN \
		--device /dev/fuse \
		--security-opt apparmor:unconfined \
		--security-opt label=disable \
		--entrypoint /bin/bash \
		--volume ${BACKUP_SOURCE_FOLDER}:/backup/src \
		--volume ${BACKUP_FILTER_RULES}:/backup/brave-filter-rules.txt \
		--volume ${SSH_KEY_FILE}:/home/crypt/.ssh/id_rsa \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION}

run_container_as_root:
	@$(call _passkey_check,/backup/passfile); \
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
		--volume ${BACKUP_FILTER_RULES}:/backup/brave-filter-rules.txt \
		--volume ${BACKUP_ENCRYPTION_CONF}:/backup/src/.gocryptfs.reverse.conf.original \
		--volume ${SSH_KEY_FILE}:/root/.ssh/id_rsa \
		$$_pv \
		--env PARANOID_MODE=$$_paranoid \
		--rm \
		--interactive --tty ${DOCKER_IMAGE_TAG_NAME}:${DOCKER_IMAGE_TAG_VERSION}
