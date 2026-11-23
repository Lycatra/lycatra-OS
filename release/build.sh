#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
out=${1:?output directory}
mkdir -p "$out"
tar --exclude=__pycache__ --exclude='*.pyc' --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -czf "$out/lycatra-desktop.tar.gz" assets lib release/bootstrap.sh catalog.json lycatra-desktop install.sh requirements.lock README.md LICENSE
