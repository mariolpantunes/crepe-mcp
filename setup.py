#!/usr/bin/env python3
"""Setup script for CREPE MCP server integration with Goose Agent (`Option 1: Local Development`).

Uses argparse to provide --install and --uninstall modes, auto-detects system dependencies
(`shutil.which` for Chrome/Chromium and macOS `/Applications` paths), prompts for user feedback
when values are missing, exports environment variables (`CREPE_` prefixed) to the user's shell
profile (`~/.bashrc` / `~/.zshrc`), and updates `~/.config/goose/config.yaml`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Run 'pip install pyyaml' or use 'uv run setup.py'.", file=sys.stderr)
    sys.exit(1)


SCRIPT_DIR = str(Path(__file__).resolve().parent)
GOOSE_CONFIG_DIR = Path.home() / ".config" / "goose"
GOOSE_CONFIG_PATH = GOOSE_CONFIG_DIR / "config.yaml"

# Block delimiters for shell profile injection
PROFILE_BLOCK_START = "# === CREPE MCP Environment Variables ==="
PROFILE_BLOCK_END = "# === End CREPE MCP ==="


def detect_shell_profile() -> Path:
    """Detect the appropriate shell profile (`~/.bashrc` vs `~/.zshrc`) based on $SHELL."""
    shell_env = os.environ.get("SHELL", "").lower()
    if "zsh" in shell_env:
        return Path.home() / ".zshrc"
    if "fish" in shell_env:
        fish_cfg = Path.home() / ".config" / "fish" / "config.fish"
        if fish_cfg.exists():
            return fish_cfg
    # Default to ~/.bashrc for bash or fallback
    bashrc = Path.home() / ".bashrc"
    if bashrc.exists() or not (Path.home() / ".bash_profile").exists():
        return bashrc
    return Path.home() / ".bash_profile"


def find_headless_browser() -> Optional[str]:
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
        found = shutil.which(binary)
        if found:
            return found

    # macOS specific application paths
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


def update_shell_profile(
    profile_path: Path,
    tavily_key: str,
    browser_path: str,
    aspose_license: str,
) -> None:
    """Insert or update exported CREPE variables in the user's shell profile."""
    content = profile_path.read_text("utf-8") if profile_path.exists() else ""
    
    # Remove existing block if present
    pattern = re.compile(
        re.escape(PROFILE_BLOCK_START) + r".*?" + re.escape(PROFILE_BLOCK_END) + r"\n?",
        re.DOTALL,
    )
    content = pattern.sub("", content).rstrip() + "\n\n" if content.strip() else ""

    lines = [PROFILE_BLOCK_START]
    if tavily_key:
        lines.append(f'export CREPE_TAVILY_API_KEY="{tavily_key}"')
    if browser_path:
        lines.append(f'export CREPE_HEADLESS_BROWSER_PATH="{browser_path}"')
    if aspose_license:
        lines.append(f'export CREPE_ASPOSE_LICENSE_PATH="{aspose_license}"')
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


def update_goose_config() -> None:
    """Register or update CREPE MCP server in `~/.config/goose/config.yaml` using Option 1."""
    GOOSE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if GOOSE_CONFIG_PATH.exists():
        try:
            with open(GOOSE_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️ Warning: Failed to parse {GOOSE_CONFIG_PATH}: {e}")
            config = {}
    else:
        config = {}

    extensions = config.setdefault("extensions", {})
    extensions["crepe"] = {
        "enabled": True,
        "type": "stdio",
        "name": "crepe",
        "display_name": "CREPE Presentation Engine",
        "cmd": "uv",
        "args": ["--directory", SCRIPT_DIR, "run", "crepe-mcp"],
        "timeout": 300,
        "env_keys": [
            "CREPE_TAVILY_API_KEY",
            "CREPE_HEADLESS_BROWSER_PATH",
            "CREPE_ASPOSE_LICENSE_PATH",
        ],
    }

    with open(GOOSE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
    print(f"✅ Registered CREPE MCP server in Goose config: {GOOSE_CONFIG_PATH}")


def remove_from_goose_config() -> None:
    """Remove CREPE MCP server entry from `~/.config/goose/config.yaml` upon uninstall."""
    if not GOOSE_CONFIG_PATH.exists():
        return
    try:
        with open(GOOSE_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return

    extensions = config.get("extensions", {})
    if "crepe" in extensions:
        del extensions["crepe"]
        with open(GOOSE_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
        print(f"🧹 Removed CREPE MCP server from Goose config: {GOOSE_CONFIG_PATH}")


def interactive_prompt(prompt_text: str, default_val: str = "") -> str:
    """Ask user for input, showing default if present."""
    display = f"{prompt_text} [{default_val}]: " if default_val else f"{prompt_text}: "
    try:
        ans = input(display).strip()
        return ans if ans else default_val
    except (KeyboardInterrupt, EOFError):
        print("\nInstallation aborted by user.")
        sys.exit(1)


def run_install(args: argparse.Namespace) -> None:
    print(f"🚀 Installing CREPE MCP Server (`Option 1: Local Development` at {SCRIPT_DIR})...\n")

    # 1. Check uv & pandoc
    if not shutil.which("uv"):
        print("⚠️ Warning: `uv` was not found on PATH. Please install uv from https://docs.astral.sh/uv/.")
    if not shutil.which("pandoc"):
        print("⚠️ Warning: `pandoc` was not found on PATH. Required for PDF/PPTX compilation.")

    # 2. Resolve Headless Browser Path
    browser_path = args.browser_path or os.environ.get("CREPE_HEADLESS_BROWSER_PATH", "").strip()
    if not browser_path:
        detected = find_headless_browser()
        if detected:
            print(f"🔍 Auto-detected headless browser: {detected}")
            if not args.non_interactive:
                ans = interactive_prompt("Confirm or override browser path", detected)
                browser_path = ans
            else:
                browser_path = detected
        else:
            print("⚠️ Could not auto-detect Chrome, Chromium, Brave, or Edge binary on your system.")
            if not args.non_interactive:
                browser_path = interactive_prompt(
                    "Enter absolute path to your Chromium-compatible browser executable (or press Enter to skip & use urllib fallback)"
                )
    if not browser_path:
        print("ℹ️ No browser path set. `fetch_webpage` will use instantaneous urllib fallback mode.")

    # 3. Resolve Tavily API Key
    tavily_key = args.tavily_key or os.environ.get("CREPE_TAVILY_API_KEY", os.environ.get("TAVILY_API_KEY", "")).strip()
    if not tavily_key and not args.non_interactive:
        tavily_key = interactive_prompt(
            "Enter your Tavily API key for web search (or press Enter to skip)"
        )
    if not tavily_key:
        print("ℹ️ No Tavily API key set. `web_search` will return a friendly warning when invoked.")

    # 4. Resolve Aspose License Path
    aspose_lic = args.aspose_license or os.environ.get("CREPE_ASPOSE_LICENSE_PATH", "").strip()
    if not aspose_lic and not args.non_interactive:
        aspose_lic = interactive_prompt(
            "Enter Aspose .lic file path for watermark-free PowerPoint export (or press Enter to skip)"
        )
    if not aspose_lic:
        print("ℹ️ Note: Using Aspose evaluation mode. Output PNGs will contain watermarks unless a valid .lic file path is set later.")

    # 5. Export variables to shell profile
    profile_path = detect_shell_profile()
    update_shell_profile(profile_path, tavily_key, browser_path, aspose_lic)

    # 6. Update ~/.config/goose/config.yaml
    update_goose_config()

    print("\n🎉 CREPE MCP server installation completed successfully!")
    print(f"💡 To apply environment variables immediately in your current terminal, run:\n    source {profile_path}")
    print("💡 Goose Agent will automatically inherit `crepe` tools right from this repository (`uv run`).")


def run_uninstall(args: argparse.Namespace) -> None:
    print("🧹 Uninstalling CREPE MCP Server from Goose & Shell Profile...\n")
    profile_path = detect_shell_profile()
    remove_shell_profile_block(profile_path)
    remove_from_goose_config()
    print("\n✅ Uninstalled CREPE MCP server completely.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Setup script for registering CREPE MCP Server with Goose Agent (`Option 1`)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--install",
        action="store_true",
        help="Install and register CREPE MCP in Goose (default mode if no mode flag specified).",
    )
    group.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall and remove CREPE MCP from Goose config and shell profile.",
    )
    parser.add_argument(
        "-y",
        "--non-interactive",
        action="store_true",
        help="Do not prompt for missing inputs; accept defaults and command-line flags.",
    )
    parser.add_argument(
        "--tavily-key",
        type=str,
        default="",
        help="Tavily API key (`CREPE_TAVILY_API_KEY`).",
    )
    parser.add_argument(
        "--browser-path",
        type=str,
        default="",
        help="Path to Chromium/Chrome executable (`CREPE_HEADLESS_BROWSER_PATH`).",
    )
    parser.add_argument(
        "--aspose-license",
        type=str,
        default="",
        help="Path to Aspose `.lic` file (`CREPE_ASPOSE_LICENSE_PATH`).",
    )

    args = parser.parse_args()

    if args.uninstall:
        run_uninstall(args)
    else:
        run_install(args)


if __name__ == "__main__":
    main()
