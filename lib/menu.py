"""Build a stock-menu clone only against the reviewed Omarchy source."""

import hashlib
import json
import subprocess
from pathlib import Path

SOURCE = Path("/usr/share/omarchy/shell/plugins/menu")
SUPPORTED = "f76b25be69e80fbb139d29ca8e4bca8a98078f559d203750c3ced0f86f6d5f21"


def render():
    data = (SOURCE / "Menu.qml").read_bytes()
    if hashlib.sha256(data).hexdigest() != SUPPORTED:
        raise ValueError(
            "Unsupported Omarchy menu version. Install a compatible Lycatra Desktop release."
        )
    patch = Path(__file__).resolve().parents[1] / "assets/menu.patch"
    patched = subprocess.run(
        [
            "patch",
            "--silent",
            "--batch",
            "--fuzz=0",
            "--output=-",
            str(SOURCE / "Menu.qml"),
            str(patch),
        ],
        capture_output=True,
        check=True,
    ).stdout
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    manifest.update(
        id="lycatra.menu", name="Lycatra Search", omarchy={"clonedFrom": "omarchy.menu"}
    )
    return {
        "Menu.qml": patched,
        "manifest.json": json.dumps(manifest, indent=2).encode(),
        **{p: (SOURCE / p).read_bytes() for p in ("MenuModel.js", "BarWidget.qml")},
    }
