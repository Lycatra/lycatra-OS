"""Unified settings rendered by the user's native Omarchy menu."""

import contextlib
import fcntl
import shutil
import subprocess
import time
from pathlib import Path

import manage
import models
import search

SWITCHES = {
    "apps": ("Omarchy apps", True),
    "commands": ("Omarchy commands", True),
    "local": ("Local files and folders", True),
    "semantic": ("Local semantic matches", True),
    "wiki": ("Lycatra wiki", False),
    "offline_only": ("Offline mode", False),
    "paused": ("Pause indexing", False),
}


def update(values):
    search.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (search.STATE / "settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cfg = search.settings()
        if values.get("wiki") is True:
            identity = search.session_signature()
            if not identity or not shutil.which("lycatra"):
                raise ValueError(
                    "Sign in with lycatra login before enabling wiki search."
                )
            values = {**values, "wiki_session": identity}
        elif values.get("wiki") is False:
            values = {**values, "wiki_session": None}
        cfg.update(values)
        manage.save(search.CONFIG, cfg)
    if "roots" in values or "exclude" in values:
        for name in models.MODELS:
            if search.database(name).is_file():
                with contextlib.closing(search.connect(name)) as db:
                    search.purge_missing(db, cfg)
    return cfg


def pick(title, choices):
    # Options are separate argv entries. The CLI returns the selected label.
    p = subprocess.run(
        ["omarchy", "menu", "select", title, *choices],
        capture_output=True,
        text=True,
        check=False,
    )
    label = p.stdout.rstrip("\n")
    return label if p.returncode == 0 and label in choices else None


def roots_menu(key):
    label = "Indexed folders" if key == "roots" else "Excluded folders"
    while True:
        cfg = search.settings()
        paths = cfg.get(key, [str(search.HOME)] if key == "roots" else [])
        remove = {"Remove · " + str(path): path for path in paths}
        action = pick(label, ["Add folder", *remove, "Back"])
        if action in (None, "Back"):
            return
        if action == "Add folder":
            p = subprocess.run(
                ["omarchy", "menu", "input", "Absolute folder path"],
                text=True,
                capture_output=True,
                check=False,
            )
            raw = p.stdout.strip()
            if not raw or p.returncode:
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute() or not path.is_dir():
                pick("Choose an existing absolute directory", ["Back"])
                continue
            paths = list(dict.fromkeys([*paths, str(path.resolve())]))
        else:
            paths = [p for p in paths if p != remove[action]]
        update({key: paths})


def model_menu():
    current = search.settings().get("model", models.DEFAULT)
    labels = {
        ("✓ " if key == current else "") + spec["label"]: key
        for key, spec in models.MODELS.items()
    }
    choice = pick("Local embedding model", [*labels, "Back"])
    if choice not in labels or labels[choice] == current:
        return
    name = labels[choice]
    if (
        pick("Download model and prepare its index?", ["Download and prepare", "Back"])
        != "Download and prepare"
    ):
        return
    subprocess.run(
        ["systemctl", "--user", "start", "--no-block", f"lycatra-model@{name}.service"],
        check=True,
    )


def show():
    while True:
        cfg = search.settings()
        labels = {}
        for key, (label, default) in SWITCHES.items():
            enabled = cfg.get(key, default)
            if key == "wiki":
                enabled = (
                    enabled and cfg.get("wiki_session") == search.session_signature()
                )
            labels[f"{label} · {'On' if enabled else 'Off'}"] = key
        model = cfg.get("model", models.DEFAULT)
        label = "Embedding model · " + models.MODELS[model]["label"].split(" · ")[0]
        action = pick(
            "Search settings",
            [
                *labels,
                label,
                "Indexed folders",
                "Excluded folders",
                "Index status",
                "Done",
            ],
        )
        if action in (None, "Done"):
            return
        try:
            if action in labels:
                key = labels[action]
                value = action.endswith("Off")
                if (
                    key == "wiki"
                    and value
                    and pick(
                        "Send search phrases to Lycatra?",
                        ["Enable wiki search", "Back"],
                    )
                    != "Enable wiki search"
                ):
                    continue
                update({key: value})
            elif action == label:
                model_menu()
            elif action in ("Indexed folders", "Excluded folders"):
                roots_menu("roots" if action == "Indexed folders" else "exclude")
            else:
                with contextlib.closing(search.connect()) as db:
                    search.initialize(db)
                    files = db.execute("SELECT count(*) FROM files").fetchone()[0]
                    passages = db.execute("SELECT count(*) FROM passages").fetchone()[0]
                status = manage.read(search.STATE / "model-job.json", {}).get(
                    "message", ""
                )
                options = [v for v in [status, "Back"] if v]
                pick(f"{files:,} files and folders · {passages:,} passages", options)
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            pick(str(e)[:180], ["Back"])


def prepare(name):
    """Serve the old model until the selected model has a consistent index."""
    job = search.STATE / "model-job.json"
    search.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (search.STATE / "model.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tool = manage.ROOT / "lycatra-desktop"
        try:
            manage.save(job, {"message": "Downloading " + models.MODELS[name]["label"]})
            subprocess.run([str(tool), "model-install", name], check=True)
            manage.save(job, {"message": "Preparing " + name + " index"})
            runtime = search.MODEL / "venv/bin/python"
            subprocess.run([str(runtime), str(tool), "model-index", name], check=True)
            old = search.settings().get("model", models.DEFAULT)
            update({"model": name})
            try:
                manage.run("systemctl", "--user", "restart", "lycatra-search.service")
            except subprocess.CalledProcessError:
                update({"model": old})
                manage.run("systemctl", "--user", "restart", "lycatra-search.service")
                raise
            manage.save(job, {"message": "Ready · " + name})
        except Exception:
            manage.save(
                job,
                {
                    "message": "Model change failed · see user journal for lycatra-model@"
                    + name
                },
            )
            raise


def build_index(name):
    encoder = search.Encoder(name)
    with contextlib.closing(search.connect(name)) as db:
        search.initialize(db)
        search.inventory(db, search.settings())
        while True:
            cfg = search.settings()
            if cfg.get("paused") or not cfg.get("local", True):
                time.sleep(5)
                continue
            if (
                shutil.disk_usage(search.STATE).free < 512 * 1024 * 1024
                or search.database(name).stat().st_size
                > cfg.get("index_mib", 2048) * 1024 * 1024
            ):
                raise ValueError("Index space budget reached")
            if not search.enrich(db, cfg, encoder):
                return
