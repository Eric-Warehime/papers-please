# Runs clippy (warnings denied, cognitive complexity and function length added) and tests for each
# crate containing the given files. Unlike go.sh, existing warnings anywhere in the crate count.
crate() { d=$(dirname "$1"); while [ "$d" != . ] && [ ! -f "$d/Cargo.toml" ]; do d=$(dirname "$d"); done; echo "$d"; }
status=0
for c in $(for f in "$@"; do crate "$f"; done | sort -u); do
  [ -f "$c/Cargo.toml" ] || continue
  cargo clippy -q --manifest-path "$c/Cargo.toml" --all-targets -- -D warnings \
    -W clippy::cognitive_complexity -W clippy::too_many_lines || status=1
  cargo test -q --manifest-path "$c/Cargo.toml" || status=1
done
exit $status
