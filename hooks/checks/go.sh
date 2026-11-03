# Lints and tests the Go packages containing the given files, module by module. Only lint issues on
# lines changed since $CODER_BASE count. A repo golangci config wins over checks/golangci.yml;
# gocognit, gocyclo and dupl are enabled either way.
here=$(cd "$(dirname "$0")" && pwd)
modroot() { d=$(dirname "$1"); while [ "$d" != . ] && [ ! -f "$d/go.mod" ]; do d=$(dirname "$d"); done; echo "$d"; }
pairs=$(for f in "$@"; do m=$(modroot "$f"); [ -f "$m/go.mod" ] && printf '%s\t./%s\n' "$m" "$(dirname "${f#"$m"/}")"; done | sort -u)
status=0
for m in $(printf '%s\n' "$pairs" | cut -f1 | sort -u); do
  pkgs=$(printf '%s\n' "$pairs" | awk -F'\t' -v m="$m" '$1 == m { print $2 }')
  (
    cd "$m" || exit 1
    s=0
    if command -v golangci-lint >/dev/null; then
      golangci-lint config path >/dev/null 2>&1 || cfg="--config=$here/golangci.yml"
      golangci-lint run ${cfg:-} -E gocognit,gocyclo,dupl --new-from-rev="$CODER_BASE" $pkgs || s=1
    fi
    go test $pkgs || s=1
    exit $s
  ) || status=1
done
exit $status
