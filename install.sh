#!/bin/sh
set -eu
src=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
dest="$HOME/.local/share/lycatra-desktop/releases"
mkdir -p "$dest" "$HOME/.local/bin"
exec 9>"$dest/.install.lock"
flock 9
stage=$(mktemp -d "$dest/.install.XXXXXX")
trap 'test -z "$stage" || rm -rf -- "$stage"' EXIT
mkdir "$stage/release"
cp "$src/release/bootstrap.sh" "$stage/release/"
cp -R "$src/lib" "$src/assets" "$src/catalog.json" "$src/requirements.lock" "$src/lycatra-desktop" "$stage/"
find "$stage" -type d -name __pycache__ -prune -exec rm -rf {} +
digest=$(cd "$stage" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
bin="$HOME/.local/bin/lycatra-desktop"
if [ -e "$bin" ] || [ -L "$bin" ]; then
  case "$(readlink "$bin" || true)" in "$dest"/*/lycatra-desktop) ;; *) echo "Existing launcher at $bin" >&2; exit 1;; esac
fi
if [ ! -d "$dest/$digest" ]; then mv -T "$stage" "$dest/$digest"; stage=''; fi
LYCATRA_DESKTOP_INSTALLING="$dest/$digest/lycatra-desktop" "$dest/$digest/lycatra-desktop" apply "$@"
