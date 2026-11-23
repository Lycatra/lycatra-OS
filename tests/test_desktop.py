import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Desktop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_STATE_HOME=str(self.home / ".local/state"),
            XDG_RUNTIME_DIR=str(self.home),
        )

    def cli(self, *args, ok=True):
        p = subprocess.run(
            [ROOT / "lycatra-desktop", *args],
            env=self.env,
            text=True,
            capture_output=True,
        )
        if not ok:
            self.assertNotEqual(p.returncode, 0)
            return
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_install_rollback_conflict(self):
        self.cli("apply", "webapp", "--stage")
        self.assertFalse(self.cli("apply", "webapp", "--stage")["changed"])
        self.cli("remove", "webapp", "--stage")
        self.cli("rollback", "--stage")
        launcher = self.home / ".local/share/applications/lycatra-desktop.desktop"
        self.assertTrue(launcher.is_file())
        launcher.write_text("mine")
        self.cli("remove", "webapp", "--stage", ok=False)
        self.assertEqual(launcher.read_text(), "mine")

    @unittest.skipUnless(
        Path("/usr/share/omarchy/shell/plugins/menu/Menu.qml").exists(),
        "requires Omarchy",
    )
    def test_menu(self):
        self.cli("apply", "search", "--stage")
        plugin = self.home / ".config/omarchy/plugins/lycatra.menu"
        self.assertEqual(
            json.loads((plugin / "manifest.json").read_text())["omarchy"]["clonedFrom"],
            "omarchy.menu",
        )
        subprocess.run(
            ["/usr/lib/qt6/bin/qmlformat", str(plugin / "Menu.qml")],
            stdout=subprocess.DEVNULL,
            check=True,
        )

    def test_upgrade_preserves_unmanaged_config(self):
        lua = self.home / ".config/hypr/looknfeel.lua"
        lua.parent.mkdir(parents=True)
        lua.write_text("-- custom settings\n")
        self.cli("apply", "style", "--stage")
        lua.write_text(lua.read_text() + "-- more settings\n")
        self.cli("remove", "style", "--stage")
        self.assertEqual(lua.read_text(), "-- custom settings\n-- more settings\n")

    def test_local_prefix_and_wiki_consent(self):
        fake = self.home / "bin"
        fake.mkdir()
        flag = self.home / "called"
        (fake / "lycatra").write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\nPath("
            + repr(str(flag))
            + ").touch()\nprint('{}')\n"
        )
        (fake / "lycatra").chmod(0o755)
        self.env["PATH"] = str(fake) + os.pathsep + self.env["PATH"]
        self.cli("configure", "--wiki", "on", ok=False)
        auth = self.home / ".config/lycatra/auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text('{"user":{"id":"example"}}')
        self.cli("configure", "--wiki", "on")
        self.assertEqual(self.cli("query", "wiki", "files: invoice"), [])
        self.assertFalse(flag.exists())
        self.cli("query", "wiki", "handbook")
        self.assertTrue(flag.exists())
        flag.unlink()
        auth.unlink()
        self.assertEqual(self.cli("query", "wiki", "handbook"), [])
        self.assertFalse(flag.exists())

    def test_signed_archive_layout(self):
        subprocess.run([ROOT / "release/build.sh", self.temp.name], check=True)
        import tarfile

        with tarfile.open(self.home / "lycatra-desktop.tar.gz") as archive:
            names = archive.getnames()
        self.assertIn("assets/phone/LICENSE", names)
        self.assertIn("LICENSE", names)
        self.assertFalse(
            any(".git/" in n or "__pycache__" in n or n.endswith(".db") for n in names)
        )

    def test_index(self):
        docs = self.home / "docs"
        docs.mkdir()
        (docs / "production.md").write_text(
            "Camera originals are copied and checked using checksums"
        )
        (docs / "private.key").write_text("originals")
        (docs / "link.txt").symlink_to("/etc/passwd")
        self.cli("configure", "--root", str(docs))

        def index():
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import search as s; d=s.connect(); s.initialize(d); s.inventory(d,s.settings()); s.enrich(d,s.settings(),None)",
                ],
                env=dict(self.env, PYTHONPATH=str(ROOT / "lib")),
                check=True,
            )

        index()
        self.assertEqual(
            [x["label"] for x in self.cli("query", "lexical", "originals")],
            ["production.md"],
        )
        self.assertEqual(self.cli("query", "wiki", "files: secret"), [])
        self.assertEqual(self.cli("query", "lexical", "private"), [])
        (docs / "production.md").unlink()
        index()
        self.assertEqual(self.cli("status")["files"], 0)
