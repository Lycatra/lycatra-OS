"""Offline smoke benchmark with the actual installed model, in a temporary index."""

import contextlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import search

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "work"
    folder.mkdir()
    search.STATE = Path(tmp) / "state"
    cfg = {"roots": [str(folder)]}
    samples = {
        "camera.md": "We verify two independent copies of the camera originals before erasing the memory card.",
        "food.md": "Bake the sourdough bread until the crust turns golden brown.",
        "train.md": "The train to Zurich departs from platform three every morning.",
    }
    for name, body in samples.items():
        (folder / name).write_text(body)
    model = search.Encoder()
    with contextlib.closing(search.connect()) as db:
        search.initialize(db)
        search.inventory(db, cfg)
        search.enrich(db, cfg, model)
        for query, expected in [
            ("How should I protect footage before formatting storage?", "camera.md"),
            ("Wann fährt die Bahn nach Zürich?", "train.md"),
        ]:
            start = time.monotonic()
            hits = search.semantic(db, query, cfg, model)
            assert hits[0]["label"] == expected, hits
            print(expected, round((time.monotonic() - start) * 1000), "ms")
