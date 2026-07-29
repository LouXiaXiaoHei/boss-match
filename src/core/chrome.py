"""Chrome process management — dual-profile support for geek/boss identities."""

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time

import requests

from src.core.constants import (
    DEFAULT_CHROME_PATH,
    DEFAULT_PROFILE_DIR,
    GEEK_CDP_PORT,
    BOSS_CDP_PORT,
    DEFAULT_BASE_DATA_DIR,
)

log = __import__("logging").getLogger(__name__)


def _chrome_data_dir(identity: str) -> str:
    """Return the isolated Chrome profile directory for the given identity."""
    return os.path.join(os.path.expanduser(DEFAULT_BASE_DATA_DIR), f"chrome-profile-{identity}")


def _cdp_port(identity: str) -> int:
    """Return the CDP port for the given identity."""
    return GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT


def is_cdp_ready(cdp_port: int) -> bool:
    try:
        resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def is_chrome_command(command: str) -> bool:
    lower = (command or "").lower()
    return any(token in lower for token in (
        "google chrome", "google-chrome", "chromium", "chrome.exe",
    ))


def normalize_profile_path(path: str) -> str:
    clean = (path or "").strip("\"'")
    if platform.system() == "Windows":
        import ntpath
        return ntpath.normcase(ntpath.normpath(clean))
    return os.path.realpath(os.path.expanduser(clean))


def extract_user_data_dir(command: str) -> str | None:
    match = re.search(r"--user-data-dir=(\"[^\"]+\"|'[^']+'|\S+)", command or "")
    if not match:
        return None
    return match.group(1).strip("\"'")


def iter_chrome_process_commands() -> list[tuple[int, str]]:
    """Return (pid, command line) tuples for Chrome-like browser processes."""
    if platform.system() == "Windows":
        import ntpath
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return []
        if not r.stdout.strip():
            return []
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        processes = []
        for item in data:
            command = item.get("CommandLine") or ""
            if not is_chrome_command(command):
                continue
            try:
                processes.append((int(item.get("ProcessId")), command))
            except (TypeError, ValueError):
                continue
        return processes

    try:
        r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5)
    except Exception:
        return []

    processes = []
    for line in r.stdout.splitlines():
        if not is_chrome_command(line):
            continue
        try:
            pid_text, command = line.strip().split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes


def chrome_pids_for_user_data_dir(user_data_dir: str) -> list[int]:
    pids = []
    real_dir = normalize_profile_path(user_data_dir)
    for pid, command in iter_chrome_process_commands():
        if "--user-data-dir=" not in command:
            continue
        path = extract_user_data_dir(command)
        if path and normalize_profile_path(path) == real_dir:
            pids.append(pid)
    return pids


def chrome_user_data_dirs_for_cdp_port(cdp_port: int) -> list[str]:
    dirs = []
    port_arg = f"--remote-debugging-port={cdp_port}"
    for _pid, command in iter_chrome_process_commands():
        if port_arg not in command:
            continue
        path = extract_user_data_dir(command)
        if path:
            dirs.append(path)
    return dirs


def cdp_port_uses_profile(cdp_port: int, cdp_data_dir: str) -> bool:
    expected = normalize_profile_path(cdp_data_dir)
    return any(
        normalize_profile_path(path) == expected
        for path in chrome_user_data_dirs_for_cdp_port(cdp_port)
    )


def terminate_process(pid: int, force: bool = False) -> None:
    if platform.system() == "Windows":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def stop_cdp_chrome(cdp_data_dir: str) -> int:
    """Stop only Chrome processes that use the given isolated profile."""
    pids = chrome_pids_for_user_data_dir(cdp_data_dir)
    if not pids:
        return 0

    for pid in pids:
        try:
            terminate_process(pid, force=False)
        except ProcessLookupError:
            pass
    for _ in range(10):
        time.sleep(0.5)
        if not chrome_pids_for_user_data_dir(cdp_data_dir):
            return len(pids)

    for pid in chrome_pids_for_user_data_dir(cdp_data_dir):
        try:
            terminate_process(pid, force=True)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return len(pids)


def wait_for_cdp(cdp_port: int, timeout: int = 30) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        if is_cdp_ready(cdp_port):
            return True
    return False


def launch_chrome(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def prepare_cdp_profile(identity: str, copy_login_state: bool = False, reset: bool = False) -> dict:
    """Prepare an isolated persistent Chrome profile for the given identity."""
    cdp_data_dir = _chrome_data_dir(identity)
    cdp_default = os.path.join(cdp_data_dir, "Default")

    if reset and os.path.exists(cdp_data_dir):
        shutil.rmtree(cdp_data_dir)

    os.makedirs(cdp_default, exist_ok=True)

    copied = 0
    if copy_login_state:
        default_profile = DEFAULT_PROFILE_DIR
        default_default = os.path.join(default_profile, "Default")
        cookie_files = []
        for rel_dir in ("", "Network"):
            for name in ("Cookies", "Cookies-journal", "Cookies-wal", "Cookies-shm"):
                rel_path = os.path.join(rel_dir, name) if rel_dir else name
                cookie_files.append((
                    os.path.join(default_default, rel_path),
                    os.path.join(cdp_default, rel_path),
                ))

        copy_files = [
            (os.path.join(default_profile, "Local State"), os.path.join(cdp_data_dir, "Local State"))
        ]
        copy_files.extend(cookie_files)
        for src, dst in copy_files:
            if os.path.exists(src):
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1
                except Exception as e:
                    log.warning(f"复制 {os.path.basename(src)} 失败: {e}")

    return {
        "path": cdp_data_dir,
        "copied": copied,
        "reset": reset,
        "copy_login_state": copy_login_state,
    }


def setup_chrome(identity: str, copy_login_state: bool = False,
                 reset_profile: bool = False, wait_login: bool = True,
                 login_timeout: int = 300) -> dict:
    """Launch Chrome CDP for the given identity. Returns {ok, data/error}."""
    cdp_port = _cdp_port(identity)
    profile = prepare_cdp_profile(identity, copy_login_state=copy_login_state, reset=reset_profile)
    cdp_data_dir = profile["path"]

    if is_cdp_ready(cdp_port):
        if cdp_port_uses_profile(cdp_port, cdp_data_dir):
            return {"ok": True, "data": {"cdp_port": cdp_port, "profile_path": cdp_data_dir, "already_running": True}}
        return {"ok": False, "error": f"端口 {cdp_port} 已被其他 Chrome CDP profile 占用"}

    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        log.info(f"已关闭 {stopped} 个旧的 CDP Chrome 进程")

    cmd = [
        DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    launch_chrome(cmd)

    if not wait_for_cdp(cdp_port):
        return {"ok": False, "error": f"Chrome 启动超时，CDP 端口 {cdp_port} 未就绪"}

    return {"ok": True, "data": {"cdp_port": cdp_port, "profile_path": cdp_data_dir, "already_running": False}}


def stop_chrome(identity: str) -> dict:
    """Stop Chrome for the given identity. Returns {ok, data/error}."""
    cdp_data_dir = _chrome_data_dir(identity)
    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        return {"ok": True, "data": {"stopped": stopped}}
    return {"ok": True, "data": {"stopped": 0, "message": "没有运行中的 Chrome 进程"}}
