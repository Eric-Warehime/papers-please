# Lints the given Python files with ruff (repo config wins; complexity and swallowed-exception rules
# added) and runs the tests whose names match them. Unlike go.sh, issues anywhere in a touched file count.
status=0
if command -v ruff >/dev/null; then
  ruff check --extend-select C901,BLE001,S110 "$@" || status=1
fi
# The project's environment when uv manages it: a global pytest can't import the project.
if [ -f uv.lock ] && command -v uv >/dev/null; then
  pytest="uv run --frozen pytest"
elif command -v pytest >/dev/null; then
  pytest=pytest
fi
if [ -n "$pytest" ]; then
  tests=$(for f in "$@"; do
    b=$(basename "$f" .py)
    case "$b" in (test_*|*_test) echo "$f" ;; (*) git ls-files "*test_$b.py" "*${b}_test.py" ;; esac
  done | sort -u)
  if [ -n "$tests" ]; then
    $pytest -q $tests; r=$?
    [ $r -eq 0 ] || [ $r -eq 5 ] || status=1  # 5: no tests collected
  fi
fi
exit $status
