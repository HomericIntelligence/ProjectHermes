#!/usr/bin/env bash
# build-tags.sh — Emit an additional docker/metadata-action `tags:` line
# when the operator supplied a manual image_tag override.
# Reads $IMAGE_TAG from the environment (NEVER from $1, to keep callers
# from accidentally inlining ${{ }} into shell). Writes to stdout.
set -euo pipefail

raw="${IMAGE_TAG-}"

# Empty input: no override requested. Emit nothing, exit 0.
if [[ -z "$raw" ]]; then
  exit 0
fi

# Strip CR/LF/tab (defence in depth: GitHub's UI text field is single-line,
# but the dispatch API accepts arbitrary strings).
stripped="${raw//$'\r'/}"
stripped="${stripped//$'\n'/}"
stripped="${stripped//$'\t'/}"

# Keep only the Docker tag charset: [A-Za-z0-9_.-]. Anything else becomes ''.
clean="$(printf '%s' "$stripped" | LC_ALL=C tr -cd 'A-Za-z0-9_.-')"

# Docker rejects leading '.' or '-'; strip the entire leading run of '.' and '-'.
# ${clean%%[!.-]*} yields the longest leading run of [.-] chars; we then strip
# that prefix so inputs like '.-foo' or '...foo' correctly become 'foo'.
clean="${clean#"${clean%%[!.-]*}"}"

# Truncate to Docker's 128-char tag limit.
clean="${clean:0:128}"

if [[ -z "$clean" ]]; then
  echo "::error::image_tag '$raw' contained no valid Docker tag characters" >&2
  exit 2
fi

printf 'type=raw,value=%s\n' "$clean"
