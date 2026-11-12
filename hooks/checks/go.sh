# Go. `check FILES`: golangci-lint and the tests of each touched package, module by module. A module's
# own golangci config wins over checks/golangci.yml, but still gets the two complexity linters and dupl.
# `fix FILE`: the configured golangci-lint formatters.
here=$(cd "$(dirname "$0")" && pwd); . "$here/../lib.sh"
mode=$1; shift
modroot() { d=$(dirname "$1"); while [ "$d" != . ] && [ ! -f "$d/go.mod" ]; do d=$(dirname "$d"); done; echo "$d"; }
cfg() { golangci-lint config path >/dev/null 2>&1 || echo "--config=$here/golangci.yml"; }
if [ "$mode" = fix ]; then
  m=$(modroot "$1")
  [ -f "$m/go.mod" ] && command -v golangci-lint >/dev/null && cd "$m" && golangci-lint fmt $(cfg) "${1#"$m"/}"
  exit 0
fi
pairs=$(for f in "$@"; do m=$(modroot "$f"); [ -f "$m/go.mod" ] && printf '%s\t./%s\n' "$m" "$(dirname "${f#"$m"/}")"; done | sort -u)
[ -n "$pairs" ] && need go || exit 0
lint=$(need golangci-lint && echo y)
status=0
for m in $(printf '%s\n' "$pairs" | cut -f1 | sort -u); do
  pkgs=$(printf '%s\n' "$pairs" | awk -F'\t' -v m="$m" '$1 == m { print $2 }')
  (
    cd "$m" || exit 1
    s=0
    if [ -n "$lint" ]; then
      newonly golangci-lint run $(cfg) -E gocognit,gocyclo,dupl --path-mode=abs --uniq-by-line=false \
        --max-issues-per-linter=0 --max-same-issues=0 $pkgs || s=1
    fi
    quiet go test $pkgs || s=1
    exit $s
  ) || status=1
done
exit $status
