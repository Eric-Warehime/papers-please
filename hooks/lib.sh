# Sourced by checks/*.sh after they set $here to their own directory. Lines they print starting with
# "papers-please: " are relayed to the user once per session instead of to Claude.

# Fails, and reports $1 as missing, if command $1 is not installed.
need() {
  command -v "$1" >/dev/null && return
  echo "papers-please: $1 is not installed, so its checks were skipped" >&2
  return 1
}

# Prints how to run Python CLI $1: from PATH, else through uvx. Extra args go to uvx (e.g. --with a
# plugin), and force uvx since a copy on PATH may lack the plugin.
pytool() {
  name=$1; shift
  if [ $# -eq 0 ] && command -v "$name" >/dev/null; then echo "$name"
  elif command -v uvx >/dev/null; then echo "uvx $* $name"
  else echo "papers-please: $name (or uv to fetch it) is not installed, so its checks were skipped" >&2; return 1; fi
}

# Runs a command, printing its output only if it fails.
quiet() { out=$("$@" 2>&1) || { printf '%s\n' "$out"; return 1; }; }

# Runs a linter, keeping only findings on lines the patch changed (see gate.py newonly).
newonly() { python3 "$here/../gate.py" newonly "$@"; }
