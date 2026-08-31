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
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Run 'pip install pyyaml' or use 'uv run setup.py'.", file=sys.stderr)
    sys.exit(1)


SCRIPT_DIR = str(Path(__file__).resolve().parent)
AGENTS_MD_SRC = Path(__file__).resolve().parent / "AGENTS.md"

# Target Config Paths
GOOSE_CONFIG_DIR = Path.home() / ".config" / "goose"
GOOSE_CONFIG_PATH = GOOSE_CONFIG_DIR / "config.yaml"
GOOSE_CONFIG_BACKUP_PATH = GOOSE_CONFIG_DIR / "config.yaml.bak"
AGENTS_MD_DST = GOOSE_CONFIG_DIR / "CREPE_AGENTS.md"

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

SUB_SERVERS = [
    ("crepe-presentations", "crepe-presentations", "CREPE Presentations"),
    ("crepe-documents",     "crepe-documents",     "CREPE Documents"),
    ("crepe-research",      "crepe-research",      "CREPE Research"),
    ("crepe-spreadsheets",  "crepe-spreadsheets",  "CREPE Spreadsheets"),
    ("crepe-diagrams",      "crepe-diagrams",      "CREPE Diagrams"),
]


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


def read_keys_file() -> dict[str, str]:
    """Read API keys from keys.md or .keys.md in the repo root or home directory."""
    candidates = [
        Path(SCRIPT_DIR) / "keys.md",
        Path(SCRIPT_DIR) / ".keys.md",
        Path.home() / "keys.md",
        Path.home() / ".keys.md",
    ]
    keys: dict[str, str] = {}
    for p in candidates:
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if "s2" in k or "semantic" in k:
                            keys.setdefault("ss_key", v)
                        elif "tavily" in k:
                            keys.setdefault("tavily_key", v)
            except Exception:
                pass
            if keys:
                break
    return keys


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
    """Auto-detect a native draw.io binary (PATH, SlackBuild /opt paths), macOS app bundle, or flatpak."""
    for binary in ("drawio", "draw.io", "/opt/drawio/drawio", "/opt/draw.io/drawio"):
        if "/" in binary:
            if os.path.isfile(binary) and os.access(binary, os.X_OK):
                return binary
        else:
            found = which_binary(binary)
            if found:
                return found

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


def update_goose_config(envs: dict[str, str], legacy: bool = False) -> bool:
    """Register or update CREPE MCP server in ~/.config/goose/config.yaml."""
    GOOSE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if GOOSE_CONFIG_PATH.exists():
        try:
            with open(GOOSE_CONFIG_PATH, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"❌ Failed to parse existing {GOOSE_CONFIG_PATH}: {e}")
            return False
        shutil.copy2(GOOSE_CONFIG_PATH, GOOSE_CONFIG_BACKUP_PATH)
        os.chmod(GOOSE_CONFIG_BACKUP_PATH, 0o600)
        print(f"🗂️  Backed up Goose config to {GOOSE_CONFIG_BACKUP_PATH}")

    extensions = config.setdefault("extensions", {})

    if legacy:
        for sub_name, _, _ in SUB_SERVERS:
            extensions.pop(sub_name, None)
        extensions["crepe"] = {
            "enabled": True,
            "type": "stdio",
            "name": "crepe",
            "display_name": "CREPE Presentation Engine",
            "cmd": "uv",
            "args": ["--directory", SCRIPT_DIR, "run", "crepe-mcp"],
            "timeout": 300,
            "envs": envs,
            "env_keys": [],
        }
        print("📦 Configured Goose mode: Monolith (crepe-mcp, 40 tools)")
    else:
        extensions.pop("crepe", None)
        for sub_name, cmd_name, display in SUB_SERVERS:
            extensions[sub_name] = {
                "enabled": True,
                "type": "stdio",
                "name": sub_name,
                "display_name": display,
                "cmd": "uv",
                "args": ["--directory", SCRIPT_DIR, "run", cmd_name],
                "timeout": 300,
                "envs": envs,
                "env_keys": [],
            }
        print("📦 Configured Goose mode: 5 Separate Sub-Servers")

    with open(GOOSE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
    os.chmod(GOOSE_CONFIG_PATH, 0o600)
    print(f"✅ Registered CREPE in Goose config: {GOOSE_CONFIG_PATH}")
    return True


def remove_from_goose_config() -> None:
    """Remove CREPE MCP servers from Goose config."""
    if not GOOSE_CONFIG_PATH.exists():
        return
    try:
        with open(GOOSE_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"⚠️ Warning: Failed to parse {GOOSE_CONFIG_PATH}: {e}")
        return

    extensions = config.get("extensions", {})
    modified = False
    if "crepe" in extensions:
        del extensions["crepe"]
        modified = True
    for sub, _, _ in SUB_SERVERS:
        if sub in extensions:
            del extensions[sub]
            modified = True
    if modified:
        with open(GOOSE_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
        print(f"🧹 Removed CREPE from Goose config: {GOOSE_CONFIG_PATH}")


def update_json_mcp_config(
    config_path: Path,
    client_name: str,
    envs: dict[str, str],
    legacy: bool = False,
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

    if legacy:
        for sub_name, _, _ in SUB_SERVERS:
            mcp_servers.pop(sub_name, None)
        mcp_servers["crepe"] = {
            "command": "uv",
            "args": ["--directory", SCRIPT_DIR, "run", "crepe-mcp"],
            "env": envs,
        }
        print(f"📦 Configured {client_name} mode: Monolith (crepe-mcp, 40 tools)")
    else:
        mcp_servers.pop("crepe", None)
        for sub_name, cmd_name, _ in SUB_SERVERS:
            mcp_servers[sub_name] = {
                "command": "uv",
                "args": ["--directory", SCRIPT_DIR, "run", cmd_name],
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
    for sub, _, _ in SUB_SERVERS:
        if sub in mcp_servers:
            del mcp_servers[sub]
            modified = True
    if modified:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"🧹 Removed CREPE from {client_name} config: {config_path}")


def install_agents_md() -> None:
    """Copy AGENTS.md to ~/.config/goose/CREPE_AGENTS.md if Goose directory exists."""
    if not AGENTS_MD_SRC.is_file():
        return
    GOOSE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(AGENTS_MD_SRC, AGENTS_MD_DST)
    print(f"📖 Installed CREPE agent guide: {AGENTS_MD_DST}")


def remove_agents_md() -> None:
    """Remove CREPE_AGENTS.md on uninstall."""
    if AGENTS_MD_DST.exists():
        AGENTS_MD_DST.unlink()
        print(f"🧹 Removed CREPE agent guide: {AGENTS_MD_DST}")


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

    file_keys = read_keys_file()
    if file_keys:
        print("📄 Auto-detected API keys from keys.md")

    # 1. Dependency checks
    for bin_name, hint in [
        ("uv", "Install uv from https://docs.astral.sh/uv/"),
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

    # 3. Tavily Key
    tavily_key = (
        args.tavily_key
        or os.environ.get("CREPE_TAVILY_API_KEY", os.environ.get("TAVILY_API_KEY", "")).strip()
        or file_keys.get("tavily_key", "")
    )
    if not tavily_key and not args.non_interactive:
        tavily_key = interactive_prompt_secret("Enter Tavily API key (or Enter to skip)")

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
    drawio_path = args.drawio_path or os.environ.get("CREPE_DRAWIO_PATH", "").strip()
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

    # 5b. Semantic Scholar
    ss_key = (
        args.ss_key
        or os.environ.get("CREPE_SEMANTIC_SCHOLAR_API_KEY", "").strip()
        or file_keys.get("ss_key", "")
    )
    if not ss_key and not args.non_interactive:
        ss_key = interactive_prompt_secret("Enter Semantic Scholar API key (or Enter to skip)")

    # 6. Shell Profile
    profile_path = detect_shell_profile()
    update_shell_profile(profile_path, browser_path, libreoffice_path, drawio_path)

    # 7. Build env dict
    envs = {}
    if tavily_key:
        envs["CREPE_TAVILY_API_KEY"] = tavily_key
    if ss_key:
        envs["CREPE_SEMANTIC_SCHOLAR_API_KEY"] = ss_key
    if browser_path:
        envs["CREPE_HEADLESS_BROWSER_PATH"] = browser_path
    if libreoffice_path:
        envs["CREPE_LIBREOFFICE_PATH"] = libreoffice_path
    if drawio_path:
        envs["CREPE_DRAWIO_PATH"] = drawio_path

    # 8. Update Target Configurations
    legacy = getattr(args, "legacy", False)
    if "goose" in targets:
        update_goose_config(envs, legacy=legacy)
        install_agents_md()

    if "agy" in targets:
        update_json_mcp_config(AGY_CONFIG_PATH, "AGY CLI", envs, legacy=legacy)

    if "claude" in targets:
        claude_path = CLAUDE_MACOS_PATH if sys.platform == "darwin" else CLAUDE_LINUX_PATH
        update_json_mcp_config(claude_path, "Claude Desktop", envs, legacy=legacy)
        if CLAUDE_CODE_PATH.exists():
            update_json_mcp_config(CLAUDE_CODE_PATH, "Claude Code", envs, legacy=legacy)

    print("\n🎉 CREPE MCP server installation completed successfully!")
    print(f"💡 To apply environment variables immediately in your current shell:\n    source {profile_path}")


def run_uninstall(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.target)
    print(f"🧹 Uninstalling CREPE MCP Server from {', '.join(targets)} & Shell Profile...\n")
    profile_path = detect_shell_profile()
    remove_shell_profile_block(profile_path)

    if "goose" in targets:
        remove_from_goose_config()
        remove_agents_md()

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
        "--legacy",
        action="store_true",
        help="Install CREPE as a single monolith server (crepe-mcp, 40 tools) instead of 5 separate sub-servers.",
    )
    parser.add_argument(
        "-y",
        "--non-interactive",
        action="store_true",
        help="Do not prompt for missing inputs; accept defaults and flags.",
    )
    parser.add_argument("--tavily-key", type=str, default="", help="Tavily API key.")
    parser.add_argument("--browser-path", type=str, default="", help="Path to Chromium/Chrome binary.")
    parser.add_argument("--libreoffice-path", type=str, default="", help="Path to LibreOffice executable.")
    parser.add_argument("--drawio-path", type=str, default="", help="Path to draw.io executable.")
    parser.add_argument("--ss-key", type=str, default="", help="Semantic Scholar API key.")

    args = parser.parse_args()
    if args.uninstall:
        run_uninstall(args)
    else:
        run_install(args)


if __name__ == "__main__":
    main()
