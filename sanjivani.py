#!/usr/bin/env python3
"""Sanjivani: the toolkit the Sanjivani agents use to examine, revive or bury
abandoned side projects. Standard library only, Python 3.9+, Windows, macOS
and Linux.

Every patient lives in the graveyard folder (default ~/sanjivani-graveyard,
or --graveyard / SANJIVANI_GRAVEYARD). The toolkit refuses to touch anything
outside it, and keeps its own notes in <graveyard>/.state/<patient>/, never
inside the patient.

    python sanjivani.py clone       <github-url> --requested-by NAME [--owner]
    python sanjivani.py autopsy     <patient>              # reads files, never runs them
    python sanjivani.py vitals      <patient> --label "..." [--sandbox docker|host|auto]
    python sanjivani.py certificate <patient>              # REVIVAL.md from the vitals history
    python sanjivani.py tombstone   <patient> [--input funeral.json]  # default: the notes folder's
    python sanjivani.py where       <patient>              # full path of the patient's notes folder
    python sanjivani.py fixture     [dest]                 # a dead demo patient with real history
"""
from __future__ import annotations

import argparse
import datetime as dt
import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

__version__ = "0.2.0"

# ---------------------------------------------------------------- limits
MAX_FILES = 20_000             # files walked per patient
MAX_DEPTH = 40                 # folder levels walked per patient
MAX_READ = 400_000             # bytes read per file for analysis
MAX_SYNTAX_BYTES = 2_000_000   # bytes compiled per file by the syntax check
MAX_REPO_MB = 50               # GitHub-reported size a clone may have
MAX_DONATIONS = 3
MAX_ORGAN_BYTES = 1_000_000
TAIL_BYTES = 8_000             # output kept per check
PER_PERSON = 3                 # patients per person per 24 hours (the owner is exempt)
DOCKER_IMAGES = {"python": "python:3.12-slim", "node": "node:20-slim",
                 "go": "golang:1.22", "rust": "rust:1-slim"}
# Environment variables a patient's checks may see when they run on the host.
SAFE_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
            "OS", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist",
             "build", "target", ".next", ".idea", ".vscode", "vendor",
             ".mypy_cache", ".pytest_cache", ".tox", "coverage"}
CODE_EXT = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
            ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
            ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#",
            ".swift": "Swift", ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte",
            ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".sh": "Shell", ".lua": "Lua"}
MANIFESTS = {"package.json": "node", "requirements.txt": "python", "pyproject.toml": "python",
             "setup.py": "python", "Pipfile": "python", "Cargo.toml": "rust", "go.mod": "go",
             "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
             "Gemfile": "ruby", "composer.json": "php", "pubspec.yaml": "dart"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
             "Pipfile.lock", "uv.lock", "Cargo.lock", "go.sum", "Gemfile.lock",
             "composer.lock", "pubspec.lock"}
CI_PATHS = [".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml",
            ".travis.yml", "Jenkinsfile"]

# Things that were alive when the project was written and are not any more:
# (regex, file it applies to (extension or exact name), what happened)
DECEASED = [
    (r"from collections import .*\b(Mapping|MutableMapping|Sequence|Iterable|Callable)\b",
     ".py", "collections ABC aliases were removed in Python 3.10 (use collections.abc)"),
    (r"\bcollections\.(Mapping|MutableMapping|Sequence|Iterable|Callable)\b",
     ".py", "collections ABC aliases were removed in Python 3.10 (use collections.abc)"),
    (r"^\s*import imp\b|^\s*from imp import", ".py", "the imp module was removed in Python 3.12"),
    (r"^\s*(import|from) distutils", ".py", "distutils was removed in Python 3.12"),
    (r"@asyncio\.coroutine", ".py", "@asyncio.coroutine was removed in Python 3.11"),
    (r"\bdatetime\.utcnow\(\)", ".py", "datetime.utcnow() is deprecated since Python 3.12"),
    (r"^\s*(import|from) (nose|pkg_resources)\b", ".py", "nose / pkg_resources are dead or deprecated"),
    (r"from flask import .*\b(escape|Markup)\b", ".py", "flask.escape / flask.Markup were removed in Flask 3"),
    (r"\"(request|node-sass|tslint|left-pad|gulp-util|babel-eslint)\"\s*:", "package.json",
     "deprecated npm package"),
    (r"\"react-scripts\"\s*:", "package.json", "create-react-app (react-scripts) is no longer maintained"),
    (r"\"webpack\"\s*:\s*\"\^?[1-4]\.", "package.json", "webpack 4 or older"),
    (r"\"node\"\s*:\s*\"[<>=~^ ]*(8|10|12|14)(\.|\")", "package.json", "pinned to an end-of-life Node.js"),
    (r"^\s*[Dd]jango==(1|2)\.", "requirements.txt", "Django 1.x/2.x is end-of-life"),
    (r"^\s*tensorflow==1\.", "requirements.txt", "TensorFlow 1.x"),
]
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)")
ABANDON_WORDS = re.compile(r"\b(wip|tmp|temp|todo|will finish|later|broken|half|draft|hack|almost)\b", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
                       r"|\.(test|spec)\.[jt]sx?$")
GITHUB_URL = re.compile(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)"
                        r"(?:\.git)?/?")
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")
# git settings for every call on a patient: no credential prompts or helpers, no
# file:// transport, no symlinks, no fsmonitor hook, no external diff drivers.
GIT_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.symlinks=false", "-c", "protocol.file.allow=never",
            "-c", "core.hooksPath=" + os.devnull]
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GCM_INTERACTIVE": "never"}


def exe(name: str) -> str:
    """The full path of a program on PATH, never one in the current folder.
    Windows (and shutil.which before Python 3.12) look in the current folder
    first, where a patient, or code run inside its container, could leave a
    fake git.exe or docker.exe. Raises FileNotFoundError when it is missing."""
    if os.path.isabs(name):
        return name
    if os.name == "nt":
        pathext = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";") if e]
        exts = [""] if os.path.splitext(name)[1].lower() in pathext else pathext
    else:
        exts = [""]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d or not os.path.isabs(d):
            continue  # an empty or relative PATH entry means the current folder
        for e in exts:
            cand = os.path.join(d, name + e)
            if os.path.isfile(cand) and (os.name == "nt" or os.access(cand, os.X_OK)):
                return cand
    raise FileNotFoundError(errno.ENOENT, "not found on PATH", name)


class Refused(Exception):
    """Something the toolkit will not do. Printed as one line, exit code 2."""


# ---------------------------------------------------------------- text from patients is data
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍‎‏‪‫‬‭‮"
                                     "⁠⁦⁧⁨⁩﻿"), None)


def clean(text: object, limit: int = 120) -> str:
    """Make text that came from a patient safe to post in chat: one line, no
    control or bidi characters, no backticks, no working @mentions, no masked
    links, and short."""
    s = str(text).translate(_INVISIBLE)
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    s = s.replace("`", "'").replace("@", "@​").replace("](", "] (").replace("<", "‹").replace(">", "›")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def code(text: object, limit: int = 120) -> str:
    return f"`{clean(text, limit)}`"


# ---------------------------------------------------------------- the graveyard
def same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def inside(child: Path, root: Path) -> bool:
    """True when child is strictly inside root (both already resolved)."""
    c, r = os.path.normcase(str(child)), os.path.normcase(str(root))
    try:
        return c != r and os.path.commonpath([c, r]) == r
    except ValueError:  # different drives on Windows
        return False


def graveyard_root(arg: str | None) -> Path:
    raw = arg or os.environ.get("SANJIVANI_GRAVEYARD") or "~/sanjivani-graveyard"
    root = Path(os.path.expanduser(raw)).resolve()
    if root.exists() and not root.is_dir():
        raise Refused("the graveyard is not a folder")
    home = Path.home().resolve()
    if root.parent == root or same(root, home) or inside(home, root):
        raise Refused("the graveyard cannot be your home folder, a drive or a folder that contains them; "
                      "use a folder of its own such as ~/sanjivani-graveyard")
    root.mkdir(parents=True, exist_ok=True)
    return root


def patient_path(arg: str | Path, root: Path, must_exist: bool = True) -> Path:
    """Resolve a patient folder and refuse anything that is not a direct child
    of the graveyard (no other folders, no symlinked patients, no state)."""
    p = Path(os.path.expanduser(str(arg)))
    if not p.is_absolute():
        p = (root / p) if (root / p).exists() or not p.exists() else p
    rp = p.resolve()
    if not inside(rp, root) or not same(rp.parent, root):
        raise Refused(f"{clean(arg)} is not a patient: patients must be folders directly inside the graveyard")
    if rp.name.startswith((".", "_")):
        raise Refused(f"{clean(rp.name)} is reserved for the toolkit, not a patient")
    if must_exist and not rp.is_dir():
        raise Refused(f"no patient folder {clean(rp.name)} in the graveyard")
    return rp


def state_dir(root: Path, name: str) -> Path:
    base = root / ".state"
    d = base / name
    for part in (base, d):
        if part.is_symlink():
            raise Refused(f"{part.name} is a symlink; refusing to use it")
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default):
    """Read a toolkit note. A missing, damaged or wrongly shaped note gives the
    default and a warning, never a crash."""
    if not path.exists():
        return default
    if path.is_symlink():
        raise Refused(f"{path.name} is a symlink; refusing to read it")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print(f"sanjivani: warning: {path.name} was damaged and has been ignored", file=sys.stderr)
        return default
    if not isinstance(data, type(default)):
        print(f"sanjivani: warning: {path.name} has the wrong shape and has been ignored", file=sys.stderr)
        return default
    return data


def save_json(path: Path, data) -> None:
    safe_write(path, json.dumps(data, indent=2), within=path.parent, ledger=None, force=True)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_write(target: Path, text: str, within: Path, ledger: dict | None, force: bool) -> None:
    """Write a file without following symlinks, only inside `within`, and
    without overwriting a file the toolkit did not write itself (unless force)."""
    data = text.encode("utf-8")
    check_writable(target, within, ledger, force)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(target), flags, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if ledger is not None:
        ledger[target.name] = sha(data)


def check_writable(target: Path, within: Path, ledger: dict | None, force: bool) -> None:
    parent = target.parent.resolve()
    if not (same(parent, within.resolve()) or inside(parent, within.resolve())):
        raise Refused(f"{target.name} would be written outside {within.name}")
    if target.is_symlink():
        raise Refused(f"{target.name} is a symlink; refusing to write through it")
    key = target.name
    if target.exists():
        if not target.is_file():
            raise Refused(f"{target.name} exists and is not a file")
        mine = ledger is not None and ledger.get(key) == sha(target.read_bytes())
        if ledger is not None and not mine and not force:
            raise Refused(f"{target.name} already exists and was not written by Sanjivani. "
                          "Ask the owner, then rerun with --force")


# ---------------------------------------------------------------- helpers
def git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        out = subprocess.run([exe("git"), *GIT_SAFE, "-C", str(repo), *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def list_files(repo: Path, limit: int = MAX_FILES) -> tuple[list[Path], bool]:
    """Regular files of a patient, never following or returning symlinks."""
    found: list[Path] = []
    base = len(repo.parts)
    for root, dirs, files in os.walk(repo):  # os.walk does not follow symlinked dirs
        if len(Path(root).parts) - base >= MAX_DEPTH:
            dirs[:] = []  # very deep trees can exhaust os.walk's recursion on older Pythons
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github")
                   and not os.path.islink(os.path.join(root, d))]
        for f in files:
            p = Path(root) / f
            if p.is_symlink() or not p.is_file():
                continue
            found.append(p)
            if len(found) >= limit:
                return found, True
    return found, False


def read(path: Path | None, limit: int = MAX_READ) -> str:
    try:
        if path is None or path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sparkline(values: list[int]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not values or max(values) == 0:
        return "▁" * len(values)
    top = max(values)
    return "".join(bars[0] if v == 0 else bars[min(7, 1 + v * 6 // top)] for v in values)


def human_age(days: int) -> str:
    days = max(0, days)
    if days < 60:
        return f"{days} days"
    if days < 730:
        return f"{days // 30} months"
    return f"{days / 365:.1f} years"


def run_capped(argv: list[str], cwd: Path, env: dict, timeout: int,
               on_timeout=None) -> tuple[int | None, bool, str]:
    """Run a command in its own process group with output in a temp file.
    On timeout the whole group is killed. Returns (exit code, timed out, tail)."""
    with tempfile.TemporaryFile() as out:
        kw: dict = {"cwd": str(cwd), "env": env, "stdin": subprocess.DEVNULL, "stdout": out,
                    "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            p = subprocess.Popen([exe(argv[0]), *argv[1:]], **kw)
        except FileNotFoundError:
            return None, False, f"tool not found: {argv[0]}"
        except OSError as e:
            return None, False, f"could not start {argv[0]}: {e}"
        timed_out = False
        try:
            rc: int | None = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out, rc = True, None
            if on_timeout:
                on_timeout()
        _kill_tree(p)
        out.seek(0, os.SEEK_END)
        size = out.tell()
        out.seek(max(0, size - TAIL_BYTES))
        tail = out.read().decode("utf-8", "replace")
    return rc, timed_out, tail


def _kill_tree(p: subprocess.Popen) -> None:
    if os.name == "nt":
        if p.poll() is None:
            try:
                subprocess.run([exe("taskkill"), "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)  # also reaps leftovers after a normal exit
        except (ProcessLookupError, PermissionError):
            pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()


# ---------------------------------------------------------------- clone
def parse_github(url: str) -> tuple[str, str]:
    m = GITHUB_URL.fullmatch(url.strip())
    if not m:
        raise Refused("only GitHub links shaped like https://github.com/<owner>/<repo> are accepted "
                      "(no local paths, file://, ssh or other hosts)")
    owner, name = m.group(1), m.group(2)
    if name.startswith(".") or name in {".", ".."}:
        raise Refused("that repository name is not accepted")
    return owner, name


def github_info(owner: str, name: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{name}",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "sanjivani-toolkit"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Refused(f"{owner}/{name} does not exist or is not public")
        if e.code in (403, 429):
            raise Refused("GitHub's API limit was hit; try again in an hour (the owner can use --skip-size-check)")
        raise Refused(f"GitHub answered {e.code} for {owner}/{name}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Refused(f"could not check {owner}/{name} on GitHub: {clean(e, 80)}")
    if not isinstance(data, dict):
        raise Refused("GitHub gave an unexpected answer")
    return data


def slug(owner: str, name: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "-", f"{owner}-{name}".lower()).strip(".-_") or "patient"


def clone(url: str, root: Path, requested_by: str, owner_request: bool, allow_private: bool,
          max_mb: int, per_person: int, skip_size_check: bool, timeout: int = 600) -> Path:
    owner, name = parse_github(url)
    who = clean(requested_by, 60).lower()
    if not who:
        raise Refused("say who asked for this patient with --requested-by")
    if allow_private and not owner_request:
        raise Refused("--allow-private is only for the room owner's own requests (--owner)")
    if skip_size_check and not owner_request:
        raise Refused("--skip-size-check is only for the room owner's own requests (--owner)")
    requests_file = state_dir(root, "_requests") / "requests.json"
    requests = load_json(requests_file, [])
    now = time.time()
    if not owner_request:
        recent = [r for r in requests if isinstance(r, dict) and r.get("by") == who
                  and now - float(r.get("at", 0)) < 86_400]
        if len(recent) >= per_person:
            raise Refused(f"{who} already brought {len(recent)} patients in the last 24 hours (limit {per_person})")
    if not allow_private and not skip_size_check:
        info = github_info(owner, name)
        if info.get("private"):
            raise Refused(f"{owner}/{name} is private")
        size_mb = int(info.get("size", 0)) / 1024
        if size_mb > max_mb:
            raise Refused(f"{owner}/{name} is {size_mb:.0f} MB on GitHub; the limit is {max_mb} MB")
    dest = root / slug(owner, name)
    if dest.exists():
        raise Refused(f"{dest.name} is already in the graveyard")
    env = dict(os.environ, **GIT_ENV)
    cmd = ["git", *GIT_SAFE]
    if not allow_private:  # never let a request use the owner's saved GitHub login
        cmd += ["-c", "credential.helper="]
    cmd += ["clone", "--quiet", "--no-recurse-submodules", "--config", "core.symlinks=false",
            "--", f"https://github.com/{owner}/{name}.git", str(dest)]
    rc, timed_out, tail = run_capped(cmd, root, env, timeout)
    if rc != 0:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        why = "timed out" if timed_out else clean(tail.strip().splitlines()[-1] if tail.strip() else "failed", 160)
        raise Refused(f"git clone {why}")
    requests.append({"by": who, "at": now, "patient": dest.name, "owner": owner_request})
    save_json(requests_file, requests[-500:])
    save_json(state_dir(root, dest.name) / "origin.json",
              {"url": f"https://github.com/{owner}/{name}", "requested_by": who, "owner_request": owner_request,
               "cloned_at": dt.datetime.now().isoformat(timespec="seconds")})
    return dest


# ---------------------------------------------------------------- autopsy
def examine(repo: Path) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    rep: dict = {"name": repo.name, "examined_at": now.isoformat(timespec="seconds")}

    # --- history: dates from the earliest and latest commit, whatever order git prints
    commits = []
    for line in git(repo, "log", "--max-count=5000", "--format=%H%x1f%aI%x1f%an%x1f%s").splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) == 4:
            try:
                when = dt.datetime.fromisoformat(parts[1])
            except ValueError:
                continue
            commits.append({"date": when, "author": parts[2], "msg": parts[3]})
    hist: dict = {"commits": len(commits)}
    if commits:
        dates = [c["date"] for c in commits]
        first, last = min(dates), max(dates)
        months = Counter((d.year, d.month) for d in dates)
        series = []
        y, m = last.year, last.month
        for _ in range(24):  # the 24 months up to the last commit, oldest first
            if (y, m) < (first.year, first.month):
                break
            series.append(months.get((y, m), 0))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        series.reverse()
        hist.update({
            "born": first.date().isoformat(),
            "last_seen": last.date().isoformat(),
            "days_since_last_commit": max(0, (now - last).days),
            "lifespan_days": (last - first).days,
            "last_words": commits[0]["msg"],
            "authors": sorted({c["author"] for c in commits})[:20],
            "monthly": series,
            "activity": sparkline(series),
            "final_commits": [f"{c['date'].date()} {c['msg']}" for c in commits[:5]],
        })
    rep["history"] = hist

    # --- body
    loc: Counter = Counter()
    todos, deceased = [], []
    stacks, manifests, lockfiles = set(), [], []
    has_tests = False
    files, truncated = list_files(repo)
    for p in files:
        rel = p.relative_to(repo).as_posix()
        name, ext = p.name, p.suffix.lower()
        if name in MANIFESTS and rel.count("/") <= 1:
            stacks.add(MANIFESTS[name])
            manifests.append(rel)
        if name in LOCKFILES:
            lockfiles.append(rel)
        if TEST_PATH.search(rel.lower()):
            has_tests = True
        text = None
        if ext in CODE_EXT:
            text = read(p)
            lines = text.splitlines()
            loc[CODE_EXT[ext]] += sum(1 for ln in lines if ln.strip())
            for i, ln in enumerate(lines, 1):
                mt = TODO_RE.search(ln)
                if mt and len(todos) < 200:
                    todos.append({"where": f"{rel}:{i}", "tag": mt.group(1), "text": mt.group(2).strip()[:100]})
        for rx, scope, why in DECEASED:
            if ext == scope or name == scope:
                text = text if text is not None else read(p)
                for i, ln in enumerate(text.splitlines(), 1):
                    if re.search(rx, ln):
                        deceased.append({"where": f"{rel}:{i}", "why": why})
                        break
    readme = next((repo / n for n in ("README.md", "README.rst", "README.txt", "README", "readme.md")
                   if (repo / n).is_file()), None)
    rep["body"] = {
        "files": len(files),
        "files_truncated": truncated,
        "lines_of_code": sum(loc.values()),
        "languages": dict(loc.most_common()),
        "stacks": sorted(stacks),
        "manifests": manifests[:20],
        "lockfile": bool(lockfiles),
        "tests": has_tests,
        "ci": any((repo / c).exists() for c in CI_PATHS),
        "readme_words": len(read(readme).split()),
        "license": any((repo / n).is_file() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")),
        "todos": len(todos),
        "todo_samples": todos[:6],
        "deceased_apis": deceased[:50],
        "uncommitted_changes": bool(git(repo, "status", "--porcelain", "--untracked-files=no")),
    }
    rep["causes_of_death"] = diagnose(rep)
    rep["revivability"], rep["verdict"] = prognosis(rep)
    return rep


def diagnose(rep: dict) -> list[dict]:
    h, b = rep["history"], rep["body"]
    causes = []

    def add(cause: str, evidence: str, weight: int):
        causes.append({"cause": cause, "evidence": evidence, "weight": weight})

    last = h.get("last_words", "")
    if last and ABANDON_WORDS.search(last):
        add("Abandoned mid-feature", f"last commit was {code(last, 80)}", 3)
    if b["uncommitted_changes"]:
        add("Left on the operating table", "uncommitted changes in the working tree", 2)
    if b["deceased_apis"]:
        add("Dependency rot", f'{len(b["deceased_apis"])} use(s) of APIs that no longer exist', 3)
    if b["stacks"] and not b["lockfile"]:
        add("No lockfile", "an install today resolves to versions it never saw", 1)
    if not b["tests"]:
        add("No tests", "nothing tells you if a change breaks it, so nobody changed it", 2)
    if b["todos"] >= 5:
        add("Scope creep", f'{b["todos"]} TODO/FIXME markers left behind', 2)
    if b["readme_words"] < 40:
        add("Never introduced to the world", f'README has {b["readme_words"]} words', 1)
    monthly = h.get("monthly") or []
    if len(monthly) >= 3 and monthly[0] == max(monthly) and sum(monthly[-2:]) <= 2:
        add("Hype curve", "most of the work happened in the first month, then it faded", 1)
    if h.get("days_since_last_commit", 0) > 365 and not causes:
        add("Natural causes", f'untouched for {human_age(h["days_since_last_commit"])}', 1)
    return sorted(causes, key=lambda c: -c["weight"])


def prognosis(rep: dict) -> tuple[int, str]:
    h, b = rep["history"], rep["body"]
    score = 70
    loc = b["lines_of_code"]
    score -= 15 if loc > 20_000 else 8 if loc > 5_000 else 0
    score += 10 if b["tests"] else -5
    score += 5 if b["lockfile"] else 0
    score -= min(20, 4 * len(b["deceased_apis"]))
    score -= min(15, b["todos"] // 3)
    idle = h.get("days_since_last_commit", 0)
    score -= 15 if idle > 4 * 365 else 8 if idle > 2 * 365 else 0
    score += 5 if b["readme_words"] >= 100 else 0
    score = max(0, min(100, score))
    verdict = "REVIVE" if score >= 60 else "SCOPE-DOWN" if score >= 35 else "BURY"
    return score, verdict


def autopsy_markdown(r: dict) -> str:
    """Everything that came from the patient goes through clean()/code()."""
    h, b = r["history"], r["body"]
    out = [f"## 🩻 Autopsy: {code(r['name'], 60)}", ""]
    if h.get("commits"):
        authors = ", ".join(clean(a, 30) for a in h["authors"][:3])
        out += [f"- **Born** {h['born']} · **last seen** {h['last_seen']} "
                f"({human_age(h['days_since_last_commit'])} ago) · {h['commits']} commits by {authors}",
                f"- **Activity** `{h['activity']}`",
                f"- **Last words** {code(h['last_words'], 100)}"]
    else:
        out.append("- No git history found")
    langs = ", ".join(f"{k} {v}" for k, v in list(b["languages"].items())[:4]) or "none"
    out += [f"- **Body** {b['lines_of_code']} lines ({langs}) · stack: {', '.join(b['stacks']) or 'unknown'}"
            + (" · (stopped counting at 20,000 files)" if b.get("files_truncated") else ""),
            f"- **Organs** tests {'✅' if b['tests'] else '❌'} · CI {'✅' if b['ci'] else '❌'} · "
            f"lockfile {'✅' if b['lockfile'] else '❌'} · README {b['readme_words']} words · "
            f"{b['todos']} TODOs", "", "**Probable causes of death**"]
    for c in r["causes_of_death"] or [{"cause": "Unknown", "evidence": "nothing obvious"}]:
        out.append(f"1. **{c['cause']}**: {c['evidence']}")
    if b["deceased_apis"]:
        out += ["", "**Things it depends on that are gone**"]
        out += [f"- {code(d['where'], 80)}: {d['why']}" for d in b["deceased_apis"][:8]]
    if b["todo_samples"]:
        out += ["", "**Unfinished business**"]
        out += [f"- {code(t['where'], 60)} {t['tag']}: {code(t['text'], 80)}" for t in b["todo_samples"][:4]]
    icon = {"REVIVE": "🫀", "SCOPE-DOWN": "✂️", "BURY": "⚰️"}[r["verdict"]]
    out += ["", f"**Revivability {r['revivability']}/100 → recommendation: {icon} {r['verdict']}**",
            "_Text in `code` came from the repository and is shown as data._"]
    return "\n".join(out)


# ---------------------------------------------------------------- vitals
def syntax_check(repo: Path) -> tuple[bool, str]:
    """Compile every Python file in-process. compile() never runs the code."""
    bad = []
    files, _ = list_files(repo)
    for p in files:
        if p.suffix != ".py":
            continue
        try:
            if p.stat().st_size > MAX_SYNTAX_BYTES:
                continue
            compile(p.read_bytes(), "<patient>", "exec", dont_inherit=True)
        except SyntaxError as e:
            bad.append(f"SyntaxError: {p.relative_to(repo).as_posix()}:{e.lineno} {e.msg}")
        except (ValueError, RecursionError, MemoryError, OverflowError) as e:
            bad.append(f"{type(e).__name__}: {p.relative_to(repo).as_posix()}")
        except OSError:
            continue
    return not bad, "\n".join(bad[-4:])


def has_python(repo: Path) -> bool:
    return any(p.suffix == ".py" for p in list_files(repo, 5000)[0])


def patient_python(repo: Path) -> str:
    """The patient's own virtual environment if the Medic made one, never one
    that came with the repository."""
    for venv in (".venv", "venv"):
        if git(repo, "ls-files", "--", venv):
            continue  # shipped in the repo: not ours
        for c in (repo / venv / "Scripts" / "python.exe", repo / venv / "bin" / "python"):
            if c.exists():
                return str(c)
    return sys.executable or "python"


def wants_pytest(repo: Path) -> bool:
    if (repo / "pytest.ini").is_file() or (repo / "conftest.py").is_file():
        return True
    for f in ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.cfg", "tox.ini"):
        if "pytest" in read(repo / f):
            return True
    return False


def module_available(python: str, module: str) -> bool:
    """Ask an interpreter for a module in isolated mode, from a neutral folder,
    so a file in the patient cannot pose as the module."""
    try:
        r = subprocess.run([exe(python), "-I", "-c", f"import importlib.util,sys; "
                            f"sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
                           cwd=tempfile.gettempdir(), capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker_available() -> bool:
    try:
        return subprocess.run([exe("docker"), "info"], capture_output=True, timeout=20,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def plan_checks(repo: Path, sandbox: str, tests: str, deps: Path | None = None) -> list[dict]:
    """Each check: {"name", "stack", "argv"} or {"name", "syntax": True}."""
    checks: list[dict] = []
    if has_python(repo):
        checks.append({"name": "python: compiles", "syntax": True})
        start = "tests" if (repo / "tests").is_dir() else "."
        py = "python" if sandbox == "docker" else patient_python(repo)
        if sandbox == "docker":
            have_pytest = deps is not None and (deps / "pytest").is_dir()
        else:
            have_pytest = module_available(py, "pytest")
        use_pytest = tests == "pytest" or (tests == "auto" and wants_pytest(repo) and have_pytest)
        argv = ([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"] if use_pytest else
                [py, "-m", "unittest", "discover", "-t", ".", "-s", start])
        checks.append({"name": "python: tests", "stack": "python", "argv": argv})
    pkg = repo / "package.json"
    if pkg.is_file():
        npm = "npm"
        try:
            scripts = json.loads(read(pkg) or "{}").get("scripts", {})
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        if not isinstance(scripts, dict):
            scripts = {}
        if not (repo / "node_modules").is_dir():
            checks.append({"name": "node: dependencies installed", "missing": "node_modules missing: run "
                           "npm install --ignore-scripts"})
        for s in ("build", "test"):
            if isinstance(scripts.get(s), str) and "no test specified" not in scripts[s]:
                checks.append({"name": f"node: npm run {s}", "stack": "node", "argv": [npm, "run", s, "--silent"]})
    if (repo / "go.mod").is_file():
        checks += [{"name": "go: build", "stack": "go", "argv": ["go", "build", "./..."]},
                   {"name": "go: test", "stack": "go", "argv": ["go", "test", "./..."]}]
    if (repo / "Cargo.toml").is_file():
        checks += [{"name": "rust: build", "stack": "rust", "argv": ["cargo", "build", "-q"]},
                   {"name": "rust: test", "stack": "rust", "argv": ["cargo", "test", "-q"]}]
    return checks


def clean_env(tmp: str) -> dict:
    """Only the variables a build needs; HOME points at an empty temp folder."""
    env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV}
    env.update({"HOME": tmp, "USERPROFILE": tmp, "APPDATA": tmp, "LOCALAPPDATA": tmp,
                "PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "NO_COLOR": "1", **GIT_ENV})
    return env


def docker_argv(repo: Path, image: str, argv: list[str], name: str, deps: Path | None = None,
                network: str = "none") -> list[str]:
    """A throwaway container that sees only the patient (and its installed
    dependencies), with no network, no capabilities and capped resources."""
    for p in (repo, deps):
        if p is not None and "," in str(p):
            raise Refused("the patient's path contains a comma, which Docker's --mount cannot take")
    mounts = ["--mount", f"type=bind,source={repo},target=/patient"]
    git_dir = repo / ".git"
    if git_dir.exists() and not git_dir.is_symlink():
        # Read-only inside the container: code in there must not plant a git hook
        # or config that would run later, outside Docker, when git is used here.
        mounts += ["--mount", f"type=bind,source={git_dir},target=/patient/.git,readonly"]
    if deps is not None:
        mounts += ["--mount", f"type=bind,source={deps},target=/deps", "-e", "PYTHONPATH=/deps"]
    # On Linux and macOS, run as you, so files it creates in the patient are not owned by root.
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    return ["docker", "run", "--rm", "--name", name, "--network", network, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "2g", "--cpus", "2",
            "--tmpfs", "/tmp", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "CI=1",
            *user, *mounts, "-w", "/patient", image, *argv]


def install_steps(repo: Path, allow_source: bool = False) -> list[dict]:
    """Dependency installs for --install, run inside Docker only. They are the
    only steps that get network, so by default none of them runs the patient's
    or its packages' code: pip takes wheels only and npm skips install scripts.
    Building from source (a setup.py, an sdist) needs --allow-source-builds."""
    pip = ["pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--target", "/deps"]
    wheels = [] if allow_source else ["--only-binary=:all:"]
    steps: list[dict] = []
    if (repo / "requirements.txt").is_file():
        steps.append({"stack": "python", "name": "python: install requirements",
                      "argv": [*pip, *wheels, "-r", "requirements.txt"]})
    elif (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
        if allow_source:  # pip builds a local project from source even with --only-binary
            steps.append({"stack": "python", "name": "python: install project", "argv": [*pip, "."]})
        else:
            steps.append({"stack": "python", "name": "python: install project",
                          "skip": "skipped: installing it runs the project's own setup code with network on "
                                  "(the owner can allow it with --allow-source-builds)"})
    if (repo / "package.json").is_file():
        lock = (repo / "package-lock.json").is_file()
        steps.append({"stack": "node", "name": "node: install packages",
                      "argv": ["npm", "ci" if lock else "install", "--ignore-scripts", "--no-audit", "--no-fund"]})
    return steps


def run_vitals(repo: Path, root: Path, label: str, extra: list[str], timeout: int,
               sandbox: str, tests: str, image: str | None, install: bool = False,
               allow_source: bool = False) -> dict:
    mode = sandbox
    if sandbox == "auto":
        mode = "docker" if docker_available() else "host"
    elif sandbox == "docker" and not docker_available():
        raise Refused("Docker is not running. Start Docker Desktop, or ask the owner before using --sandbox host")
    if install and mode != "docker":
        raise Refused("--install only runs inside Docker; on the host install into the patient's .venv yourself")
    deps = None
    if mode == "docker":
        deps = state_dir(root, repo.name) / "deps"
        if deps.is_symlink():
            raise Refused("the deps folder is a symlink")
        deps.mkdir(exist_ok=True)
    setup: list[dict] = []
    if install:  # the only steps that get network, still inside the container
        setup = [dict(s, network="bridge", setup=True) for s in install_steps(repo, allow_source)]
        if not setup:
            raise Refused("nothing to install: no requirements.txt, pyproject.toml, setup.py or package.json")
    elif allow_source:
        raise Refused("--allow-source-builds only goes with --install")
    tmp_home = tempfile.mkdtemp(prefix="sanjivani-home-")
    env = clean_env(tmp_home)
    results = []
    try:
        results = _run_checks(repo, setup, mode, env, timeout, image, deps)
        # planned after the install, so they see the node_modules and pytest it just brought
        checks = plan_checks(repo, mode, tests, deps)
        checks += [{"name": f"custom: {clean(c, 60)}", "stack": None,
                    "argv": shlex.split(c, posix=os.name != "nt")} for c in extra]
        results += _run_checks(repo, checks, mode, env, timeout, image, deps)
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)
    counted = [r for r in results if r["status"] != "none" and not r.get("setup")]
    sd = state_dir(root, repo.name)
    beat = {"label": clean(label, 40), "at": dt.datetime.now().isoformat(timespec="seconds"),
            "sandbox": mode, "commit": git(repo, "rev-parse", "--short=12", "HEAD"),
            "passed": sum(r["status"] == "pass" for r in counted), "total": len(counted),
            "results": [{"check": r["check"], "status": r["status"], "setup": bool(r.get("setup")),
                         "tail": tail_lines(r["tail"])} for r in results]}
    history = [b for b in load_json(sd / "vitals.json", []) if isinstance(b, dict)]
    save_json(sd / "vitals.json", (history + [beat])[-200:])
    return beat


def _run_checks(repo: Path, checks: list[dict], mode: str, env: dict, timeout: int,
                image: str | None, deps: Path | None = None) -> list[dict]:
    results = []
    for c in checks:
        if c.get("syntax"):
            ok, tail = syntax_check(repo)
            results.append({"check": c["name"], "status": "pass" if ok else "fail", "tail": tail})
            continue
        if "missing" in c:
            results.append({"check": c["name"], "status": "fail", "tail": c["missing"]})
            continue
        if "skip" in c:
            results.append({"check": c["name"], "status": "skipped", "tail": c["skip"],
                            "setup": c.get("setup", False)})
            continue
        argv, kill, run_env = c["argv"], None, env
        if mode == "docker":
            run_env = dict(os.environ)  # the docker CLI needs its own config; the container gets nothing
            img = image or DOCKER_IMAGES.get(c.get("stack") or "", DOCKER_IMAGES["python"])
            cname = f"sanjivani-{uuid.uuid4().hex[:12]}"
            argv = docker_argv(repo, img, argv, cname, deps, c.get("network", "none"))

            def stop_container(cname=cname):
                try:
                    subprocess.run([exe("docker"), "kill", cname], capture_output=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            kill = stop_container
        rc, timed_out, tail = run_capped(argv, repo, run_env, timeout, on_timeout=kill)
        no_tests = rc == 5 or (rc == 0 and re.search(r"\bRan 0 tests?\b", tail) is not None)
        if timed_out:
            status, tail = "fail", f"timed out after {timeout}s (process tree killed)"
        elif no_tests and "tests" in c["name"]:
            status = "none"
        else:
            status = "pass" if rc == 0 else "fail"
        results.append({"check": c["name"], "status": status, "tail": tail, "setup": c.get("setup", False)})
    return results


def tail_lines(text: str) -> list[str]:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    key = [ln for ln in lines if re.search(r"Error|FAILED|failed|error:|Traceback|assert|not found|timed out", ln)]
    return [clean(ln, 160) for ln in (key or lines)[-4:]]


def pulse_word(passed: int, total: int) -> str:
    return "no signal" if total == 0 else "flatline" if passed == 0 else "weak" if passed < total else "strong"


def ecg(passed: int, total: int) -> str:
    return {"no signal": "────────────── no signal", "flatline": "────────────────────── flatline",
            "weak": "───╱╲──────╱╲───── weak pulse",
            "strong": "─╱╲_╱╲──╱╲_╱╲──╱╲_╱╲─ strong pulse"}[pulse_word(passed, total)]


def vitals_markdown(beat: dict, history: list[dict]) -> str:
    where = ("🔒 ran in Docker with no network" if beat.get("sandbox") == "docker"
             else "⚠️ ran on this computer with a clean environment (no Docker)")
    out = [f"## 🫀 Vitals ({clean(beat['label'], 40)}): {beat['passed']}/{beat['total']} checks passing",
           f"`{ecg(beat['passed'], beat['total'])}`", where, ""]
    icons = {"pass": "✅", "fail": "❌", "none": "⚪", "skipped": "⏭️"}
    for r in beat["results"]:
        note = " (no tests found)" if r["status"] == "none" else ""
        icon = "🔧" if r.get("setup") and r["status"] == "pass" else icons.get(r["status"], "❔")
        out.append(f"- {icon} {r['check']}{note}")
        if r["status"] in ("fail", "skipped") and r["tail"]:
            out.append("  " + code(" | ".join(r["tail"][-2:]), 220))
    if len(history) > 1:
        trend = " → ".join(f"{h.get('passed', 0)}/{h.get('total', 0)}" for h in history[-6:])
        out += ["", f"Trend: {trend} (first reading: {clean(history[0].get('label', '?'), 40)}, "
                    f"{str(history[0].get('at', '?'))[:10]})"]
    return "\n".join(out)


# ---------------------------------------------------------------- certificate / tombstone
def certificate(repo: Path, root: Path, force: bool) -> Path:
    sd = state_dir(root, repo.name)
    history = [b for b in load_json(sd / "vitals.json", []) if isinstance(b, dict)]
    if len(history) < 2:
        raise Refused("need at least two vitals readings (before and after) for a certificate")
    before, after = history[0], history[-1]
    first = load_json(sd / "autopsy-first.json", {}) or examine(repo)
    fh = first.get("history", {}) if isinstance(first.get("history"), dict) else {}
    commit = str(before.get("commit", ""))
    surgery = ""
    if COMMIT_RE.fullmatch(commit) and git(repo, "cat-file", "-t", commit) == "commit":
        surgery = git(repo, "diff", "--shortstat", "--no-ext-diff", "--no-textconv", commit, "--")

    def n(b: dict, k: str) -> int:
        v = b.get(k, 0)
        return v if isinstance(v, int) else 0

    md = [f"# 🌿 Certificate of Revival: {code(repo.name, 60)}", "",
          f"Found after {human_age(int(fh.get('days_since_last_commit', 0) or 0))} of silence "
          f"(last words: {code(fh.get('last_words', '...'), 100)}). "
          f"First reading {str(before.get('at', '?'))[:10]}, brought back {str(after.get('at', '?'))[:10]} "
          "by the Sanjivani room on Switch.", "",
          "| | Before | After |", "|---|---|---|",
          f"| Checks passing | {n(before, 'passed')}/{n(before, 'total')} | {n(after, 'passed')}/{n(after, 'total')} |",
          f"| Heartbeat | {pulse_word(n(before, 'passed'), n(before, 'total'))} | "
          f"{pulse_word(n(after, 'passed'), n(after, 'total'))} |",
          f"| Commit | {code(before.get('commit') or '-', 14)} | {code(after.get('commit') or '-', 14)} |", ""]
    if surgery:
        md += [f"Surgery since the first reading: {clean(surgery, 120)}", ""]
    icons = {"pass": "✅", "fail": "❌", "none": "⚪"}
    md += ["## Checks now", *[f"- {icons.get(x.get('status'), '❔')} {clean(x.get('check', '?'), 60)}"
                              for x in after.get("results", []) if isinstance(x, dict) and not x.get("setup")], "",
           "## Readings", *[f"- {str(h.get('at', '?'))[:16]} · {clean(h.get('label', '?'), 40)}: "
                            f"{n(h, 'passed')}/{n(h, 'total')}" for h in history], ""]
    out = repo / "REVIVAL.md"
    ledger = load_json(sd / "written.json", {})
    safe_write(out, "\n".join(md), within=repo, ledger=ledger, force=force)
    save_json(sd / "written.json", ledger)
    return out


def as_list(value) -> list:
    """A JSON field that should be a list may arrive as one string."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_funeral(path: str | None, notes: Path) -> dict:
    """The funeral file must sit in the patient's notes folder, never inside the
    patient itself (a repository could ship its own)."""
    if not path:
        return {}
    p = Path(os.path.expanduser(path)).resolve()
    if not same(p.parent, notes.resolve()):
        raise Refused("the funeral file must be in the patient's notes folder (see: sanjivani.py where)")
    data = load_json(p, {})
    if not data:
        raise Refused(f"could not read {p.name}")
    return data


def donate(repo: Path, root: Path, rels: list[str], force: bool) -> list[str]:
    """Copy up to three reusable files into the organ bank, refusing symlinks,
    anything outside the patient, and big files. Records where they came from."""
    rels = list(dict.fromkeys(r.replace("\\", "/").strip() for r in rels if r and r.strip()))
    if not rels:
        return []
    if len(rels) > MAX_DONATIONS:
        raise Refused(f"at most {MAX_DONATIONS} organs per patient")
    bank = root / "_organ-bank" / repo.name
    for part in (root / "_organ-bank", bank):
        if part.is_symlink():
            raise Refused(f"{part.name} is a symlink; refusing to use it")
    bank.mkdir(parents=True, exist_ok=True)
    ledger_file = state_dir(root, repo.name) / "organs.json"
    ledger = load_json(ledger_file, {})
    picked, used, seen = [], {"origin.md"}, set()
    for rel in rels:  # check every organ before copying any of them
        src = (repo / rel)
        if src.is_symlink():
            raise Refused(f"{clean(rel)} is a symlink; organs must be real files")
        rsrc = src.resolve()
        if not inside(rsrc, repo) or not rsrc.is_file():
            raise Refused(f"{clean(rel)} is not a file inside the patient")
        if rsrc in seen:
            continue
        seen.add(rsrc)
        if rsrc.stat().st_size > MAX_ORGAN_BYTES:
            raise Refused(f"{clean(rel)} is bigger than 1 MB")
        try:
            text = rsrc.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(f"{clean(rel)} is not a text file; organs must be text")
        flat = re.sub(r"[^A-Za-z0-9._-]", "_", rsrc.relative_to(repo).as_posix())
        if flat.lower() in used:  # lower(): Windows and macOS ignore case in file names
            raise Refused(f"{clean(rel)} would get the same organ-bank name as another file ({clean(flat, 60)})")
        used.add(flat.lower())
        check_writable(bank / flat, bank, ledger, force)
        picked.append((flat, text, rsrc.relative_to(repo).as_posix()))
    done = []
    for flat, text, rel in picked:
        safe_write(bank / flat, text, within=bank, ledger=ledger, force=force)
        done.append(rel)
    save_json(ledger_file, ledger)
    origin = load_json(state_dir(root, repo.name) / "origin.json", {})
    own = bool(origin.get("owner_request")) or origin.get("source") == "fixture"
    note = ["# Organs from " + clean(repo.name, 60), "",
            f"- Source: {clean(origin.get('url', 'local folder'), 120)}",
            f"- Brought by: {clean(origin.get('requested_by', 'unknown'), 60)}",
            "- Trust: " + ("the room owner's own project." if own else
                           "**UNTRUSTED.** Someone else's code. Read it before reusing any of it."),
            "", "Files:", *[f"- {clean(d, 120)}" for d in done], ""]
    safe_write(bank / "ORIGIN.md", "\n".join(note), within=bank, ledger=None, force=True)
    return done


def tombstone(repo: Path, root: Path, epitaph: str, lessons: list[str], organs: list[str], force: bool) -> Path:
    if not epitaph:
        raise Refused("an epitaph is required (--epitaph or \"epitaph\" in the --input file)")
    sd = state_dir(root, repo.name)
    r = load_json(sd / "autopsy-first.json", {}) or examine(repo)
    h = r.get("history", {}) if isinstance(r.get("history"), dict) else {}
    body = r.get("body", {}) if isinstance(r.get("body"), dict) else {}
    out = repo / "TOMBSTONE.md"
    ledger = load_json(sd / "written.json", {})
    check_writable(out, repo, ledger, force)  # refuse before any organ is copied
    donated = donate(repo, root, organs, force)
    w = 34

    def row(s: str = "") -> str:
        return "     |" + s.center(w) + "|"

    stone = ["       " + "_" * (w - 2), "      /" + " " * (w - 2) + "\\",
             row("R . I . P"), row(), row(clean(repo.name, w - 4)),
             row(f"{h.get('born', '?')} - {h.get('last_seen', '?')}"),
             row(f"{h.get('commits', 0)} commits"), row()]
    stone += [row(ln) for ln in textwrap.wrap('"' + clean(epitaph, 90) + '"', w - 4)[:3]]
    stone += [row(), "  ~~~" + "~" * (w + 4) + "~~~"]
    causes = r.get("causes_of_death") or [{"cause": "Natural causes", "evidence": "time"}]
    langs = ", ".join(list(body.get("languages", {}))[:3]) or "code"
    md = [f"# ⚰️ {clean(repo.name, 60)}", "", "```", *stone, "```", "",
          f"**Here lies {code(repo.name, 60)}**, {body.get('lines_of_code', 0)} lines of {langs}, laid to rest on "
          f"{dt.date.today().isoformat()} after {human_age(int(h.get('days_since_last_commit', 0) or 0))} of silence.",
          "", "## Cause of death", *[f"- {c.get('cause', '?')}: {c.get('evidence', '')}" for c in causes
                                     if isinstance(c, dict)], "",
          f"Last words: {code(h.get('last_words', '...'), 100)}", ""]
    if lessons:
        md += ["## What it taught me", *[f"- {clean(x, 200)}" for x in lessons[:10]], ""]
    if donated:
        md += ["## Organ donations", "Parts worth reusing, now in the organ bank:",
               *[f"- {code(x, 100)}" for x in donated], ""]
    md += ["---", "*Examined and laid to rest by the Sanjivani room on Switch. Archived, not forgotten.*", ""]
    safe_write(out, "\n".join(md), within=repo, ledger=ledger, force=force)
    save_json(sd / "written.json", ledger)
    return out


# ---------------------------------------------------------------- fixture
FIXTURE_FILES = {
    "README.md": "# weatherly\n\nCLI weather thing. WIP.\n",
    "requirements.txt": "requests==2.22.0\n",
    "weatherly/__init__.py": "",
    "weatherly/config.py": textwrap.dedent('''\
        from collections import Mapping


        DEFAULTS = {"units": "metric", "city": "Hyderabad"}


        def merge(base, override):
            """Deep-merge two config dicts."""
            out = dict(base)
            for key, value in override.items():
                if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
                    out[key] = merge(out[key], value)
                else:
                    out[key] = value
            return out
        '''),
    "weatherly/format.py": textwrap.dedent('''\
        def temperature(celsius, units="metric"):
            # TODO: kelvin, because why not
            if units == "imperial":
                return f"{celsius * 9 / 5 + 32:.0f}°F"
            return f"{celsius:.0f}°C"


        def describe(code):
            # FIXME: only half the WMO codes are here
            return {0: "clear", 1: "mostly clear", 2: "cloudy", 61: "rain", 95: "thunderstorm"}.get(code, "???")
        '''),
    "weatherly/cache.py": textwrap.dedent('''\
        import imp  # TODO: remove, was experimenting with plugin loading
        import json
        import os
        import time

        CACHE = os.path.expanduser("~/.weatherly-cache.json")


        def get(key, max_age=600):
            # TODO: finish this weekend
            raise NotImplementedError
        '''),
    "weatherly/cli.py": textwrap.dedent('''\
        import argparse

        from weatherly import cache
        from weatherly.config import DEFAULTS, merge
        from weatherly.format import describe, temperature


        def main(argv=None):
            p = argparse.ArgumentParser(prog="weatherly")
            p.add_argument("city", nargs="?")
            p.add_argument("--imperial", action="store_true")
            a = p.parse_args(argv)
            cfg = merge(DEFAULTS, {"city": a.city} if a.city else {})
            # TODO: actually call the API (open-meteo?)
            print(cfg["city"], temperature(31, "imperial" if a.imperial else "metric"), describe(0))
            # TODO: --week flag
            # TODO: colors
            # TODO: ascii art clouds


        if __name__ == "__main__":
            main()
        '''),
    "tests/__init__.py": "",
    "tests/test_weatherly.py": textwrap.dedent('''\
        import unittest

        from weatherly.cli import main
        from weatherly.config import merge
        from weatherly.format import describe, temperature


        class TestFormat(unittest.TestCase):
            def test_metric(self):
                self.assertEqual(temperature(31), "31°C")

            def test_imperial(self):
                self.assertEqual(temperature(0, "imperial"), "32°F")

            def test_describe(self):
                self.assertEqual(describe(61), "rain")


        class TestConfig(unittest.TestCase):
            def test_merge_nested(self):
                self.assertEqual(merge({"a": {"b": 1}}, {"a": {"c": 2}}), {"a": {"b": 1, "c": 2}})


        class TestCli(unittest.TestCase):
            def test_runs(self):
                main(["Pune"])


        if __name__ == "__main__":
            unittest.main()
        '''),
}
FIXTURE_HISTORY = [
    ("2022-03-12T23:41:00+05:30", ["README.md", "weatherly/__init__.py", "weatherly/format.py"],
     "init: weatherly, the last weather cli i will ever need"),
    ("2022-03-13T14:55:00+05:30", ["weatherly/config.py", "requirements.txt"], "config merging"),
    ("2022-03-15T01:20:00+05:30", ["weatherly/cli.py", "weatherly/cache.py"], "cli + cache stub"),
    ("2022-03-20T22:05:00+05:30", ["tests/__init__.py", "tests/test_weatherly.py"], "tests!!"),
    ("2022-05-28T03:12:00+05:30", ["weatherly/cache.py"], "WIP: caching, will finish this weekend"),
]


def make_fixture(dest: Path, root: Path) -> Path:
    if dest.exists() and not dest.is_dir():
        raise Refused(f"{dest.name} exists and is not a folder")
    if dest.exists() and any(dest.iterdir()):
        raise Refused(f"{dest.name} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    ident = {"GIT_AUTHOR_NAME": "past you", "GIT_AUTHOR_EMAIL": "past-you@example.com",
             "GIT_COMMITTER_NAME": "past you", "GIT_COMMITTER_EMAIL": "past-you@example.com"}
    env = dict(os.environ, **ident, **GIT_ENV)
    g = exe("git")
    subprocess.run([g, "init", "-q", str(dest)], check=True, env=env)  # no -b: works on old git too
    subprocess.run([g, "-C", str(dest), "symbolic-ref", "HEAD", "refs/heads/main"], check=True, env=env)
    for i, (when, files, msg) in enumerate(FIXTURE_HISTORY):
        for f in files:
            p = dest / f
            p.parent.mkdir(parents=True, exist_ok=True)
            content = FIXTURE_FILES[f]
            if f == "weatherly/cache.py" and i < len(FIXTURE_HISTORY) - 1:
                content = "# cache: coming soon\n"
            p.write_text(content, encoding="utf-8")
        subprocess.run([g, "-C", str(dest), "add", *files], check=True, env=env)
        subprocess.run([g, "-C", str(dest), "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg],
                       check=True, env=dict(env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when))
    save_json(state_dir(root, dest.name) / "origin.json",
              {"source": "fixture", "requested_by": "owner", "owner_request": True,
               "cloned_at": dt.datetime.now().isoformat(timespec="seconds")})
    return dest


# ---------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--graveyard", help="the graveyard folder (default ~/sanjivani-graveyard)")
    ap = argparse.ArgumentParser(prog="sanjivani", description="Examine, revive or bury abandoned projects.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command", required=True)

    cl = sub.add_parser("clone", parents=[common], help="clone a public GitHub repo into the graveyard")
    cl.add_argument("url")
    cl.add_argument("--requested-by", required=True, help="chat name of the person who brought it")
    cl.add_argument("--owner", action="store_true", help="the request came from the room owner")
    cl.add_argument("--allow-private", action="store_true", help="owner only: use the owner's git login")
    cl.add_argument("--skip-size-check", action="store_true", help="owner only")
    cl.add_argument("--max-mb", type=int, default=MAX_REPO_MB)
    cl.add_argument("--limit", type=int, default=PER_PERSON, help="patients per person per 24 hours")

    a = sub.add_parser("autopsy", parents=[common], help="what it was and why it died (never runs its code)")
    a.add_argument("repo")
    a.add_argument("--json", action="store_true")

    v = sub.add_parser("vitals", parents=[common], help="run its checks and record a heartbeat")
    v.add_argument("repo")
    v.add_argument("--label", default="reading")
    v.add_argument("--sandbox", choices=["docker", "host", "auto"], default="docker",
                   help="docker (default) refuses when Docker is not running; host runs the checks on this "
                        "computer with a clean environment; auto picks Docker when it is running")
    v.add_argument("--tests", choices=["auto", "pytest", "unittest"], default="auto")
    v.add_argument("--image", help="Docker image to use instead of the default for the stack")
    v.add_argument("--install", action="store_true",
                   help="Docker only: install the patient's dependencies inside the container first")
    v.add_argument("--allow-source-builds", action="store_true",
                   help="owner only: let --install build packages from source, which runs their setup code")
    v.add_argument("--cmd", action="append", default=[], help="extra check, run without a shell (repeatable)")
    v.add_argument("--timeout", type=int, default=300)
    v.add_argument("--json", action="store_true")

    c = sub.add_parser("certificate", parents=[common], help="write REVIVAL.md from the vitals history")
    c.add_argument("repo")
    c.add_argument("--force", action="store_true", help="overwrite a REVIVAL.md Sanjivani did not write")

    t = sub.add_parser("tombstone", parents=[common], help="write TOMBSTONE.md and fill the organ bank")
    t.add_argument("repo")
    t.add_argument("--input", help='JSON file: {"epitaph": "...", "lessons": [...], "donate": [...]}')
    t.add_argument("--epitaph")
    t.add_argument("--lesson", action="append", default=[])
    t.add_argument("--donate", action="append", default=[], help="file worth reusing (repeatable)")
    t.add_argument("--force", action="store_true", help="overwrite a TOMBSTONE.md Sanjivani did not write")

    w = sub.add_parser("where", parents=[common], help="print the full path of a patient's notes folder")
    w.add_argument("repo")

    f = sub.add_parser("fixture", parents=[common], help="create a dead demo patient with backdated history")
    f.add_argument("dest", nargs="?", default="demo-weatherly")
    args = ap.parse_args(argv)

    try:
        root = graveyard_root(args.graveyard)
        if args.command == "clone":
            dest = clone(args.url, root, args.requested_by, args.owner, args.allow_private, args.max_mb,
                         args.limit, args.skip_size_check)
            print(f"cloned into the graveyard as {dest.name}")
        elif args.command == "autopsy":
            repo = patient_path(args.repo, root)
            rep = examine(repo)
            sd = state_dir(root, repo.name)
            save_json(sd / "autopsy.json", rep)
            if not (sd / "autopsy-first.json").exists():
                save_json(sd / "autopsy-first.json", rep)
            print(json.dumps(rep, indent=2, ensure_ascii=False) if args.json else autopsy_markdown(rep))
        elif args.command == "vitals":
            repo = patient_path(args.repo, root)
            beat = run_vitals(repo, root, args.label, args.cmd, args.timeout, args.sandbox, args.tests,
                              args.image, args.install, args.allow_source_builds)
            history = load_json(state_dir(root, repo.name) / "vitals.json", [])
            print(json.dumps(beat, indent=2, ensure_ascii=False) if args.json else vitals_markdown(beat, history))
        elif args.command == "certificate":
            out = certificate(patient_path(args.repo, root), root, args.force)
            print(f"wrote {out.parent.name}/{out.name}")
        elif args.command == "tombstone":
            repo = patient_path(args.repo, root)
            notes = state_dir(root, repo.name)
            default_input = notes / "funeral.json"
            if not args.input and not args.epitaph and default_input.exists():
                args.input = str(default_input)
            data = load_funeral(args.input, notes)
            lessons = [str(x) for x in as_list(data.get("lessons")) if isinstance(x, (str, int, float))] + args.lesson
            organs = [str(x) for x in as_list(data.get("donate")) if isinstance(x, str)] + args.donate
            epitaph = str(data.get("epitaph") or args.epitaph or "")
            out = tombstone(repo, root, epitaph, lessons, organs, args.force)
            print(f"wrote {out.parent.name}/{out.name}")
        elif args.command == "where":
            print(state_dir(root, patient_path(args.repo, root).name))
        elif args.command == "fixture":
            dest = patient_path(args.dest, root, must_exist=False)
            print(f"created a dead demo patient: {make_fixture(dest, root).name}")
    except Refused as e:
        print(f"sanjivani: refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
