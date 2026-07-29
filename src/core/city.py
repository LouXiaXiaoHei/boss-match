"""City code resolution — local static + live BOSS API fallback."""

import json
import os
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.core.constants import CITY_DATA_FILENAME, HOT_CITY_URL, CITY_GROUP_URL

log = logging.getLogger(__name__)

_local_city_map_cache = None
_live_city_maps_cache = None


def _city_data_path() -> str:
    """Return path to data/city_codes.json, compatible with dev and installed modes."""
    repo_data = os.path.join(os.path.dirname(__file__), "..", "..", "data", CITY_DATA_FILENAME)
    if os.path.isfile(repo_data):
        return os.path.normpath(repo_data)
    try:
        from importlib.resources import files
        pkg_data = files(__package__ or "__main__").joinpath("..", "data", CITY_DATA_FILENAME) \
            if __package__ else None
    except Exception:
        pkg_data = None
    if pkg_data is not None and os.path.isfile(str(pkg_data)):
        return str(pkg_data)
    return os.path.normpath(repo_data)


def load_local_city_map() -> tuple[dict[str, str], dict[str, str]]:
    """Read local data/city_codes.json. Returns (name_to_code, code_to_name)."""
    global _local_city_map_cache
    if _local_city_map_cache is not None:
        return _local_city_map_cache
    name_to_code = {}
    try:
        path = _city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for name, code in raw.items():
                if name and code is not None:
                    name_to_code[str(name)] = str(code)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"读取本地城市码表失败: {e}")
    code_to_name = {code: name for name, code in name_to_code.items()}
    _local_city_map_cache = name_to_code, code_to_name
    return _local_city_map_cache


def _fetch_boss_json(url: str, timeout: int = 10) -> dict:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_live_city_maps(timeout: int = 10) -> tuple[dict[str, str], dict[str, str]]:
    """Fetch city maps from BOSS API. Returns (name_to_code, code_to_name)."""
    global _live_city_maps_cache
    if _live_city_maps_cache is not None:
        return _live_city_maps_cache

    name_to_code = {}
    try:
        hot_city_data = _fetch_boss_json(HOT_CITY_URL, timeout=timeout)
        for item in hot_city_data.get("zpData", {}).get("hotCityList", []):
            name = item.get("name")
            code = item.get("code")
            if name and code is not None:
                name_to_code[name] = str(code)

        city_group_data = _fetch_boss_json(CITY_GROUP_URL, timeout=timeout)
        for group in city_group_data.get("zpData", {}).get("cityGroup", []):
            for item in group.get("cityList", []):
                name = item.get("name")
                code = item.get("code")
                if name and code is not None:
                    name_to_code.setdefault(name, str(code))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"加载 BOSS 城市映射失败: {e}")

    code_to_name = {code: name for name, code in name_to_code.items()}
    _live_city_maps_cache = name_to_code, code_to_name
    return _live_city_maps_cache


def resolve_city(city_input: str) -> tuple[str, str]:
    """Resolve city name/code to (name, code).

    Lookup chain: local static → live BOSS API → passthrough.
    """
    if not city_input:
        return city_input, city_input

    local_map, local_reverse = load_local_city_map()
    if city_input in local_map:
        return city_input, local_map[city_input]
    if city_input in local_reverse:
        return local_reverse[city_input], city_input

    live_map, live_reverse = load_live_city_maps()
    if city_input in live_map:
        return city_input, live_map[city_input]
    if city_input in live_reverse:
        return live_reverse[city_input], city_input

    return city_input, city_input


def list_cities(keyword: str | None = None, use_live: bool = True) -> list[dict]:
    """Return list of {name, code} dicts, optionally filtered by keyword."""
    name_to_code = {}
    if use_live:
        live_map, _ = load_live_city_maps()
        name_to_code.update(live_map)
    if not name_to_code:
        local_map, _ = load_local_city_map()
        name_to_code.update(local_map)

    items = sorted(name_to_code.items(), key=lambda kv: kv[0])
    if keyword:
        keyword = keyword.strip()
        items = [(n, c) for n, c in items if keyword in n]
    return [{"name": n, "code": c} for n, c in items]
