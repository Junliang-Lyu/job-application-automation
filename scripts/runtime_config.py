"""Private runtime configuration for the job-search scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def browser_profile_dir() -> Path:
    """Return the private application profile; scripts must not launch it."""
    profile = repo_root() / ".browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def discovery_browser_profile_dir() -> Path:
    """Return the script-only profile used for unauthenticated job discovery."""
    profile = repo_root() / ".browser-discovery-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def load_dotenv() -> dict[str, str]:
    path = repo_root() / ".env"
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            values[key] = value.strip('"').strip("'")
    return values


def get_anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or load_dotenv().get(
        "ANTHROPIC_API_KEY"
    )


def get_analysis_llm_config() -> dict[str, str | None]:
    values = load_dotenv()
    provider = (os.environ.get("LLM_PROVIDER") or values.get("LLM_PROVIDER") or "anthropic").lower()
    if provider == "deepseek":
        return {
            "provider": provider,
            "api_key": os.environ.get("DEEPSEEK_API_KEY") or values.get("DEEPSEEK_API_KEY"),
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
        }
    if provider == "anthropic":
        return {
            "provider": provider,
            "api_key": get_anthropic_api_key(),
            "base_url": None,
            "model": "claude-haiku-4-5",
        }
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


def launch_browser_context(playwright, *, headless: bool, profile_dir: Path | None = None):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir or browser_profile_dir()),
        channel="msedge",
        headless=headless,
    )


def launch_discovery_browser_context(playwright, *, headless: bool):
    """Launch only the isolated discovery profile, never the application profile."""
    return launch_browser_context(
        playwright,
        headless=headless,
        profile_dir=discovery_browser_profile_dir(),
    )


def edge_executable() -> str:
    candidate = Path(
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    ) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    return str(candidate) if candidate.exists() else "msedge.exe"


def launch_setup_login():
    return subprocess.Popen([
        edge_executable(),
        f"--user-data-dir={browser_profile_dir()}",
        "https://www.linkedin.com/login",
        "https://smu.joinhandshake.com/stu/jobs",
    ])


def open_jobsearch_workbench():
    """Open verified native Edge profile normally; never add a debugging port."""
    import json
    state=Path(os.environ['LOCALAPPDATA'])/'Microsoft/Edge/User Data/Local State'
    profiles=json.loads(state.read_text(encoding='utf-8'))['profile']['info_cache']
    matches=[directory for directory,info in profiles.items() if info.get('name')=='Job Search']
    if len(matches)!=1:raise ValueError('无法唯一确认 Job Search 配置，请检查 Edge 配置名称')
    subprocess.Popen([edge_executable(),'--profile-directory='+matches[0],'http://127.0.0.1:8765/'])
    return {'message':'已请求在 Job Search 中打开工作台；首次请加载 browser-extension/jobsearch-bridge 扩展后刷新页面'}
