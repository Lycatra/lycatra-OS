import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import models
import preferences
import search


class Settings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, key, val in [
            (search, "STATE", self.root / "state"),
            (search, "CONFIG", self.root / "config/search.json"),
        ]:
            ctx = patch.object(obj, key, val)
            ctx.start()
            self.addCleanup(ctx.stop)

    def test_all_sources_toggle_independently(self):
        choices = iter(
            [
                "Omarchy apps · On",
                "Omarchy commands · On",
                "Local files and folders · On",
                "Local semantic matches · On",
                "Offline mode · Off",
                "Done",
            ]
        )

        def choose(title, rows):
            selected = next(choices)
            self.assertIn(selected, rows)
            return selected

        with patch.object(preferences, "pick", side_effect=choose):
            preferences.show()
        cfg = search.settings()
        self.assertTrue(cfg["offline_only"])
        for key in ["apps", "commands", "local", "semantic"]:
            self.assertFalse(cfg[key])
        with patch(
            "subprocess.run", side_effect=AssertionError("unexpected network worker")
        ):
            self.assertEqual(search.search("wiki", "ordinary query"), [])
            self.assertEqual(search.search("semantic", "find a note"), [])

    def test_account_consent_survives_rotation_and_expires_on_switch(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)}):
            auth = self.root / "lycatra/auth.json"
            auth.parent.mkdir()
            auth.write_text(
                json.dumps({"user": {"id": "a"}, "accessToken": "fixture-a"})
            )
            with patch("shutil.which", return_value="/bin/true"):
                preferences.update({"wiki": True})
            auth.write_text(
                json.dumps({"user": {"id": "a"}, "accessToken": "fixture-b"})
            )
            self.assertEqual(
                search.settings()["wiki_session"], search.session_signature()
            )
            auth.write_text(json.dumps({"user": {"id": "b"}}))
            with patch(
                "subprocess.run",
                side_effect=AssertionError("request after account switch"),
            ):
                self.assertEqual(search.search("wiki", "anything"), [])

    def test_excluding_folder_purges_each_model_index(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "hello.txt").write_text("hello")
        cfg = preferences.update({"roots": [str(docs)]})
        for name in models.MODELS:
            db = search.connect(name)
            search.initialize(db)
            search.inventory(db, cfg)
            self.assertEqual(db.execute("select count(*) from files").fetchone()[0], 1)
            db.close()
        preferences.update({"exclude": [str(docs)]})
        for name in models.MODELS:
            db = search.connect(name)
            self.assertEqual(db.execute("select count(*) from files").fetchone()[0], 0)
            db.close()
