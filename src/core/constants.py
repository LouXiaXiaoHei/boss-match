"""BOSS zhipin filter parameter mappings."""

import os
import platform
import ntpath

SCALE_MAP = {
    "0-20人": "301", "20-99人": "302", "100-499人": "303",
    "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
}

STAGE_MAP = {
    "未融资": "801", "天使轮": "802", "A轮": "803", "B轮": "804",
    "C轮": "805", "D轮及以上": "806", "已上市": "807", "不需要融资": "808",
}

SALARY_MAP = {
    "不限": "0", "3K以下": "402", "3-5K": "403", "5-10K": "404",
    "10-20K": "405", "20-50K": "406", "50K以上": "407",
}

EXPERIENCE_MAP = {
    "不限": "0", "在校生": "108", "应届生": "102", "经验不限": "101",
    "1年以内": "103", "1-3年": "104",
    "3-5年": "105", "5-10年": "106", "10年以上": "107",
}

DEGREE_MAP = {
    "不限": "0", "初中及以下": "209", "中专/中技": "208", "高中": "206",
    "大专": "202", "本科": "203", "硕士": "204", "博士": "205",
}

INDUSTRY_MAP = {
    "互联网": "1001", "电子商务": "1002", "金融": "1003", "游戏": "1004",
    "企业服务": "1005", "教育培训": "1006", "社交网络": "1007",
    "医疗健康": "1008", "生活服务": "1009", "广告营销": "1010",
}

# API paths
API_JOB_LIST_PATH = "/wapi/zpgeek/search/joblist.json"
HOT_CITY_URL = "https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json"
CITY_GROUP_URL = "https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json"

# Rate limiting
MAX_PAGES = 10
MAX_API_REQUESTS = 500

# Login probe
DEFAULT_CDP_PORT = 9222
GEEK_CDP_PORT = 9222
BOSS_CDP_PORT = 9223
DEFAULT_CITY_INPUT = "上海"
LOGIN_PROBE_QUERY = "Java"
LOGIN_PROBE_CITY = "101020100"
LOGIN_PROBE_TARGETS = (
    ("Java", "101020100"),
    ("AI Agent", "101010100"),
    ("产品经理", "101280600"),
)
LOGIN_PROBE_PAGE_SIZE = 10
LOGIN_PROBE_MAX_INTERVAL = 15
LOGIN_PROBE_MAX_TRANSIENT_ERRORS = 2
LOGIN_RESTRICTED_CODES = {31, 37}
LOGIN_RESTRICTED_MESSAGE_KEYWORDS = (
    "环境存在异常",
    "访问频繁",
    "操作太频繁",
    "安全校验",
    "滑块",
    "验证",
)
DEFAULT_LOGIN_TIMEOUT = 300

# Detail extraction markers
DETAIL_LOGIN_MARKER = "登录查看完整内容"
DETAIL_DESCRIPTION_MARKER = "职位描述"
DETAIL_COMPETITIVENESS_MARKER = "竞争力分析"
DETAIL_SAFETY_MARKER = "BOSS 安全提示"
MIN_DETAIL_TEXT_LENGTH = 120

# Data directories
DEFAULT_BASE_DATA_DIR = "~/.boss-match"


def _get_default_chrome_path() -> str:
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if system == "Windows":
        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(ntpath.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"))
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(ntpath.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return candidates[0] if candidates else "chrome.exe"
    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _get_default_profile_dir() -> str:
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = ntpath.join(os.path.expanduser("~"), "AppData", "Local")
        return ntpath.join(base, "Google", "Chrome", "User Data")
    return os.path.expanduser("~/.config/google-chrome")


DEFAULT_CHROME_PATH = _get_default_chrome_path()
DEFAULT_PROFILE_DIR = _get_default_profile_dir()

# City data
CITY_DATA_FILENAME = "city_codes.json"
