"""Content-addressed backups and compare-before-write installation."""

import base64
import contextlib
import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import menu
from search import HOME, STATE

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
CAT = json.loads((ROOT / "catalog.json").read_text())["components"]
RECEIPT = STATE / "install.json"
UNDO = STATE / "previous.json"
PENDING = STATE / "pending.json"


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def save(path, obj):
    atomic(path, json.dumps(obj, indent=2).encode())


def names(items):
    result = set()

    def add(k):
        if k not in CAT:
            raise ValueError(f"Unknown component {k}")
        for dep in CAT[k]["requires"]:
            add(dep)
        result.add(k)

    for k in CAT if "studio" in items else items:
        add(k)
    return sorted(result)


def snapshot(data, mode):
    return {"data": base64.b64encode(data).decode(), "mode": mode}


def current(p):
    if p.is_symlink():
        raise ValueError(f"Existing symlink requires manual review: {p}")
    return (
        snapshot(p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
    )


def files(selected, owned=None):
    result = {}

    def put(rel, data, mode=0o644):
        result[rel] = snapshot(data.encode() if isinstance(data, str) else data, mode)

    def copy(src, rel):
        put(rel, src.read_bytes(), stat.S_IMODE(src.stat().st_mode))

    def tree(src, rel):
        for p in src.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                copy(p, str(Path(rel) / p.relative_to(src)))

    owned = owned or {}

    def existing_plugin(folder, plugin_id):
        rel = ".config/omarchy/plugins/" + folder + "/manifest.json"
        return (
            not any(k.startswith(str(Path(rel).parent) + "/") for k in owned)
            and read(HOME / rel, {}).get("id") == plugin_id
        )

    plugins = ".config/omarchy/plugins/"
    if (
        "webapp" in selected
        and not (HOME / ".local/share/applications/Lycatra.desktop").exists()
    ):
        put(
            ".local/share/applications/lycatra-desktop.desktop",
            "[Desktop Entry]\nType=Application\nName=Lycatra\nExec=lycatra-desktop webapp\nIcon=web-browser\nCategories=Office;\n",
        )
    if "search" in selected:
        put(
            ".local/share/applications/lycatra-search-settings.desktop",
            "[Desktop Entry]\nType=Application\nName=Search settings\nComment=Omarchy and Lycatra search preferences\nExec=lycatra-desktop settings\nIcon=preferences-system-search\nCategories=Settings;\n",
        )
        copy(ASSETS / "model.service", ".config/systemd/user/lycatra-model@.service")
        for name, data in menu.render().items():
            put(plugins + "lycatra.menu/" + name, data)
        copy(ASSETS / "search.service", ".config/systemd/user/lycatra-search.service")
        for ext in ("service", "path"):
            copy(
                ASSETS / ("compat." + ext),
                ".config/systemd/user/lycatra-menu-compat." + ext,
            )
    for key in ("phone", "media", "keyboard", "updates"):
        plugin = CAT[key]["plugin"]
        if key in selected and not existing_plugin(plugin, plugin):
            tree(ASSETS / key, plugins + plugin)
    if "media" in selected:
        for part, dest in [
            ("bin", ".local/bin"),
            ("systemd", ".config/systemd/user"),
            ("media_focus", ".local/lib/omarchy-media-focus/media_focus"),
        ]:
            source = ASSETS / "focus" / part
            external = all(
                (HOME / dest / p.name).is_file()
                and str(Path(dest) / p.name) not in owned
                for p in source.iterdir()
                if p.is_file()
            )
            if not external:
                tree(source, dest)
    if "dictation" in selected:
        put(
            ".config/hypr/lycatra-dictation.lua",
            """local tool = o.shell_quote(os.getenv("HOME") .. "/.local/bin/dictation-focus")
hl.unbind("ALT + SPACE")
o.bind_hold("ALT + SPACE", "Start studio dictation", tool .. " start", "Transcribe studio dictation", tool .. " stop")
""",
        )
        for p in (ASSETS / "focus/integrations/superwhisper").iterdir():
            copy(
                p,
                (".config/systemd/user/" if p.suffix == ".service" else ".local/bin/")
                + p.name,
            )
        source = Path("/opt/superwhisper/assets/omarchy-plugin/superwhisper-panel")
        if (
            source.exists()
            and not (HOME / (plugins + "superwhisper-panel")).is_symlink()
        ):
            tree(source, plugins + "superwhisper-panel")
    if "developer" in selected:
        put(
            ".local/share/applications/lycatra-agent.desktop",
            "[Desktop Entry]\nType=Application\nName=Studio Coding Agent\nExec=codex\nTerminal=true\nCategories=Development;\n",
        )
    if "style" in selected:
        put(
            ".config/hypr/lycatra-studio.lua",
            "hl.config({ decoration = { rounding = 0 } })\n",
        )
    return result


def dst(rel):
    path = HOME / rel
    if (
        Path(rel).is_absolute()
        or ".." in Path(rel).parts
        or not path.parent.resolve().is_relative_to(HOME.resolve())
    ):
        raise ValueError(f"Unsafe destination {rel}")
    return path


def inspect(selected):
    receipt = read(RECEIPT, {"selected": [], "owned": {}})
    if os.environ.get("LYCATRA_DESKTOP_INSTALLING") and "manager" not in receipt:
        existing = HOME / ".local/bin/lycatra-desktop"
        if (
            existing.is_symlink()
            and existing.resolve().parent.parent
            == HOME / ".local/share/lycatra-desktop/releases"
        ):
            receipt["manager"] = str(existing.resolve())
    target = files(selected, receipt["owned"])
    changes = {}
    conflicts = []
    owned = {}
    for rel in sorted(set(target) | set(receipt["owned"])):
        old = receipt["owned"].get(rel)
        desired = target.get(rel)
        actual = current(dst(rel))
        if actual == desired and not old:
            continue
        if old and actual != old or not old and actual is not None:
            conflicts.append(rel)
            continue
        if desired is not None:
            owned[rel] = desired
        if actual != desired:
            changes[rel] = [actual, desired]
    # Edit only the named include in shared user configuration.
    for key, filename, module in [
        ("style", "looknfeel", "studio"),
        ("dictation", "bindings", "dictation"),
    ]:
        if key not in selected and key not in receipt["selected"]:
            continue
        rel = f".config/hypr/{filename}.lua"
        marker = f'-- Lycatra Desktop {key}\nrequire("hypr.lycatra-{module}")\n-- End Lycatra Desktop {key}\n'.encode()
        actual = current(dst(rel))
        original = base64.b64decode(actual["data"]) if actual else b""
        updated = original
        if key in selected and marker not in original:
            if key == "dictation" and b"dictation-focus" in original:
                continue
            if key == "dictation" and any(
                b"ALT + SPACE" in line.upper()
                for line in original.splitlines()
                if not line.lstrip().startswith(b"--")
            ):
                conflicts.append(rel)
                continue
            if b"-- Lycatra Desktop " + key.encode() in original:
                conflicts.append(rel)
                continue
            updated = (
                original
                + (b"\n" if original and not original.endswith(b"\n") else b"")
                + marker
            )
        if key not in selected:
            updated = original.replace(marker, b"")
        if updated != original:
            changes[rel] = [
                actual,
                snapshot(updated, actual["mode"] if actual else 0o644),
            ]
    missing = sorted(
        {c for k in selected for c in CAT[k].get("commands", []) if not shutil.which(c)}
    )
    new = {**receipt, "selected": selected, "owned": owned}
    install = os.environ.get("LYCATRA_DESKTOP_INSTALLING")
    if install:
        # Only an executable inside the manager's release store can claim a release.
        executable = Path(install).resolve()
        if (
            executable == ROOT / "lycatra-desktop"
            and executable.parent.parent
            == HOME / ".local/share/lycatra-desktop/releases"
        ):
            new["manager"] = str(executable)
            new["tag"] = os.environ.get("LYCATRA_DESKTOP_RELEASE", "")
    return receipt, new, changes, conflicts, missing


def write(rel, value):
    path = dst(rel)
    if value is None:
        path.unlink(missing_ok=True)
    else:
        atomic(path, base64.b64decode(value["data"]), value["mode"])


def commit(tx):
    for rel, (before, after) in tx["changes"].items():
        actual = current(dst(rel))
        if actual not in (before, after):
            raise ValueError(f"Edited during transaction: {rel}")
        if actual != after:
            write(rel, after)
    manager = tx["new"].get("manager")
    if manager:
        target = Path(manager)
        if (
            not target.is_file()
            or target.name != "lycatra-desktop"
            or target.parent.parent != HOME / ".local/share/lycatra-desktop/releases"
        ):
            raise ValueError("Previous release executable unavailable")
        link = HOME / ".local/bin/lycatra-desktop"
        if link.exists() and not link.is_symlink():
            raise ValueError("Existing manager is not owned by the installer")
        pending = link.with_suffix(".lycatra-new")
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        os.replace(pending, link)
    save(RECEIPT, tx["new"])
    save(UNDO, tx)
    PENDING.unlink(missing_ok=True)


def inverse(tx):
    return {
        "old": tx["new"],
        "new": tx["old"],
        "changes": {p: [b, a] for p, (a, b) in tx["changes"].items()},
    }


@contextlib.contextmanager
def locked():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "install.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def manage(op, items, stage):
    with contextlib.nullcontext() if op in ("inspect", "verify", "plan") else locked():
        if PENDING.exists():
            if op in ("inspect", "plan", "verify"):
                raise ValueError("Interrupted installation. Run apply to resume.")
            commit(read(PENDING, {}))
        old = read(RECEIPT, {"selected": [], "owned": {}})
        if op == "rollback":
            if not UNDO.exists():
                raise ValueError("No previous change")
            tx = inverse(read(UNDO, {}))
            for rel, (before, _) in tx["changes"].items():
                if current(dst(rel)) != before:
                    raise ValueError(f"Rollback would overwrite an edit: {rel}")
        else:
            if op == "remove":
                remove = set(
                    names(["studio"]) if not items or "studio" in items else items
                )
                selected = names(set(old["selected"]) - remove)
                if remove.intersection(selected):
                    raise ValueError("Remove dependent components together")
            else:
                selected = names(set(old["selected"]) | set(items))
            old, new, changes, conflicts, missing = inspect(selected)
            report = dict(
                selected=selected,
                changes=list(changes),
                conflicts=conflicts,
                missing_commands=missing,
            )
            if op in ("plan", "inspect", "verify"):
                if op == "verify" and not stage:
                    plugins = (
                        plugin_state()
                        if any("plugin" in CAT[k] for k in selected)
                        else {}
                    )
                    report["inactive"] = [
                        c["plugin"]
                        for k in selected
                        if (c := CAT[k]).get("plugin") and not plugins.get(c["plugin"])
                    ]
                    report["inactive"] += [
                        c["service"]
                        for k in selected
                        if (c := CAT[k]).get("service")
                        and subprocess.run(
                            [
                                "systemctl",
                                "--user",
                                "is-active",
                                "--quiet",
                                c["service"],
                            ]
                        ).returncode
                    ]
                return report
            if conflicts or (missing and not stage):
                raise ValueError(json.dumps(report))
            tx = dict(old=old, new=new, changes=changes)
        # Re-run activation on retry, even if materialized files already match.
        changed = bool(tx["changes"]) or tx["old"] != tx["new"]
        if not stage:
            save_baseline(tx["new"]["selected"])
            previously_active = read(STATE / "active.json", [])
            deactivate(set(previously_active) - set(tx["new"]["selected"]))
        if changed:
            save(PENDING, tx)
            commit(tx)
        if not stage:
            activate(tx["new"]["selected"])
            if "search" in tx["new"]["selected"] and tx["old"].get("manager") != tx[
                "new"
            ].get("manager"):
                run("systemctl", "--user", "restart", "lycatra-search.service")
            save(STATE / "active.json", tx["new"]["selected"])
            baseline = read(STATE / "baseline.json", {})
            save(
                STATE / "baseline.json",
                {k: v for k, v in baseline.items() if k in tx["new"]["selected"]},
            )
        return dict(
            selected=tx["new"]["selected"], changed=changed, activated=not stage
        )


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def plugin_state():
    return {
        item["id"]: item["enabled"]
        for item in json.loads(
            subprocess.check_output(["omarchy", "plugin", "list", "--json"])
        )
    }


def service_state(name):
    return (
        subprocess.run(
            ["systemctl", "--user", "is-enabled", "--quiet", name]
        ).returncode
        == 0
    )


def save_baseline(keys):
    baseline = read(STATE / "baseline.json", {})
    new = [k for k in keys if k not in baseline]
    plugins = plugin_state() if any("plugin" in CAT[k] for k in new) else {}
    for k in new:
        c = CAT[k]
        baseline[k] = {
            "plugin": plugins.get(c.get("plugin"), False),
            "service": service_state(c["service"]) if "service" in c else False,
        }
    save(STATE / "baseline.json", baseline)


def deactivate(keys):
    baseline = read(STATE / "baseline.json", {})
    for k in keys:
        c = CAT[k]
        if "plugin" in c and not baseline.get(k, {}).get("plugin"):
            run("omarchy", "plugin", "disable", c["plugin"])
        if "service" in c and not baseline.get(k, {}).get("service"):
            run("systemctl", "--user", "disable", "--now", c["service"])


def activate(keys):
    run("systemctl", "--user", "daemon-reload")
    if any("plugin" in CAT[k] for k in keys):
        run("omarchy-shell", "shell", "rescanPlugins")
    for k in keys:
        c = CAT[k]
        if "service" in c:
            run("systemctl", "--user", "enable", "--now", c["service"])
        if "plugin" in c:
            for attempt in range(20):
                found = json.loads(
                    subprocess.check_output(["omarchy", "plugin", "list", "--json"])
                )
                if any(x["id"] == c["plugin"] for x in found):
                    break
                time.sleep(0.1)
            run("omarchy", "plugin", "enable", c["plugin"])
