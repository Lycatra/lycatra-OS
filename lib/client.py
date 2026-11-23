"""Small launcher client for the already-running local index. No network imports."""

import json
import os
import socket


def local_query(stage, phrase):
    if not phrase.strip() or len(phrase) > 500 or phrase.startswith("wiki:"):
        return []
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "lycatra-desktop",
    )
    request = {"stage": stage, "q": phrase}
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(4)
        client.connect(os.path.join(runtime, "lycatra-search.sock"))
        client.sendall(json.dumps(request, ensure_ascii=False).encode() + b"\n")
        return json.loads(client.makefile("rb").readline(131072))
