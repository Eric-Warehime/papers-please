# Python. `check FILES`: ruff with opinionated rules added to the repo's selection (C901 is cyclomatic
# complexity), flake8-cognitive-complexity, and the tests whose names match the files.
# `fix FILE`: ruff format, plus safe fixes for a new file, then reports what is left, except unused-name
# rules that fire on half-finished edits. Unused imports are never auto-removed for the same reason.
here=$(cd "$(dirname "$0")" && pwd); . "$here/../lib.sh"
mode=$1; shift
rules="--extend-select C90,PLR09,B,SIM,RET,ARG,ERA,PIE,UP,I,BLE001,S110"
status=0
if ruff=$(pytool ruff); then
  if [ "$mode" = fix ]; then
    # A tracked file keeps its existing style: only changed lines are formatted, bottom-up so earlier
    # ranges keep their line numbers, and lint autofixes are skipped since they rewrite any line.
    if git ls-files --error-unmatch "$1" >/dev/null 2>&1; then
      git diff -U0 "$CODER_BASE" -- "$1" | sed -En 's/^@@ [^+]*\+([0-9]+)(,([0-9]+))? @@.*/\1 \3/p' | sort -rn |
        while read -r s n; do [ "${n:-1}" -eq 0 ] || $ruff format -q --range "$s-$((s + ${n:-1}))" "$1"; done
    else
      $ruff format -q "$1"
      $ruff check -q --fix --unfixable F401 $rules "$1" >/dev/null
    fi
    newonly $ruff check --output-format concise $rules --extend-ignore F401,F841,ARG "$1"
    exit
  fi
  newonly $ruff check --output-format concise $rules "$@" || status=1
fi
[ "$mode" = fix ] && exit 0
if flake8=$(pytool flake8 --with flake8-cognitive-complexity); then
  newonly $flake8 --isolated --select CCR001 --max-cognitive-complexity 15 "$@" || status=1
fi
tests=$(for f in "$@"; do
  b=$(basename "$f" .py)
  case "$b" in (test_*|*_test) echo "$f" ;; (*) git ls-files "*test_$b.py" "*${b}_test.py" ;; esac
done | sort -u)
if [ -n "$tests" ]; then
  # The project's environment when uv manages it: a global pytest can't import the project.
  if [ -f uv.lock ] && command -v uv >/dev/null; then
    pytest="uv run --frozen pytest"
  elif need pytest; then
    pytest=pytest
  fi
  if [ -n "$pytest" ]; then
    out=$($pytest -q $tests 2>&1); r=$?
    [ $r -eq 0 ] || [ $r -eq 5 ] || { printf "%s\n" "$out"; status=1; }  # 5: no tests collected
  fi
fi
exit $status
