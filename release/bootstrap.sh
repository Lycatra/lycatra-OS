#!/bin/bash
set -euo pipefail
repo=Lycatra/lycatra-OS
fetch=(curl --proto '=https' --proto-redir '=https' -fsSL)
tag=${LYCATRA_DESKTOP_RELEASE:-}
if [[ -z $tag ]]; then
  if [[ ${LYCATRA_DESKTOP_CHANNEL:-stable} == preview ]]; then
    tag=$("${fetch[@]}" "https://api.github.com/repos/$repo/releases?per_page=100" | jq -er 'first(.[] | select(.draft==false) | .tag_name | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+(-preview\\.[0-9]+)?$")))')
  else
    tag=$("${fetch[@]}" "https://api.github.com/repos/$repo/releases/latest" | jq -er .tag_name)
  fi
fi
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-preview\.[0-9]+)?$ ]] || { echo 'Invalid release tag' >&2; exit 2; }
if ! command -v cosign >/dev/null; then omarchy pkg add cosign </dev/tty; fi
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
url="https://github.com/$repo/releases/download/$tag"
for file in lycatra-desktop.tar.gz lycatra-desktop.sigstore.json; do
  "${fetch[@]}" "$url/$file" -o "$tmp/$file"
done
cosign verify-blob --certificate-identity "https://github.com/$repo/.github/workflows/release.yml@refs/tags/$tag" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --bundle "$tmp/lycatra-desktop.sigstore.json" "$tmp/lycatra-desktop.tar.gz"
tar -xzf "$tmp/lycatra-desktop.tar.gz" -C "$tmp"
export LYCATRA_DESKTOP_RELEASE="$tag"
if (( $# )); then "$tmp/install.sh" "$@"; else "$tmp/install.sh" </dev/tty; fi
