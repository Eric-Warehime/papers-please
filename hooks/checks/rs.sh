# Rust. `check FILES`: clippy with complexity, length and nesting lints (thresholds from
# checks/clippy.toml unless the crate has its own clippy.toml), lizard for cyclomatic complexity, which
# clippy lacks, and each touched crate's tests. `fix FILE`: rustfmt on that file alone.
here=$(cd "$(dirname "$0")" && pwd); . "$here/../lib.sh"
mode=$1; shift
crate() { d=$(dirname "$1"); while [ "$d" != . ] && [ ! -f "$d/Cargo.toml" ]; do d=$(dirname "$d"); done; echo "$d"; }
workspace() { dirname "$(cargo locate-project --workspace --manifest-path "$1/Cargo.toml" --message-format plain)"; }
if [ "$mode" = fix ]; then
  c=$(crate "$1")
  [ -f "$c/Cargo.toml" ] && command -v rustfmt >/dev/null || exit 0
  ed=$(sed -n 's/^edition *= *"\([0-9]*\)".*/\1/p' "$c/Cargo.toml" "$(workspace "$c")/Cargo.toml" | head -1)
  # Through stdin so rustfmt does not also reformat the modules this file declares.
  out=$(cd "$c" && rustfmt --edition "${ed:-2021}" --emit stdout <"$OLDPWD/$1") && printf '%s\n' "$out" >"$1"
  exit 0
fi
crates=$(for f in "$@"; do c=$(crate "$f"); [ -f "$c/Cargo.toml" ] && echo "$c"; done | sort -u)
[ -n "$crates" ] && need cargo || exit 0
lints="-W clippy::cognitive_complexity -W clippy::too_many_lines -W clippy::excessive_nesting
  -W clippy::redundant_else -W clippy::manual_let_else -W clippy::needless_pass_by_value"
status=0
for c in $crates; do
  (
    d=$c; until [ -f "$d/clippy.toml" ] || [ -f "$d/.clippy.toml" ]; do
      [ "$d" = . ] && export CLIPPY_CONF_DIR="$here" && break; d=$(dirname "$d")
    done
    # Diagnostic paths are relative to the workspace root.
    cd "$(workspace "$c")" && newonly cargo clippy -q --message-format short \
      --manifest-path "$OLDPWD/$c/Cargo.toml" --all-targets -- $lints
  ) || status=1
  quiet cargo test -q --manifest-path "$c/Cargo.toml" || status=1
done
if lizard=$(pytool lizard); then
  newonly $lizard -w -C 15 "$@" || status=1
fi
exit $status
