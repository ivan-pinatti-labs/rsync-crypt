#!/usr/bin/env bash
#
# Runs every tests/shell/*.test.sh, each as its own bash process, and fails
# if any of them does. `make coverage` runs this under kcov, which follows
# each test into the scripts it runs and measures their lines.

set -o errexit
set -o pipefail
set -o nounset

__failed=0
for test in "$(dirname "${BASH_SOURCE[0]}")"/*.test.sh; do
  echo "# ${test}"
  bash "${test}" || __failed=$((__failed + 1))
done

if [[ "${__failed}" -gt 0 ]]; then
  echo "${__failed} test file(s) failed" >&2
  exit 1
fi
