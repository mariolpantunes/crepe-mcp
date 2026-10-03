#!/usr/bin/env python3
"""Setup script for CREPE MCP server integration with Goose, Claude, and AGY CLI.

Uses argparse to provide --install and --uninstall modes across target clients
(--target {all,goose,claude,agy}), auto-detects system dependencies (`shutil.which`
and macOS /Applications and /opt/homebrew paths), exports non-secret environment variables
(`CREPE_` prefixed) to the user's shell profile (~/.bashrc / ~/.zshrc), and registers
standard stdio configurations with client hosts.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    # Not fatal: setup.py is normally launched with the *system* interpreter, which
    # is under no obligation to ship PyYAML. Only the Goose target needs it, and by
    # the time we get there ensure_venv() has installed it into VENV_DIR as a CREPE
    # dependency. See load_yaml() for the fallback.
    yaml = None  # type: ignore[assignment]


SCRIPT_DIR = str(Path(__file__).resolve().parent)
VENV_DIR = Path(SCRIPT_DIR) / "venv"
AGENTS_MD_SRC = Path(__file__).resolve().parent / "AGENTS.md"

# Target Config Paths
GOOSE_CONFIG_DIR = Path.home() / ".config" / "goose"
GOOSE_CONFIG_PATH = GOOSE_CONFIG_DIR / "config.yaml"
AGENTS_MD_DST = GOOSE_CONFIG_DIR / "CREPE_AGENTS.md"  # legacy install location, only cleaned up
SKILL_SRC = Path(__file__).resolve().parent / ".agents" / "skills" / "crepe"
SKILL_DST = Path.home() / ".agents" / "skills" / "crepe"

AGY_CONFIG_DIR = Path.home() / ".gemini" / "config"
AGY_CONFIG_PATH = AGY_CONFIG_DIR / "mcp_config.json"

CLAUDE_LINUX_DIR = Path.home() / ".config" / "Claude"
CLAUDE_LINUX_PATH = CLAUDE_LINUX_DIR / "claude_desktop_config.json"
CLAUDE_MACOS_DIR = Path.home() / "Library" / "Application Support" / "Claude"
CLAUDE_MACOS_PATH = CLAUDE_MACOS_DIR / "claude_desktop_config.json"
CLAUDE_CODE_PATH = Path.home() / ".claude.json"

# Block delimiters for shell profile injection
PROFILE_BLOCK_START = "# === CREPE MCP Environment Variables ==="
PROFILE_BLOCK_END = "# === End CREPE MCP ==="

# Sub-server registry.
#
# `description` is not cosmetic: Goose's Extension Manager reads it to decide
# which extension to enable for a given request, so each one states the tools it
# provides and the trigger for turning it on. Without it the manager has only the
# display name to go on and cannot route reliably.
#
# `enabled` controls what is loaded at session start. Only Research is always on
# (cheap, no local binaries, and useful in almost any conversation); the other
# four stay off so their tool schemas do not occupy context until the Extension
# Manager activates them on demand.
#
# That gating only works where the agent can reach the Extension Manager. Under
# an ACP provider the agent is the external tool (e.g. Claude Code), and Goose
# forwards MCP extensions to it but not its own `type: platform` ones — so
# manage_extensions is unreachable and a disabled sub-server can never be turned
# on. Install with --enable-all there; see the ACP section in AGENTS.md.
SUB_SERVERS = [
    {
        "name": "crepe-presentations",
        "cmd": "crepe-presentations",
        "display": "CREPE Presentations",
        "enabled": False,
        "description": (
            "Author and compile slide decks. Create and edit presentations in Pandoc Markdown, "
            "lint them, compile to Beamer PDF or PowerPoint (.pptx), and render slides to PNG "
            "for visual review. ENABLE THIS when the user asks for a presentation, slide deck, "
            "talk, seminar, defence, or .pptx/Beamer output."
        ),
    },
    {
        "name": "crepe-documents",
        "cmd": "crepe-documents",
        "display": "CREPE Documents",
        "enabled": False,
        "description": (
            "Author and compile A4 documents. Create and edit reports, papers, and theses as "
            "chapters/sections in Pandoc Markdown, lint them, compile to PDF or Word (.docx), "
            "and render pages to PNG. ENABLE THIS when the user asks for a report, paper, "
            "article, deliverable, thesis, or .docx/PDF document."
        ),
    },
    {
        "name": "crepe-research",
        "cmd": "crepe-research",
        "display": "CREPE Research",
        "enabled": True,
        "description": (
            "Academic and web research. Search peer-reviewed papers on Semantic Scholar "
            "(academic_search) and preprints on arXiv (arxiv_search), run live web searches "
            "via Tavily (web_search), look up and read Wikipedia articles, and extract the "
            "full readable text of any URL (fetch_webpage). Use for citations, literature "
            "review, fact-checking, and gathering current sources."
        ),
    },
    {
        "name": "crepe-spreadsheets",
        "cmd": "crepe-spreadsheets",
        "display": "CREPE Spreadsheets",
        "enabled": False,
        "description": (
            "Create, inspect, and update styled Excel workbooks (.xlsx), including converting "
            "Markdown tables to Excel and writing cells/formulas. ENABLE THIS when the user "
            "asks for a spreadsheet, Excel file, workbook, or .xlsx output."
        ),
    },
    {
        "name": "crepe-diagrams",
        "cmd": "crepe-diagrams",
        "display": "CREPE Diagrams",
        "enabled": False,
        "description": (
            "Validate and export draw.io / diagrams.net diagrams to PNG, SVG, or PDF, and "
            "inspect their page structure. ENABLE THIS when the user asks to work with "
            ".drawio diagrams or export a diagram to an image."
        ),
    },
]


def load_yaml():
    """Return the PyYAML module, falling back to the one installed inside VENV_DIR.

    Returns None when PyYAML is reachable from neither interpreter, letting callers
    skip the Goose target with a warning instead of aborting the whole install.
    """
    global yaml
    if yaml is not None:
        return yaml
    for site in sorted(VENV_DIR.glob("lib/python3.*/site-packages")):
        if site.is_dir() and str(site) not in sys.path:
            sys.path.insert(0, str(site))
    try:
        import yaml as _yaml
    except ImportError:
        return None
    yaml = _yaml
    return yaml


def detect_shell_profile() -> Path:
    """Detect the appropriate shell profile (~/.bashrc vs ~/.zshrc) based on $SHELL."""
    shell_env = os.environ.get("SHELL", "").lower()
    if "zsh" in shell_env:
        return Path.home() / ".zshrc"
    if "fish" in shell_env:
        fish_cfg = Path.home() / ".config" / "fish" / "config.fish"
        if fish_cfg.exists():
            return fish_cfg
    bashrc = Path.home() / ".bashrc"
    if bashrc.exists() or not (Path.home() / ".bash_profile").exists():
        return bashrc
    return Path.home() / ".bash_profile"


def which_binary(name: str) -> str | None:
    """Find binary on PATH or standard macOS Homebrew / Unix locations."""
    search_path = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
    return shutil.which(name) or shutil.which(name, path=search_path)


def find_headless_browser() -> str | None:
    """Auto-detect Chromium, Chrome, Brave, or Edge across Linux and macOS paths."""
    candidates = [
        "google-chrome",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "brave",
        "microsoft-edge",
    ]
    for binary in candidates:
        found = which_binary(binary)
        if found:
            return found

    macos_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for p in macos_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


API_KEY_VARS = {
    "tavily": ("CREPE_TAVILY_API_KEY", "Tavily API key for web_search"),
    "semantic_scholar": ("CREPE_SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar API key (optional, avoids 429s)"),
}


def env_file_path() -> Path:
    """The `.env` the servers read their API keys from (same default as crepe_mcp.config)."""
    explicit = os.environ.get("CREPE_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
    return config_home / "crepe-mcp" / ".env"


def env_file_has(name: str) -> bool:
    """True when the `.env` already defines a non-empty value for `name` (value never returned)."""
    path = env_file_path()
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() == name and value.strip().strip("\"'"):
            return True
    return False


def store_env_value(name: str, value: str) -> None:
    """Set `name` in the `.env` (created 0600, folder 0700), keeping every other line as it is."""
    path = env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    entry = f"{name}={value}"
    for i, line in enumerate(lines):
        if line.strip().removeprefix("export ").partition("=")[0].strip() == name:
            lines[i] = entry
            break
    else:
        lines.append(entry)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def configure_api_keys(non_interactive: bool) -> None:
    """Make sure each API key is in the `.env`. Keys never go into client configs or the shell profile.

    A key already in the `.env` is kept. Otherwise it is taken from the process environment or, when
    interactive, from a hidden prompt. Values are never printed.
    """
    for label, (name, description) in API_KEY_VARS.items():
        if env_file_has(name):
            print(f"🔑 {name} already set in {env_file_path()}")
            continue
        value = os.environ.get(name, "").strip()
        if not value and not non_interactive:
            value = interactive_prompt_secret(f"Enter {description} (or Enter to skip)")
        if value:
            store_env_value(name, value)
            print(f"🔑 Stored {name} in {env_file_path()} (0600)")
        else:
            print(f"ℹ️  No {label} key: add {name}=... to {env_file_path()} to enable it.")


def find_libreoffice() -> str | None:
    """Auto-detect a native or flatpak soffice/libreoffice binary, or the macOS app bundle."""
    for binary in ("soffice", "libreoffice"):
        found = which_binary(binary)
        if found:
            return found

    flatpak_paths = [
        str(Path.home() / ".local/share/flatpak/exports/bin/org.libreoffice.LibreOffice"),
        "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
    ]
    for p in flatpak_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    macos_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.isfile(macos_path) and os.access(macos_path, os.X_OK):
        return macos_path
    return None


def has_flatpak_libreoffice() -> bool:
    """Detect a Flatpak install of LibreOffice (Linux only). Returns a bool."""
    if not sys.platform.startswith("linux") or not shutil.which("flatpak"):
        return False
    try:
        result = subprocess.run(
            ["flatpak", "info", "org.libreoffice.LibreOffice"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def find_drawio() -> str | None:
    """Find draw.io executable using which on PATH, standard SlackBuild / Unix / macOS paths, or verified Flatpak."""
    for binary in ("drawio", "draw.io"):
        found = which_binary(binary)
        if found:
            return found

    for opt_path in ("/opt/drawio/drawio", "/opt/draw.io/drawio", "/usr/local/bin/drawio", "/usr/bin/drawio"):
        if os.path.isfile(opt_path) and os.access(opt_path, os.X_OK):
            return opt_path

    macos_path = "/Applications/draw.io.app/Contents/MacOS/draw.io"
    if os.path.isfile(macos_path) and os.access(macos_path, os.X_OK):
        return macos_path

    if has_flatpak_drawio():
        flatpak_paths = [
            str(Path.home() / ".local/share/flatpak/exports/bin/com.jgraph.drawio.desktop"),
            "/var/lib/flatpak/exports/bin/com.jgraph.drawio.desktop",
        ]
        for p in flatpak_paths:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p

    return None


def has_flatpak_drawio() -> bool:
    """Detect a Flatpak install of draw.io (Linux only). Returns a bool."""
    if not sys.platform.startswith("linux") or not shutil.which("flatpak"):
        return False
    try:
        result = subprocess.run(
            ["flatpak", "info", "com.jgraph.drawio.desktop"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def update_shell_profile(
    profile_path: Path,
    browser_path: str,
    libreoffice_path: str = "",
    drawio_path: str = "",
) -> None:
    """Insert or update exported CREPE variables in the user's shell profile."""
    content = profile_path.read_text("utf-8") if profile_path.exists() else ""

    pattern = re.compile(
        re.escape(PROFILE_BLOCK_START) + r".*?" + re.escape(PROFILE_BLOCK_END) + r"\n?",
        re.DOTALL,
    )
    content = pattern.sub("", content).rstrip()
    content = content + "\n\n" if content else ""

    lines = [PROFILE_BLOCK_START]
    if browser_path:
        lines.append(f'export CREPE_HEADLESS_BROWSER_PATH="{browser_path}"')
    if libreoffice_path:
        lines.append(f'export CREPE_LIBREOFFICE_PATH="{libreoffice_path}"')
    if drawio_path:
        lines.append(f'export CREPE_DRAWIO_PATH="{drawio_path}"')
    lines.append(PROFILE_BLOCK_END)

    block_str = "\n".join(lines) + "\n"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(content + block_str)
    print(f"✅ Updated environment variables in shell profile: {profile_path}")


def remove_shell_profile_block(profile_path: Path) -> None:
    """Remove exported CREPE variables block from shell profile."""
    if not profile_path.exists():
        return
    content = profile_path.read_text("utf-8")
    pattern = re.compile(
        re.escape(PROFILE_BLOCK_START) + r".*?" + re.escape(PROFILE_BLOCK_END) + r"\n?",
        re.DOTALL,
    )
    new_content = pattern.sub("", content)
    if new_content != content:
        profile_path.write_text(new_content, "utf-8")
        print(f"🧹 Removed CREPE environment variables from profile: {profile_path}")


def ensure_venv() -> bool:
    """Ensure local virtual environment exists and is up to date using native venv + pip."""
    pip_bin = VENV_DIR / "bin" / "pip"
    if not pip_bin.exists():
        print(f"📦 Creating virtual environment at {VENV_DIR} using python3 -m venv...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        except Exception as exc:
            print(f"❌ Failed to create virtual environment: {exc}", file=sys.stderr)
            return False

    print(f"📦 Installing/updating CREPE dependencies in {VENV_DIR} via pip...")
    try:
        subprocess.run(
            [str(pip_bin), "install", "-e", SCRIPT_DIR],
            check=True,
        )
        print(f"✅ Virtual environment ready at {VENV_DIR}")
        return True
    except Exception as exc:
        print(f"❌ Failed to install dependencies via pip: {exc}", file=sys.stderr)
        return False


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write `text` to `path` via a temp file + rename, so a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def backup_file(path: Path, keep: int = 5) -> Path:
    """Copy `path` to a timestamped `.bak-*` sibling and prune all but the newest `keep` backups."""
    dst = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dst)
    os.chmod(dst, 0o600)
    for old in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-keep]:
        old.unlink(missing_ok=True)
    return dst


def splice_extensions(text: str, entries: dict, remove: list[str]) -> str | None:
    """Replace, add and remove extension blocks in raw Goose config text.

    Only the named blocks under the top-level `extensions:` key are touched, so
    comments, key order and every other setting survive (a PyYAML round-trip would
    drop them). Returns None when the layout is not the expected block style; the
    caller then falls back to a full rewrite.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"extensions:\s*(#.*)?$", ln)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i].startswith((" ", "#"))),
        len(lines),
    )
    drop = set(entries) | set(remove)
    kept: list[str] = []
    skipping = False
    for ln in lines[start + 1:end]:
        key = re.match(r"  (\S[^:]*):\s*(#.*)?$", ln)
        if key:
            skipping = key.group(1).strip("'\"") in drop
        elif ln.startswith("  #") or (ln.strip() and not ln.startswith("   ")):
            skipping = False  # a comment at block indent belongs to the block that follows
        if not skipping:
            kept.append(ln)
    while kept and not kept[-1].strip():
        kept.pop()
    added = ""
    for name, entry in entries.items():
        dumped = yaml.safe_dump({name: entry}, sort_keys=False, allow_unicode=True)
        added += "".join("  " + ln if ln.strip() else ln for ln in dumped.splitlines(keepends=True))
    tail = lines[end:]
    sep = ["\n"] if tail and tail[0].strip() else []
    body = "".join(lines[:start + 1] + kept)
    if kept and not kept[-1].endswith("\n"):
        body += "\n"
    return body + added + "".join(sep + tail)


def write_goose_extensions(config: dict, entries: dict, remove: list[str]) -> bool:
    """Apply `entries`/`remove` to the Goose config file, keeping it intact. Returns True if the file changed."""
    old_text = GOOSE_CONFIG_PATH.read_text(encoding="utf-8") if GOOSE_CONFIG_PATH.exists() else ""
    new_text = splice_extensions(old_text, entries, remove) if old_text else None
    if new_text is not None:
        # Verify the splice: everything except our extension blocks must be unchanged.
        try:
            check = yaml.safe_load(new_text) or {}
            want = {k: v for k, v in config.items() if k != "extensions"}
            got = {k: v for k, v in check.items() if k != "extensions"}
            ours = set(entries) | set(remove)
            ext_old = {k: v for k, v in (config.get("extensions") or {}).items() if k not in ours}
            ext_new = {k: v for k, v in (check.get("extensions") or {}).items() if k not in entries}
            ok = want == got and ext_old == ext_new and all(check["extensions"].get(k) == v for k, v in entries.items())
        except Exception:
            ok = False
        if not ok:
            print("⚠️ Targeted edit did not verify; falling back to a full rewrite (comments will be lost).")
            new_text = None
    if new_text is None:
        extensions = config.setdefault("extensions", {})
        for name in remove:
            extensions.pop(name, None)
        extensions.update(entries)
        new_text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    if new_text == old_text:
        return False
    atomic_write(GOOSE_CONFIG_PATH, new_text)
    return True


def update_goose_config(envs: dict[str, str], enable_all: bool = False) -> bool:
    """Register or update CREPE MCP server in ~/.config/goose/config.yaml.

    Only the CREPE blocks are edited; the rest of the file is left as it was. A
    timestamped backup is taken first. An extension the user already toggled keeps
    its `enabled` state on re-install, unless `enable_all` is given, and env values
    already in the config (paths) are kept unless a new value is passed.

    `enable_all` turns on every sub-server instead of honouring the per-server
    `enabled` flag in SUB_SERVERS. Required for hosts that cannot reach Goose's
    Extension Manager — see the ACP note in AGENTS.md.
    """
    yaml = load_yaml()
    if yaml is None:
        print("❌ PyYAML unavailable — skipping Goose target. Run 'python3 -m pip install pyyaml'.")
        return False
    GOOSE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if GOOSE_CONFIG_PATH.exists():
        try:
            with open(GOOSE_CONFIG_PATH, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            if not isinstance(config, dict):
                raise ValueError("top level is not a mapping")
        except Exception as e:
            print(f"❌ Failed to parse existing {GOOSE_CONFIG_PATH}: {e}")
            return False
        print(f"🗂️  Backed up Goose config to {backup_file(GOOSE_CONFIG_PATH)}")

    existing = config.get("extensions") or {}
    # Re-installing must not drop settings (e.g. tool paths) that this run was not given again.
    for name in ["crepe"] + [sub["name"] for sub in SUB_SERVERS]:
        previous_envs = (existing.get(name) or {}).get("envs")
        if isinstance(previous_envs, dict) and previous_envs:
            envs = {**previous_envs, **envs}
            break
    # "crepe" is the removed monolith entry of older installs: always drop it.
    remove = ["crepe"]
    entries = {}
    for sub in SUB_SERVERS:
        previous = existing.get(sub["name"], {})
        enabled = True if enable_all else previous.get("enabled", sub["enabled"])
        entries[sub["name"]] = {
            "enabled": bool(enabled),
            "type": "stdio",
            "name": sub["name"],
            "description": sub["description"],
            "display_name": sub["display"],
            "cmd": str(VENV_DIR / "bin" / sub["cmd"]),
            "args": [],
            "timeout": 300,
            "envs": envs,
            "env_keys": [],
        }
    print(f"📦 Configured Goose mode: {len(SUB_SERVERS)} Separate Sub-Servers")
    on = [n for n, e in entries.items() if e["enabled"]]
    off = [n for n, e in entries.items() if not e["enabled"]]
    print(f"   ├─ enabled: {', '.join(on) or 'none'}")
    print(f"   └─ on demand: {', '.join(off) or 'none'} (activated by the Extension Manager)")

    try:
        changed = write_goose_extensions(config, entries, remove)
    except OSError as e:
        print(f"❌ Could not write {GOOSE_CONFIG_PATH}: {e}")
        return False
    if changed:
        print(f"✅ Registered CREPE in Goose config: {GOOSE_CONFIG_PATH}")
    else:
        print("✅ Goose config already up to date")
    return True


def remove_from_goose_config() -> None:
    """Remove CREPE MCP servers from Goose config."""
    if not GOOSE_CONFIG_PATH.exists():
        return
    yaml = load_yaml()
    if yaml is None:
        print("⚠️ Warning: PyYAML unavailable — left Goose config untouched.")
        return
    try:
        with open(GOOSE_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"⚠️ Warning: Failed to parse {GOOSE_CONFIG_PATH}: {e}")
        return

    names = ["crepe"] + [sub["name"] for sub in SUB_SERVERS]
    if not any(n in (config.get("extensions") or {}) for n in names):
        return
    backup_file(GOOSE_CONFIG_PATH)
    try:
        write_goose_extensions(config, {}, names)
    except OSError as e:
        print(f"⚠️ Warning: Could not write {GOOSE_CONFIG_PATH}: {e}")
        return
    print(f"🧹 Removed CREPE from Goose config: {GOOSE_CONFIG_PATH}")


def update_json_mcp_config(
    config_path: Path,
    client_name: str,
    envs: dict[str, str],
) -> bool:
    """Register or update CREPE in standard JSON mcpServers format (AGY CLI / Claude)."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f) or {}
        except Exception as e:
            print(f"❌ Failed to parse {client_name} config ({config_path}): {e}")
            return False
        backup_path = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup_path)
        os.chmod(backup_path, 0o600)
        print(f"🗂️  Backed up {client_name} config to {backup_path}")

    mcp_servers = config.setdefault("mcpServers", {})

    mcp_servers.pop("crepe", None)  # removed monolith entry of older installs
    for sub in SUB_SERVERS:
        cmd_path = str(VENV_DIR / "bin" / sub["cmd"])
        mcp_servers[sub["name"]] = {
            "command": cmd_path,
            "args": [],
            "env": envs,
        }
    print(f"📦 Configured {client_name} mode: 5 Separate Sub-Servers")

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.chmod(config_path, 0o600)
    print(f"✅ Registered CREPE in {client_name} config: {config_path}")
    return True


def remove_from_json_mcp_config(config_path: Path, client_name: str) -> None:
    """Remove CREPE entries from standard JSON mcpServers config."""
    if not config_path.exists():
        return
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f) or {}
    except Exception as e:
        print(f"⚠️ Warning: Failed to parse {config_path}: {e}")
        return

    mcp_servers = config.get("mcpServers", {})
    modified = False
    if "crepe" in mcp_servers:
        del mcp_servers["crepe"]
        modified = True
    for sub in SUB_SERVERS:
        if sub["name"] in mcp_servers:
            del mcp_servers[sub["name"]]
            modified = True
    if modified:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"🧹 Removed CREPE from {client_name} config: {config_path}")


def install_skill() -> bool:
    """Install the crepe skill into ~/.agents/skills/crepe, with AGENTS.md as references/guide.md.

    The copy is built next to the destination and swapped in, so a failure leaves
    any previous install untouched.
    """
    if not (SKILL_SRC / "SKILL.md").is_file():
        print(f"⚠️ Warning: skill source not found at {SKILL_SRC}; skipping skill install.")
        return False
    tmp = SKILL_DST.with_name(SKILL_DST.name + ".tmp")
    try:
        SKILL_DST.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(SKILL_SRC, tmp, ignore=shutil.ignore_patterns("__pycache__"))
        if AGENTS_MD_SRC.is_file():
            (tmp / "references").mkdir(exist_ok=True)
            shutil.copy2(AGENTS_MD_SRC, tmp / "references" / "guide.md")
        shutil.rmtree(SKILL_DST, ignore_errors=True)
        os.replace(tmp, SKILL_DST)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"❌ Could not install the CREPE skill: {e}", file=sys.stderr)
        return False
    print(f"🧠 Installed CREPE agent skill: {SKILL_DST}")
    return True


def remove_skill() -> None:
    """Remove the crepe skill, and the CREPE_AGENTS.md copy that older installs left in the Goose config dir."""
    if SKILL_DST.exists():
        shutil.rmtree(SKILL_DST, ignore_errors=True)
        print(f"🧹 Removed CREPE agent skill: {SKILL_DST}")
    if AGENTS_MD_DST.exists():
        AGENTS_MD_DST.unlink()
        print(f"🧹 Removed legacy CREPE agent guide: {AGENTS_MD_DST}")


def interactive_prompt(prompt_text: str, default_val: str = "") -> str:
    """Ask user for input, showing default if present."""
    display = f"{prompt_text} [{default_val}]: " if default_val else f"{prompt_text}: "
    try:
        ans = input(display).strip()
        return ans if ans else default_val
    except (KeyboardInterrupt, EOFError):
        print("\nInstallation aborted by user.")
        sys.exit(1)


def interactive_prompt_secret(prompt_text: str) -> str:
    """Ask user for sensitive input without echoing."""
    try:
        return getpass.getpass(f"{prompt_text}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nInstallation aborted by user.")
        sys.exit(1)


def resolve_targets(target_args: list[str]) -> list[str]:
    """Resolve target client applications."""
    if not target_args or "all" in target_args:
        # Check installed environments or default to available
        detected = []
        if GOOSE_CONFIG_DIR.exists() or Path.home().joinpath(".config", "goose").exists():
            detected.append("goose")
        if AGY_CONFIG_DIR.exists() or Path.home().joinpath(".gemini").exists():
            detected.append("agy")
        if CLAUDE_LINUX_DIR.exists() or CLAUDE_MACOS_DIR.exists() or CLAUDE_CODE_PATH.exists():
            detected.append("claude")
        return detected if detected else ["goose", "agy", "claude"]
    return list(set(target_args))


def run_install(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.target)
    print(f"🚀 Installing CREPE MCP Server (`Option 1: Local Development` at {SCRIPT_DIR})")
    print(f"🎯 Selected Targets: {', '.join(targets)}\n")

    # 1. Dependency checks
    for bin_name, hint in [
        ("pandoc", "Required for PDF/PPTX/DOCX compilation"),
        ("lualatex", "Required for PDF/Beamer output (TeX Live / MacTeX)"),
    ]:
        found = which_binary(bin_name)
        if not found:
            print(f"⚠️ Warning: `{bin_name}` was not found on PATH. {hint}")

    # 2. Browser
    browser_path = args.browser_path or os.environ.get("CREPE_HEADLESS_BROWSER_PATH", "").strip()
    if not browser_path:
        detected = find_headless_browser()
        if detected:
            print(f"🔍 Auto-detected headless browser: {detected}")
            browser_path = detected if args.non_interactive else interactive_prompt(
                "Confirm browser path", detected
            )
        elif not args.non_interactive:
            browser_path = interactive_prompt("Enter browser executable path (or Enter to skip)")

    # 4. LibreOffice
    libreoffice_path = args.libreoffice_path or os.environ.get("CREPE_LIBREOFFICE_PATH", "").strip()
    if not libreoffice_path:
        detected_lo = find_libreoffice()
        if detected_lo:
            print(f"🔍 Auto-detected LibreOffice: {detected_lo}")
            libreoffice_path = detected_lo if args.non_interactive else interactive_prompt(
                "Confirm LibreOffice path", detected_lo
            )
        elif has_flatpak_libreoffice():
            print("🔍 Found LibreOffice Flatpak (org.libreoffice.LibreOffice)")
        elif not args.non_interactive:
            libreoffice_path = interactive_prompt("Enter LibreOffice executable path (or Enter to skip)")

    # 5. draw.io
    drawio_env = os.environ.get("CREPE_DRAWIO_PATH", "").strip()
    if drawio_env and (not os.path.isfile(drawio_env) or not os.access(drawio_env, os.X_OK)):
        drawio_env = ""
    drawio_path = args.drawio_path or drawio_env
    if not drawio_path:
        detected_dio = find_drawio()
        if detected_dio:
            print(f"🔍 Auto-detected draw.io: {detected_dio}")
            drawio_path = detected_dio if args.non_interactive else interactive_prompt(
                "Confirm draw.io path", detected_dio
            )
        elif has_flatpak_drawio():
            print("🔍 Found draw.io Flatpak (com.jgraph.drawio.desktop)")
        elif not args.non_interactive:
            drawio_path = interactive_prompt("Enter draw.io executable path (or Enter to skip)")

    # 5b. API keys go to the .env the servers load, never into client configs
    configure_api_keys(args.non_interactive)

    # 5c. Virtual environment preparation (native venv + pip)
    if not ensure_venv():
        print("❌ Installation aborted: failed to set up virtual environment.", file=sys.stderr)
        sys.exit(1)

    # 6. Shell Profile
    profile_path = detect_shell_profile()
    update_shell_profile(profile_path, browser_path, libreoffice_path, drawio_path)

    # 7. Build env dict
    envs = {}
    if browser_path:
        envs["CREPE_HEADLESS_BROWSER_PATH"] = browser_path
    if libreoffice_path:
        envs["CREPE_LIBREOFFICE_PATH"] = libreoffice_path
    if drawio_path:
        envs["CREPE_DRAWIO_PATH"] = drawio_path

    # 8. Update Target Configurations
    failed: list[str] = []
    if "goose" in targets:
        if not update_goose_config(envs, enable_all=getattr(args, "enable_all", False)):
            failed.append("Goose config")
        if not install_skill():
            failed.append("CREPE skill")
        if AGENTS_MD_DST.exists():
            AGENTS_MD_DST.unlink()
            print(f"🧹 Removed legacy CREPE agent guide (replaced by the skill): {AGENTS_MD_DST}")

    if "agy" in targets:
        update_json_mcp_config(AGY_CONFIG_PATH, "AGY CLI", envs)

    if "claude" in targets:
        claude_path = CLAUDE_MACOS_PATH if sys.platform == "darwin" else CLAUDE_LINUX_PATH
        update_json_mcp_config(claude_path, "Claude Desktop", envs)
        if CLAUDE_CODE_PATH.exists():
            update_json_mcp_config(CLAUDE_CODE_PATH, "Claude Code", envs)

    if failed:
        print(f"\n⚠️ CREPE installed with problems: {', '.join(failed)} failed (see messages above).", file=sys.stderr)
        print(f"💡 To apply environment variables immediately in your current shell:\n    source {profile_path}")
        sys.exit(1)
    print("\n🎉 CREPE MCP server installation completed successfully!")
    print(f"💡 To apply environment variables immediately in your current shell:\n    source {profile_path}")


def run_uninstall(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.target)
    print(f"🧹 Uninstalling CREPE MCP Server from {', '.join(targets)} & Shell Profile...\n")
    profile_path = detect_shell_profile()
    remove_shell_profile_block(profile_path)

    if "goose" in targets:
        remove_from_goose_config()
        remove_skill()

    if "agy" in targets:
        remove_from_json_mcp_config(AGY_CONFIG_PATH, "AGY CLI")

    if "claude" in targets:
        claude_path = CLAUDE_MACOS_PATH if sys.platform == "darwin" else CLAUDE_LINUX_PATH
        remove_from_json_mcp_config(claude_path, "Claude Desktop")
        if CLAUDE_CODE_PATH.exists():
            remove_from_json_mcp_config(CLAUDE_CODE_PATH, "Claude Code")

    print("\n✅ Uninstalled CREPE MCP server completely.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Setup script for registering CREPE MCP Server with Goose, Claude, and AGY CLI."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--install",
        action="store_true",
        help="Install and register CREPE MCP in client hosts (default mode).",
    )
    group.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall and remove CREPE MCP from client configs and shell profile.",
    )
    parser.add_argument(
        "--target",
        nargs="+",
        choices=["all", "goose", "claude", "agy"],
        default=["all"],
        help="Target client hosts to configure (default: all detected).",
    )
    parser.add_argument(
        "--enable-all",
        action="store_true",
        help=(
            "Goose only: register every sub-server as enabled instead of leaving the "
            "on-demand ones off. Use with hosts that cannot reach Goose's Extension "
            "Manager (e.g. the claude-acp / ACP providers) — see AGENTS.md."
        ),
    )
    parser.add_argument(
        "-y",
        "--non-interactive",
        action="store_true",
        help="Do not prompt for missing inputs; accept defaults and flags.",
    )
    parser.add_argument("--browser-path", type=str, default="", help="Path to Chromium/Chrome binary.")
    parser.add_argument("--libreoffice-path", type=str, default="", help="Path to LibreOffice executable.")
    parser.add_argument("--drawio-path", type=str, default="", help="Path to draw.io executable.")

    args = parser.parse_args()
    if args.uninstall:
        run_uninstall(args)
    else:
        run_install(args)


if __name__ == "__main__":
    main()
