#!/usr/bin/env bash
#
# Tests for files/bash/.bashrc and .bash_aliases, which the image copies into
# root's and crypt's home directories. They are sourced by an interactive
# shell rather than run, so these cases source them the same way, with HOME
# pointing at files/bash so `~/.bash_aliases` is the file in this
# repository: kcov measures them under their own names that way.

# shellcheck source=tests/shell/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

__status=0
HOME="${__repo}/files/bash" bash -c '. "${HOME}/.bashrc"; alias ll' \
  >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
check ".bashrc loads .bash_aliases" 0 out "alias ll='ls -lah'"

mkdir "${__scratch}/home"
__status=0
HOME="${__scratch}/home" bash -c '. "$1"; alias' _ "${__repo}/files/bash/.bashrc" \
  >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
if [[ "${__status}" -ne 0 ]] || [[ -s "${__scratch}/out" ]]; then
  fail ".bashrc without .bash_aliases: exit ${__status}, aliases: $(cat "${__scratch}/out")"
else
  echo "ok .bashrc without .bash_aliases defines nothing"
fi

finish
