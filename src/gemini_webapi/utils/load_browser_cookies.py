import asyncio
import contextlib
import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar

import orjson as json
from curl_cffi.requests import AsyncSession

from .logger import logger

# `browser-cookie3` ships with the optional `browser` extra, imported lazily below
HAS_BC3 = importlib.util.find_spec("browser_cookie3") is not None


async def load_cdp_cookies(
    domain_name: str = "google.com",
    port: int | None = None,
    verbose: bool = False,
) -> list[dict]:
    """Fetch cookies from a Chromium-based browser running with `--remote-debugging-port`.

    Works on Windows Chrome/Edge v127+ where App-Bound Encryption blocks direct SQLite decryption.
    """
    cdp_port = port or int(os.getenv("GEMINI_CDP_PORT", "9222"))
    base_url = f"http://127.0.0.1:{cdp_port}"

    # Fast local TCP port check (< 150ms) so we never slow down normal init when CDP is not running
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", cdp_port), timeout=0.15
        )
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    except Exception:
        return []

    try:
        async with AsyncSession(timeout=3) as session:
            # Try /json/version first, then /json (page targets)
            ws_url = None
            with contextlib.suppress(Exception):
                resp = await session.get(f"{base_url}/json/version")
                if resp.status_code == 200:
                    info = json.loads(resp.content)
                    ws_url = info.get("webSocketDebuggerUrl")

            if not ws_url:
                resp = await session.get(f"{base_url}/json")
                if resp.status_code == 200:
                    targets = json.loads(resp.content)
                    if isinstance(targets, list):
                        for t in targets:
                            if isinstance(t, dict) and t.get("webSocketDebuggerUrl"):
                                ws_url = t["webSocketDebuggerUrl"]
                                break

            if not ws_url:
                return []

            ws = await session.ws_connect(ws_url)
            try:
                req = json.dumps({"id": 1, "method": "Storage.getCookies"}).decode("utf-8")
                await ws.send_str(req)
                msg, _ = await asyncio.wait_for(ws.recv(), timeout=3.0)
                data = json.loads(msg)
                raw_cookies = data.get("result", {}).get("cookies", [])

                if not raw_cookies:
                    req2 = json.dumps({"id": 2, "method": "Network.getAllCookies"}).decode("utf-8")
                    await ws.send_str(req2)
                    msg2, _ = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    data2 = json.loads(msg2)
                    raw_cookies = data2.get("result", {}).get("cookies", [])
            finally:
                await ws.close()

            now = time.time()
            cookie_list = []
            for c in raw_cookies:
                c_domain = str(c.get("domain", ""))
                if domain_name and domain_name not in c_domain:
                    continue
                expires = c.get("expires")
                if isinstance(expires, (int, float)) and expires > 0 and expires < now:
                    continue
                cookie_list.append(
                    {
                        "name": str(c.get("name", "")),
                        "value": str(c.get("value", "")),
                        "domain": c_domain or f".{domain_name}",
                        "path": str(c.get("path", "/")),
                        "expires": int(expires) if isinstance(expires, (int, float)) and expires > 0 else None,
                    }
                )
            if verbose and cookie_list:
                logger.debug(f"Loaded {len(cookie_list)} cookies from CDP on port {cdp_port}.")
            return cookie_list
    except Exception as e:
        if verbose:
            logger.debug(f"CDP cookie fetch on port {cdp_port} unavailable: {e}")
        return []


def parse_raw_cookies(raw: str) -> dict[str, str]:
    """Parse cookies from JSON (flat dict, nested dict, Cookie-Editor list), Netscape cookies.txt,
    or a raw HTTP `Cookie:` header string (`name=value; name2=value2`).
    """
    text = raw.strip()
    if not text:
        return {}

    # 1. Try JSON formats
    if text[0] in ("{", "["):
        with contextlib.suppress(Exception):
            data = json.loads(text)
            result: dict[str, str] = {}
            if isinstance(data, dict):
                inner = data.get("cookies", data)
                if isinstance(inner, dict):
                    for k, v in inner.items():
                        if isinstance(k, str) and isinstance(v, str) and v:
                            result[k] = v
                elif isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict) and item.get("name") and item.get("value"):
                            result[str(item["name"])] = str(item["value"])
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("name") and item.get("value"):
                        result[str(item["name"])] = str(item["value"])
            if result:
                return result

    # 2. Try Netscape cookies.txt format (tab-separated 7 fields)
    result = {}
    for line in text.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("#") and not line_s.startswith("#HttpOnly_"):
            continue
        parts = line_s.split("\t")
        if len(parts) >= 7:
            name, value = parts[5].strip(), parts[6].strip()
            if name and value:
                result[name] = value
    if result:
        return result

    # 3. Try raw HTTP Cookie header string (`Cookie: a=b; c=d` or `a=b; c=d`)
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    for part in text.replace("\n", ";").split(";"):
        part_s = part.strip()
        if "=" in part_s:
            k, v = part_s.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                result[k] = v
    return result


def load_browser_cookies(
    domain_name: str = "",
    verbose: bool = False,
    browsers: list[str] | None = None,
) -> dict:
    """Try to load cookies from all supported browsers and return combined cookiejar.
    Optionally pass in a domain name to only load cookies from the specified domain.

    Parameters
    ----------
    domain_name : str, optional
        Domain name to filter cookies by, by default will load all cookies without filtering.
    verbose : bool, optional
        If `True`, will print more infomation in logs.
    browsers : list[str], optional
        Optional list of browser names to filter (e.g. `["firefox", "chrome", "edge"]`).

    Returns
    -------
    `dict[str, list[dict]]`
        Dictionary with browser as keys and their cookies for the specified domain as values.
        Each cookie is a dictionary: `{"name": str, "value": str, "domain": str, "path": str, "expires": int}`.
        Only browsers that have cookies for the specified domain will be included.

    """
    try:
        import browser_cookie3 as bc3
    except ImportError:
        if verbose:
            logger.debug(
                "Optional dependency 'browser-cookie3' not found. Skipping browser cookie loading."
            )
        return {}

    browser_fns = [
        bc3.chrome,
        bc3.chromium,
        bc3.opera,
        bc3.opera_gx,
        bc3.brave,
        bc3.edge,
        bc3.vivaldi,
        bc3.firefox,
        bc3.librewolf,
        bc3.safari,
    ]
    if browsers:
        allowed = {b.lower().strip() for b in browsers}
        browser_fns = [fn for fn in browser_fns if fn.__name__.lower() in allowed]
        if not browser_fns:
            return {}

    cookies = {}

    def fetch_cookies(cookie_fn):
        try:
            jar: CookieJar = cookie_fn(domain_name=domain_name)
            if jar:
                cookie_list = []
                for cookie in jar:
                    if not cookie.is_expired():
                        cookie_list.append(
                            {
                                "name": cookie.name,
                                "value": cookie.value,
                                "domain": cookie.domain,
                                "path": cookie.path,
                                "expires": cookie.expires,
                            }
                        )
                if cookie_list:
                    return cookie_fn.__name__, cookie_list
        except bc3.BrowserCookieError:
            pass
        except PermissionError:
            if verbose:
                logger.warning(
                    f"Permission denied while trying to load cookies from {cookie_fn.__name__}."
                )
        except Exception:
            if verbose:
                logger.debug(
                    f"Failed to load cookies from {cookie_fn.__name__} (may not be installed)."
                )
        return None

    with ThreadPoolExecutor(max_workers=len(browser_fns)) as executor:
        futures = [executor.submit(fetch_cookies, fn) for fn in browser_fns]
        for future in as_completed(futures):
            result = future.result()
            if result:
                browser_name, cookie_dict = result
                cookies[browser_name] = cookie_dict

    return cookies
