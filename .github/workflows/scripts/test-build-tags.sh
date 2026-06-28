#!/usr/bin/env bash
# test-build-tags.sh — exercise build-tags.sh on representative inputs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/build-tags.sh"

pass=0; fail=0
assert_eq() {
  local label="$1" want="$2" got="$3"
  if [[ "$want" == "$got" ]]; then
    echo "PASS: $label"
    pass=$((pass+1))
  else
    echo "FAIL: $label"
    echo "  want: $(printf '%q' "$want")"
    echo "  got:  $(printf '%q' "$got")"
    fail=$((fail+1))
  fi
}

# Case A: empty input -> empty stdout, exit 0.
out="$(IMAGE_TAG="" bash "$SCRIPT")"
assert_eq "empty input emits no tag line" "" "$out"

# Case B: clean input -> single type=raw line.
out="$(IMAGE_TAG="hotfix-v1.2.3" bash "$SCRIPT")"
assert_eq "clean input emits one raw line" "type=raw,value=hotfix-v1.2.3" "$out"

# Case C: input with CR/LF/control chars + leading dot -> sanitized single line.
out="$(IMAGE_TAG=$'\rhot\nfix\t-v1.2.3' bash "$SCRIPT")"
assert_eq "CR/LF/tab stripped, single line" "type=raw,value=hotfix-v1.2.3" "$out"

# Case D: input that becomes empty after sanitization -> nonzero exit.
set +e
IMAGE_TAG='!!!@@@###' bash "$SCRIPT" >/dev/null 2>&1
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  echo "PASS: all-invalid input exits non-zero (rc=$rc)"
  pass=$((pass+1))
else
  echo "FAIL: all-invalid input should exit non-zero"
  fail=$((fail+1))
fi

echo
echo "Summary: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
