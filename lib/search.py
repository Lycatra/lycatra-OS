"""Private, incremental desktop search. Only wiki() accesses the network."""

import contextlib
import fcntl
import hashlib
import heapq
import itertools
import json
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import models

HOME = Path.home()
STATE = Path(os.getenv("XDG_STATE_HOME", HOME / ".local/state")) / "lycatra-desktop"
CONFIG = (
    Path(os.getenv("XDG_CONFIG_HOME", HOME / ".config")) / "lycatra-desktop/search.json"
)
MODEL = Path(
    os.getenv("LYCATRA_DESKTOP_MODEL", HOME / ".local/share/lycatra-desktop/model")
)
SOCKET = Path(os.getenv("XDG_RUNTIME_DIR", STATE)) / "lycatra-search.sock"
SKIP = {
    "node_modules",
    "target",
    "__pycache__",
    "vendor",
    "build",
    "dist",
    "venv",
    "keyrings",
    "credentials",
    "secrets",
}
TEXT = set(
    "md txt csv rst org tex log py js jsx ts tsx html css c h cpp rs go java kt qml sh lua yaml yml toml".split()
)


def settings():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}


def roots(cfg):
    if "_roots" in cfg:
        return cfg["_roots"]
    return [Path(s).expanduser().resolve() for s in cfg.get("roots", [str(HOME)])]


def eligible(path, cfg, *, walking=False):
    if not any(path == root or root in path.parents for root in roots(cfg)):
        return False
    if (not walking and path.resolve() != path) or any(
        part.startswith(".") or part.lower() in SKIP for part in path.parts
    ):
        return False
    if path.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".kdbx",
    } or path.name.lower() in {
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "passwords.txt",
    }:
        return False
    return not any(
        path.is_relative_to(Path(s).expanduser().resolve())
        for s in cfg.get("exclude", [])
    )


def database(name=None):
    name = name or settings().get("model", models.DEFAULT)
    if name not in models.MODELS:
        raise ValueError("Unknown index model")
    return STATE / (
        "search-v1.db" if name == models.DEFAULT else "search-" + name + ".db"
    )


def connect(name=None):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(database(name), timeout=15)
    db.execute("PRAGMA secure_delete=ON")
    return db


def initialize(db):
    db.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA secure_delete=ON;
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,path TEXT UNIQUE,stamp TEXT,done INTEGER DEFAULT 0);
    CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(path,body,tokenize='unicode61');
    INSERT INTO words(words,rank) VALUES('secure-delete',1);
    CREATE TABLE IF NOT EXISTS passages(file INTEGER,text TEXT,vector BLOB);
    CREATE INDEX IF NOT EXISTS by_file ON passages(file);
    """)


def forget(db, fid):
    db.execute("DELETE FROM words WHERE rowid=?", (fid,))
    db.execute("DELETE FROM passages WHERE file=?", (fid,))
    db.execute("DELETE FROM files WHERE id=?", (fid,))


def stamp(st):
    return f"{st.st_dev}/{st.st_ino}/{st.st_mtime_ns}/{st.st_size}/{st.st_mode}"


def inventory(db, cfg):
    cfg = {**cfg, "_roots": roots(cfg)}
    for root in roots(cfg):
        if not root.is_dir() or not eligible(root, cfg):
            continue
        device = root.stat().st_dev
        for base, dirs, files in os.walk(root):
            base = Path(base)
            allowed = []
            with db:
                for name in itertools.chain(dirs, files):
                    path = base / name
                    try:
                        st = path.lstat()
                        if (
                            st.st_dev != device
                            or stat.S_ISLNK(st.st_mode)
                            or not eligible(path, cfg, walking=True)
                            or not os.access(path, os.R_OK)
                        ):
                            continue
                    except (OSError, RuntimeError):
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        allowed.append(name)
                    elif not stat.S_ISREG(st.st_mode):
                        continue
                    old = db.execute(
                        "SELECT id,stamp FROM files WHERE path=?", (str(path),)
                    ).fetchone()
                    if old and old[1] == stamp(st):
                        continue
                    if old:
                        forget(db, old[0])
                    fid = db.execute(
                        "INSERT INTO files(path,stamp) VALUES(?,?)",
                        (str(path), stamp(st)),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO words(rowid,path,body) VALUES(?,?, '')",
                        (fid, str(path)),
                    )
            dirs[:] = allowed
    purge_missing(db, cfg)


def purge_missing(db, cfg):
    for fid, raw in db.execute("SELECT id,path FROM files"):
        path = Path(raw)
        if not eligible(path, cfg) or not path.exists() or not os.access(path, os.R_OK):
            with db:
                forget(db, fid)


def extract(path, expected):
    ext = path.suffix.lower().lstrip(".")
    if ext not in TEXT and ext not in {"pdf", "docx", "odt"}:
        return ""
    # Check the object that was actually opened. Renames and symlink swaps
    # between inventory and extraction cannot substitute a different inode.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stamp(info) != expected
            or info.st_size > 32 * 1024 * 1024
        ):
            return ""
        if ext in TEXT:
            data = source.read(131072)
            return "" if b"\0" in data else data.decode("utf8", "replace")
        if ext == "pdf":
            if not shutil.which("pdftotext"):
                return ""
            with tempfile.TemporaryFile() as out:
                subprocess.run(
                    [
                        "prlimit",
                        "--as=536870912",
                        "--cpu=6",
                        "--fsize=1048576",
                        "--",
                        "pdftotext",
                        "-f",
                        "1",
                        "-l",
                        "40",
                        "-",
                        "-",
                    ],
                    stdin=source,
                    stdout=out,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=True,
                )
                out.seek(0)
                return out.read(131072).decode("utf8", "replace")
        with zipfile.ZipFile(source) as archive:
            member = archive.getinfo(
                "word/document.xml" if ext == "docx" else "content.xml"
            )
            if member.file_size < 2_000_000:
                return " ".join(
                    ElementTree.fromstring(archive.read(member)).itertext()
                )[:131072]
    return ""


class Encoder:
    def __init__(self, name=models.DEFAULT):
        folder = models.folder(MODEL, name)
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(folder / "model.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self.tokens = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokens.enable_truncation(max_length=512)
        self.lock = threading.Lock()

    def encode(self, text):
        np = self.np
        with self.lock:
            ids = np.asarray([self.tokens.encode(text).ids], dtype=np.int64)
            values = {
                "input_ids": ids,
                "attention_mask": np.ones_like(ids),
                "token_type_ids": np.zeros_like(ids),
            }
            hidden = self.session.run(
                None, {i.name: values[i.name] for i in self.session.get_inputs()}
            )[0]
        vec = hidden[0].mean(axis=0)
        return (vec / np.linalg.norm(vec)).astype("<f4")


def enrich(db, cfg, encoder):
    jobs = db.execute(
        "SELECT id,path,stamp FROM files WHERE done < ? ORDER BY id LIMIT 128",
        (2 if encoder else 1,),
    ).fetchall()
    for fid, raw, saved in jobs:
        path = Path(raw)
        try:
            if not visible(path, saved, cfg):
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute(
                        "SELECT 1 FROM files WHERE id=? AND path=? AND stamp=?",
                        (fid, raw, saved),
                    ).fetchone():
                        forget(db, fid)
                continue
            body = extract(path, saved)
        except (
            OSError,
            KeyError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            zipfile.BadZipFile,
            ElementTree.ParseError,
        ):
            body = ""
        words = body.split()
        chunks = (
            [
                " ".join(words[start : start + 160])
                for start in range(0, min(len(words), 4800), 150)
            ]
            if encoder
            else []
        )
        vectors = [
            (
                fid,
                chunk,
                encoder.encode("passage: " + path.name + " " + chunk).tobytes(),
            )
            for chunk in chunks
        ]
        with db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM files WHERE id=? AND path=? AND stamp=?",
                (fid, raw, saved),
            ).fetchone():
                continue
            db.execute("DELETE FROM passages WHERE file=?", (fid,))
            db.execute("UPDATE words SET body=? WHERE rowid=?", (body, fid))
            db.executemany("INSERT INTO passages VALUES(?,?,?)", vectors)
            db.execute("UPDATE files SET done=? WHERE id=?", (2 if encoder else 1, fid))

    return len(jobs)


def row(path, detail, exact=False):
    return dict(
        kind="file",
        label=path.name,
        detail=" ".join(detail.split())[:220],
        target=str(path),
        exact=exact,
    )


def lexical(db, q, cfg):
    tokens = re.findall(r"[^\W_]+", q, re.UNICODE)[:16]
    if not tokens:
        return []
    expr = " AND ".join('"' + t + '"*' for t in tokens)
    hits = db.execute(
        """SELECT f.path,f.stamp,snippet(words,1,'','','…',12) FROM words
        JOIN files f ON f.id=words.rowid WHERE words MATCH ?
        ORDER BY bm25(words,8,1) LIMIT 50""",
        (expr,),
    )
    rows = []
    for raw, saved, snippet in hits:
        path = Path(raw)
        if visible(path, saved, cfg):
            rows.append(
                row(
                    path,
                    f"{path.parent}  {snippet}",
                    path.name.casefold() == q.casefold(),
                )
            )
    return sorted(rows, key=lambda r: not r["exact"])[:12]


def semantic(db, q, cfg, encoder):
    query = encoder.encode("query: " + q)
    cursor = db.execute("SELECT file,text,vector FROM passages ORDER BY file")
    # Adjacent passages belong to the same document. Reduce each document to its
    # best passage before keeping a bounded set of candidates across batches.
    candidates = []
    last = None
    best = None

    def retain(item):
        if len(candidates) < 100:
            heapq.heappush(candidates, item)
        else:
            heapq.heappushpop(candidates, item)

    while batch := cursor.fetchmany(512):
        matrix = encoder.np.frombuffer(
            b"".join(r[2] for r in batch), dtype="<f4"
        ).reshape(len(batch), -1)
        scores = encoder.np.einsum("ij,j->i", matrix, query)
        for (fid, chunk, _), score in zip(batch, scores):
            if last != fid:
                if best is not None:
                    retain(best)
                last, best = fid, None
            if best is None or score > best[0]:
                best = (float(score), fid, chunk)
    if best is not None:
        retain(best)
    rows = []
    for _, fid, chunk in sorted(candidates, reverse=True):
        record = db.execute(
            "SELECT path,stamp FROM files WHERE id=?", (fid,)
        ).fetchone()
        if not record:
            continue
        path = Path(record[0])
        if visible(path, record[1], cfg):
            rows.append(row(path, f"{path.parent}  {chunk}"))
            if len(rows) == 10:
                break
    return rows


def visible(path, saved, cfg):
    try:
        return (
            eligible(path, cfg)
            and os.access(path, os.R_OK)
            and stamp(path.stat()) == saved
        )
    except (OSError, RuntimeError):
        return False


def session_signature():
    # The CLI refreshes its access token during normal queries. Bind consent
    # to its production user identity, which survives token rotation.
    path = Path(os.getenv("XDG_CONFIG_HOME", HOME / ".config")) / "lycatra/auth.json"
    try:
        account = json.loads(path.read_text()).get("user", {}).get("id")
    except (FileNotFoundError, ValueError):
        return None
    return hashlib.sha256(str(account).encode()).hexdigest() if account else None


def session_watch():
    last = (session_signature(), settings().get("wiki_session"))
    while True:
        time.sleep(0.4)
        now = (session_signature(), settings().get("wiki_session"))
        if now != last:
            print("changed", flush=True)
            last = now


def wiki(q, cfg):
    if (
        cfg.get("offline_only")
        or not cfg.get("wiki")
        or not cfg.get("wiki_session")
        or cfg["wiki_session"] != session_signature()
        or not shutil.which("lycatra")
        or q.startswith("-")
    ):
        return []
    proc = subprocess.run(
        ["lycatra", "wiki", "s", q, "--limit", "10", "--json"],
        capture_output=True,
        timeout=8,
        env={**os.environ, "LYCATRA_CLI_CHANNEL": "stable"},
    )
    if (
        proc.returncode
        or cfg["wiki_session"] != session_signature()
        or not settings().get("wiki")
        or settings().get("offline_only")
    ):
        return []
    result = json.loads(proc.stdout)
    rows = [
        dict(
            kind="wiki",
            label=x["title"],
            detail="Wiki · " + x["space"]["name"],
            target=x["url"],
            exact=x["title"].casefold() == q.casefold(),
        )
        for x in result.get("data", [])
        if x.get("url")
    ]
    for x in result.get("files", []):
        url = (
            "https://lycatra.com/wiki/"
            + urllib.parse.quote(x["slug"], safe="")
            + "?"
            + urllib.parse.urlencode({"space": x["spaceId"]})
        )
        rows.append(
            dict(
                kind="wiki",
                label=x["name"],
                detail="Wiki · " + x["title"],
                target=url,
                exact=x["name"].casefold() == q.casefold(),
            )
        )
    return rows


def routed(q):
    prefix, sep, value = q.partition(":")
    return (
        (prefix, value.strip())
        if sep and prefix in ("files", "wiki")
        else ("all", q.strip())
    )


def search(stage, query):
    scope, q = routed(query)
    if (
        not q
        or len(q) > 500
        or (scope == "files" and stage == "wiki")
        or (scope == "wiki" and stage != "wiki")
    ):
        return []
    cfg = settings()
    if stage == "wiki":
        return wiki(q, cfg)
    if not cfg.get("local", True) or (
        stage == "semantic" and not cfg.get("semantic", True)
    ):
        return []
    if stage == "semantic":
        if not SOCKET.exists():
            return []
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(4)
            try:
                client.connect(str(SOCKET))
            except (ConnectionRefusedError, FileNotFoundError):
                return []
            client.sendall(json.dumps(q).encode() + b"\n")
            return json.loads(client.makefile("rb").readline(65536))
    file = database()
    if not file.exists():
        return []
    with contextlib.closing(
        sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)
    ) as db:
        return lexical(db, q, cfg)


def serve():
    import socketserver

    active = settings().get("model", models.DEFAULT)
    encoder = (
        Encoder(active)
        if (models.folder(MODEL, active) / "model.onnx").exists()
        else None
    )

    def maintain(content):
        with contextlib.closing(connect(active)) as db:
            while True:
                delay = 60
                try:
                    cfg = settings()
                    if cfg.get("paused") or not cfg.get("local", True):
                        time.sleep(3)
                        continue
                    if content:
                        budget = cfg.get("index_mib", 2048) * 1024 * 1024
                        used = sum(
                            p.stat().st_size
                            for p in STATE.glob(database(active).name + "*")
                            if p.exists()
                        )
                        if (
                            used < budget
                            and shutil.disk_usage(STATE).free > 512 * 1024 * 1024
                        ):
                            count = enrich(
                                db, cfg, encoder if cfg.get("semantic", True) else None
                            )
                            delay = 1 if count else 5
                    else:
                        inventory(db, cfg)
                except (OSError, sqlite3.Error, ValueError, RuntimeError) as e:
                    print(
                        f"Index pass failed: {type(e).__name__}",
                        file=sys.stderr,
                        flush=True,
                    )
                time.sleep(delay)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            request = json.loads(self.rfile.readline(8192))
            # Accept older clients during a release handover.
            stage = request.get("stage") if isinstance(request, dict) else "semantic"
            q = request.get("q") if isinstance(request, dict) else request
            hits = []
            if isinstance(q, str) and 0 < len(q) <= 500:
                scope, q = routed(q)
                with contextlib.closing(connect(active)) as db:
                    cfg = settings()
                    if scope != "wiki" and q and cfg.get("local", True):
                        if stage == "lexical":
                            hits = lexical(db, q, settings())
                        elif (
                            stage == "semantic"
                            and encoder
                            and cfg.get("semantic", True)
                        ):
                            hits = semantic(db, q, settings(), encoder)
            self.wfile.write(json.dumps(hits).encode() + b"\n")

    with contextlib.closing(connect(active)) as db:
        initialize(db)
    # A second manual service launch must not steal the live listening socket.
    lock = (STATE / "server.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    SOCKET.unlink(missing_ok=True)

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    with Server(str(SOCKET), Handler) as server:
        for content in (False, True):
            threading.Thread(target=maintain, args=(content,), daemon=True).start()
        server.serve_forever()


def open_result(kind, target):
    if kind == "wiki":
        u = urllib.parse.urlsplit(target)
        if (
            u.scheme != "https"
            or u.netloc != "lycatra.com"
            or not u.path.startswith("/wiki/")
        ):
            raise ValueError("Invalid wiki address")
        launcher = shutil.which("omarchy-launch-webapp-helium") or shutil.which(
            "omarchy-launch-webapp"
        )
        if not launcher:
            raise ValueError("Omarchy web-app launcher is missing")
        os.execl(launcher, launcher, target, "--new-window")
    path = Path(target)
    if (
        kind != "file"
        or not path.is_absolute()
        or not eligible(path, settings())
        or not os.access(path, os.R_OK)
    ):
        raise ValueError("File unavailable")
    os.execlp("xdg-open", "xdg-open", str(path))
