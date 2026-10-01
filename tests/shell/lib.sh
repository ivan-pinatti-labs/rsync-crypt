#!/usr/bin/env bash
#
# Shared by tests/shell/*.test.sh, which source it. Each test file runs one
# script under scripts/ as its own bash process, the way the container runs
# it, once per case, with every external command it drives (gocryptfs,
# rsync, sshfs, fusermount, ssh-keygen, sshd, pkill, sleep) replaced by a
# stand-in on PATH. Nothing touches the network, a FUSE device or anything
# outside one scratch directory, which is removed afterwards.

set -o errexit
set -o pipefail
set -o nounset

__repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
__scratch="$(mktemp -d)"
trap 'rm -rf "${__scratch}"' EXIT
__failures=0
__bash="$(command -v bash)"

# Stand-ins go in __bin, first on PATH. Each one appends its name and
# arguments to __calls, and exits with the next status listed in
# __scratch/<name>.exits (one per line, consumed in order), or 0 once the
# list is empty.
__bin="${__scratch}/bin"
__calls="${__scratch}/calls"
mkdir "${__bin}"
export PATH="${__bin}:${PATH}"

# stub <name> [<shell run before exiting>]
stub() {
  local name="${1}" action="${2:-}"
  cat >"${__bin}/${name}" <<EOF
#!/usr/bin/env bash
echo "${name} \$*" >>"${__calls}"
${action}
exits="${__scratch}/${name}.exits"
code=0
if [[ -s "\${exits}" ]]; then
  code="\$(head -n 1 "\${exits}")"
  tail -n +2 "\${exits}" >"\${exits}.next"
  mv "\${exits}.next" "\${exits}"
fi
exit "\${code}"
EOF
  chmod +x "${__bin}/${name}"
}

# exits <name> <status>...: the statuses the stand-in returns, in order.
exits() {
  local name="${1}"
  shift
  printf '%s\n' "$@" >"${__scratch}/${name}.exits"
}

# A directory holding only `which` and the named stand-ins, for the cases
# where one of the script's required binaries has to be missing.
path_without() {
  local dir="${__scratch}/path-without-${1}" name
  rm -rf "${dir}"
  mkdir "${dir}"
  ln -s "$(command -v which)" "${dir}/which"
  for name in "${@:2}"; do
    ln -s "${__bin}/${name}" "${dir}/${name}"
  done
  printf '%s' "${dir}"
}

# run <script> [<argument>...]: runs scripts/<script> with standard input
# from __scratch/stdin (empty unless a case writes it), and records its exit
# status, standard output and standard error for check below. Environment
# variables for one case go in front of the call: `PARANOID_MODE=true run`.
# __run_path, set the same way, replaces PATH for the script alone.
run() {
  local script="${1}"
  shift
  : >"${__calls}"
  touch "${__scratch}/stdin"
  __status=0
  PATH="${__run_path:-${PATH}}" "${__bash}" "${__repo}/scripts/${script}" "$@" \
    <"${__scratch}/stdin" \
    >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
  rm -f "${__scratch}/stdin" "${__scratch}"/*.exits
}

# check <name> <status> <out|err|calls> <text>
check() {
  local name="${1}" want_status="${2}" stream="${3}" want_text="${4}" file
  case "${stream}" in
  calls) file="${__calls}" ;;
  *) file="${__scratch}/${stream}" ;;
  esac
  if [[ "${__status}" -ne "${want_status}" ]]; then
    fail "${name}: exit ${__status}, wanted ${want_status}"
    sed 's/^/  | /' "${__scratch}/out" "${__scratch}/err" >&2
  elif ! grep --quiet --fixed-strings -- "${want_text}" "${file}"; then
    fail "${name}: '${want_text}' not in ${stream}"
    sed 's/^/  | /' "${file}" >&2
  else
    echo "ok ${name}"
  fi
}

# Records a failed expectation that check cannot express.
fail() {
  echo "FAIL ${1}" >&2
  __failures=$((__failures + 1))
}

# Ends a test file: non-zero if any case failed.
finish() {
  if [[ "${__failures}" -gt 0 ]]; then
    echo "${__failures} failed" >&2
    exit 1
  fi
}
