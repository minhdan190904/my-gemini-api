#!/usr/bin/env python3
"""CLI for gemini-webapi"""

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import CurlFollow, CurlHttpVersion

# Run straight from a source checkout, without the package having to be installed
if (_src := str(Path(__file__).resolve().parent / "src")) not in sys.path:
    sys.path.insert(0, _src)

from gemini_webapi import GeminiClient, logger, set_log_level
from gemini_webapi.constants import BROWSER_TYPE, COOKIE_1PSID, COOKIE_1PSIDTS
from gemini_webapi.exceptions import AuthError
from gemini_webapi.types.image import GeneratedImage, WebImage
from gemini_webapi.utils import (
    extract_cookie_value,
    get_cookie_cache_dir,
    get_cookies_cache_path,
    load_browser_cookies,
    load_cdp_cookies,
    parse_raw_cookies,
    rotate_1psidts,
)

# ---------------------------------------------------------------------------
# region - Cookie helpers
# ---------------------------------------------------------------------------


def _parse_expiry(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        with contextlib.suppress(ValueError):
            return int(float(raw))
        with contextlib.suppress(ValueError):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(dt.timestamp())
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
        except Exception:
            return None
    return None


def _load_cookies_with_meta(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies, meta = {}, {}

    def _upsert(name, value, expires_raw=None):
        if not isinstance(name, str) or not name:
            return
        if not isinstance(value, str) or not value:
            return
        cookies[name] = value
        exp = _parse_expiry(expires_raw)
        meta[name] = {
            "expires_raw": expires_raw,
            "expires_epoch": exp,
            "expires_iso": (
                datetime.fromtimestamp(exp, tz=UTC).isoformat().replace("+00:00", "Z")
                if exp is not None
                else None
            ),
        }

    def _handle_obj(item):
        name = item.get("name")
        value = item.get("value")
        expires_raw = (
            item.get("expirationDate")
            or item.get("expires")
            or item.get("expiry")
            or item.get("expiresDate")
        )
        _upsert(name, value, expires_raw=expires_raw)

    # Flat {name: value}
    if isinstance(data, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        for k, v in data.items():
            _upsert(k, v)
        return cookies, meta
    # {"cookies": {name: value}}
    if isinstance(data, dict) and isinstance(data.get("cookies"), dict):
        inner = data["cookies"]
        if all(isinstance(v, str) for v in inner.values()):
            for k, v in inner.items():
                _upsert(k, v)
            return cookies, meta
    # {"cookies": [{name, value}, ...]}
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        for item in data["cookies"]:
            if isinstance(item, dict):
                _handle_obj(item)
        if cookies:
            return cookies, meta
    # [{name, value}, ...]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _handle_obj(item)
        if cookies:
            return cookies, meta

    raise SystemExit("--cookies-json unsupported format.")


def _persist_cookies(cookies_json_path, original, client_cookies, verbose=False):
    merged = dict(original)
    with contextlib.suppress(Exception):
        for cookie in client_cookies.jar:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
            if isinstance(name, str) and isinstance(value, str) and value:
                merged[name] = value
    if merged == original:
        return
    payload = {
        "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "cookies": dict(sorted(merged.items())),
    }
    Path(cookies_json_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if verbose:
        changed = [k for k in merged if merged[k] != original.get(k)]
        logger.debug(f"Persisted cookies ({', '.join(changed)})")


# ---------------------------------------------------------------------------
# region - Client init / teardown
# ---------------------------------------------------------------------------


def _resolve_default_cookies_path(args) -> str | None:
    if getattr(args, "cookies_json", None):
        return args.cookies_json
    for candidate in (
        Path.cwd() / "cookies.json",
        Path(__file__).resolve().parent / "cookies.json",
    ):
        if candidate.is_file():
            args.cookies_json = str(candidate)
            return args.cookies_json
    return None


def _build_client(args):
    json_cookies = {}
    cookies_path = _resolve_default_cookies_path(args)
    if cookies_path and Path(cookies_path).is_file():
        with contextlib.suppress(Exception, SystemExit):
            json_cookies, _ = _load_cookies_with_meta(cookies_path)

    psid = (
        json_cookies.get("__Secure-1PSID")
        or os.getenv("GEMINI_SECURE_1PSID")
        or os.getenv("SECURE_1PSID")
    )
    psidts = (
        json_cookies.get("__Secure-1PSIDTS")
        or os.getenv("GEMINI_SECURE_1PSIDTS")
        or os.getenv("SECURE_1PSIDTS")
    )

    if not psid:
        logger.debug(
            "__Secure-1PSID not in cookies.json/env; falling back to Cache / Browser auto-fetch."
        )
    elif not psidts:
        logger.warning("__Secure-1PSIDTS not found (will rely on cache or rotation if available).")

    extra = {
        k: v for k, v in json_cookies.items() if k not in {"__Secure-1PSID", "__Secure-1PSIDTS"}
    }

    client = GeminiClient(
        secure_1psid=psid,
        secure_1psidts=psidts or None,
        proxy=args.proxy,
        verify=not args.skip_verify,
    )
    if extra:
        client.cookies = extra
    return client, json_cookies


async def _init_client(args):
    if args.verbose:
        set_log_level("DEBUG")
    else:
        set_log_level("WARNING")
    client, json_cookies = _build_client(args)
    timeout = getattr(args, "request_timeout", 300)

    try:
        await client.init(
            timeout=timeout,
            auto_refresh=False,
            verbose=args.verbose,
        )
        # If cookies were auto-fetched and no cookies_json was set yet, default to ./cookies.json
        if not getattr(args, "cookies_json", None) and not getattr(args, "no_persist", False):
            args.cookies_json = str(Path(__file__).resolve().parent / "cookies.json")
        return client, json_cookies
    except AuthError as e:
        raise SystemExit(
            f"Authentication failed: {e}\n"
            "Tip: Run 'python cli.py cookies fetch', 'python cli.py cookies login', "
            "or 'python cli.py cookies paste' to set up your cookies."
        ) from e


async def _cleanup(client, args, json_cookies):
    if args.cookies_json and not args.no_persist:
        _persist_cookies(
            args.cookies_json,
            json_cookies,
            client.cookies,
            verbose=args.verbose,
        )
    await client.close()


# ---------------------------------------------------------------------------
# region - Output helpers
# ---------------------------------------------------------------------------


def _print_images(output):
    if not output or not output.images:
        return
    web = [i for i in output.images if isinstance(i, WebImage)]
    gen = [i for i in output.images if isinstance(i, GeneratedImage)]
    if web:
        print("\n---\nImages:")
        for img in web:
            print(f"  {img.url}  {img.title or ''}")
    if gen:
        print("\n---\nGenerated images:")
        for img in gen:
            print(f"  {img.url}")


def _print_chat_id(output):
    if output and output.metadata:
        cid = output.metadata[0] if output.metadata else None
        if cid:
            print(f"\n---\nChat ID: {cid}")


# ---------------------------------------------------------------------------
# region - Subcommand implementations
# ---------------------------------------------------------------------------


async def cmd_ask(args):
    client, json_cookies = await _init_client(args)
    try:
        files = [args.image] if getattr(args, "image", None) else None
        model = args.model
        if args.no_stream:
            output = await client.generate_content(
                args.prompt,
                files=files,
                model=model,
            )
            print(output.text)
            _print_images(output)
            _print_chat_id(output)
        else:
            output = None
            async for output in client.generate_content_stream(
                args.prompt,
                files=files,
                model=model,
            ):
                if output.text_delta:
                    print(output.text_delta, end="", flush=True)
            if output:
                print()
                _print_images(output)
                _print_chat_id(output)
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_reply(args):
    client, json_cookies = await _init_client(args)
    try:
        latest = await client.fetch_latest_chat_response(args.chat_id)
        if latest:
            chat = client.start_chat(
                metadata=list(latest.metadata),
                cid=args.chat_id,
                rcid=latest.rcid,
                model=args.model,
            )
        else:
            chat = client.start_chat(
                cid=args.chat_id,
                model=args.model,
            )

        if args.no_stream:
            output = await chat.send_message(args.prompt)
            print(output.text)
            _print_images(output)
        else:
            output = None
            async for output in chat.send_message_stream(args.prompt):
                if output.text_delta:
                    print(output.text_delta, end="", flush=True)
            if output:
                print()
                _print_images(output)

        print(f"\n---\nChat ID: {args.chat_id}")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_research_send(args):
    client, json_cookies = await _init_client(args)
    try:
        # The plan and its confirmation are two turns of one conversation. They must share a
        # session, or the confirmation turn is sent without the model the plan was made with
        # and Gemini rejects it as inconsistent with the history.
        chat = client.start_chat(model=args.model)

        plan = await client.create_deep_research_plan(prompt=args.prompt, chat=chat)
        if not plan.cid:
            raise SystemExit("Deep research failed: no chat ID.")

        await client.start_deep_research(plan=plan, chat=chat)

        print("Deep Research task submitted\n")
        if plan.title:
            print(f"  Title:   {plan.title}")
        if plan.eta_text:
            print(f"  ETA:     {plan.eta_text}")
        if plan.steps:
            print("  Steps:")
            for step in plan.steps:
                print(f"    - {step}")
        print(f"\n  Chat ID: {plan.cid}")
        print(f"\n  Use 'research check {plan.cid}' to check")
        print(f"  Use 'research get {plan.cid}' to fetch result")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_research_check(args):
    cid = args.chat_id
    client, json_cookies = await _init_client(args)
    try:
        # A ready report is the only completion signal - Gemini creates no task entity to
        # ask for a progress state. Several turns are scanned because the plan and
        # confirmation replies are model turns too, and the report can attach behind them.
        history = await client.read_chat(cid, limit=10)
        if not history or not history.turns:
            print("  Status: in progress (no response yet)")
            return 0

        for turn in history.turns:
            document = turn.model_output.deep_research_document if turn.model_output else None
            if document and document.ready:
                print("  Status: completed")
                print(f"  Title:  {document.title}")
                print(f"  Report: {len(document.content)} chars, {len(document.sources)} sources")
                print(f"\n  Use 'research get {cid}' for the full report.")
                return 0

        print("  Status: in progress (the report has not been attached yet)")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_research_get(args):
    cid = args.chat_id
    client, json_cookies = await _init_client(args)
    try:
        latest = await client.fetch_latest_chat_response(cid)
        if not latest:
            raise SystemExit(f"No response for chat {cid}. Research may still run.")
        text = latest.text or ""
        output_file = getattr(args, "output", None)
        if output_file:
            p = Path(output_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            print(f"Saved research result to {output_file}")
        else:
            print(text)
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_models(args):
    """List the models this account discovered at initialization."""
    client, json_cookies = await _init_client(args)
    try:
        items = client.list_models()
        if not items:
            print("No models found.")
            return 0

        name_w = max(len("Name"), max(len(m.model_name) for m in items))
        disp_w = max(len("Display"), max(len(m.display_name) for m in items))
        print(f"{'Name':<{name_w}}  {'Display':<{disp_w}}  ID")
        print("-" * (name_w + disp_w + 22))
        for m in items:
            mark = "" if m.is_available else "  (unavailable)"
            print(f"{m.model_name:<{name_w}}  {m.display_name:<{disp_w}}  {m.model_id}{mark}")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_list(args):
    client, json_cookies = await _init_client(args)
    try:
        items = client.list_chats()
        if not items:
            print("No chats found.")
            return 0

        id_w = max(len("ID"), max(len(c.cid) for c in items))
        ti_w = max(len("Title"), max(len(c.title[:50]) for c in items))
        print(f"{'ID':<{id_w}}  {'Title':<{ti_w}}  Updated")
        print("-" * (id_w + ti_w + 12))
        for c in items:
            ts = datetime.fromtimestamp(c.timestamp).strftime("%Y-%m-%d %H:%M")
            print(f"{c.cid:<{id_w}}  {c.title[:50]:<{ti_w}}  {ts}")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_read(args):
    client, json_cookies = await _init_client(args)
    try:
        history = await client.read_chat(
            args.chat_id,
            limit=args.max_turns,
        )
        if not history or not history.turns:
            print(f"No turns for chat {args.chat_id}")
            return 1
        lines = []
        for i, turn in enumerate(history.turns, 1):
            role = turn.role.upper()
            lines.extend((f"--- message {i} ---", f"[{role}] {turn.text}", ""))
        text = "\n".join(lines)
        output_file = getattr(args, "output", None)
        if output_file:
            p = Path(output_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            print(f"Saved chat to {output_file}")
        else:
            print(text)
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_download(args):
    """Download a generated image using authenticated curl_cffi session."""
    json_cookies = {}
    if args.cookies_json:
        json_cookies, _ = _load_cookies_with_meta(args.cookies_json)

    from curl_cffi.requests import AsyncSession

    url = args.url
    # Append =s2048 for full-size if not already specified for googleusercontent.com images
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host == "googleusercontent.com" or host.endswith(".googleusercontent.com"):
        last_segment = parsed.path.rsplit("/", 1)[-1]
        if "=" not in last_segment:
            url += "=s2048"

    output = args.output
    if not output:
        from hashlib import md5

        url_hash = md5(args.url.encode()).hexdigest()[:8]
        output = f"gemini-{url_hash}.png"

    async with AsyncSession(
        impersonate=BROWSER_TYPE,
        cookies=json_cookies,
        proxy=args.proxy,
        allow_redirects=CurlFollow.SAFE,
        http_version=CurlHttpVersion.NONE,
    ) as session:
        resp = await session.get(url)
        if resp.status_code != 200:
            raise SystemExit(f"Download failed: HTTP {resp.status_code}")
        ct = resp.headers.get("content-type", "")
        if "image" not in ct and "octet" not in ct:
            raise SystemExit(f"Unexpected content-type: {ct}")
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(resp.content)
        size_kb = len(resp.content) / 1024
        print(f"Saved to {output} ({size_kb:.1f} KB)")

    return 0


async def cmd_inspect(args):
    """Report the account state the client already gathered during init()."""
    client, json_cookies = await _init_client(args)
    try:
        status = client.account_status
        print("=== Account Diagnostics ===\n")
        print(f"  Status:  {status.name} ({status.value})")
        print(f"           {status.description}")

        abuse = client.abuse_status
        if abuse is None:
            print("\n  Abuse status:  (not reported)")
        elif abuse.get("is_clean"):
            print("\n  Abuse status:  clean")
        else:
            print(
                f"\n  Abuse status:  FLAGGED code={abuse.get('status_code')} "
                f"signal={abuse.get('signal')}"
            )

        if usage := client.usage_info:
            tier = usage.get("tier", {})
            print(f"\n  Plan tier:  {tier.get('label') or '?'} ({tier.get('id')})")
            for key in ("current_5h", "weekly"):
                if metric := usage.get(key):
                    reset_at = metric.get("reset_at")
                    resets = f" (resets {reset_at})" if reset_at else ""
                    print(
                        f"    {metric['window']:<8} {metric.get('usage_percentage')}% used, "
                        f"{metric.get('remaining_credits')} credits left{resets}"
                    )
            if (credits := usage.get("ai_credits_remaining")) is not None:
                print(f"    {'credits':<8} {credits} AI credits remaining")

        print("\n  Quotas:")
        for quota_id, quota in client.quotas.items():
            if quota_id in ("extra", "usage_info"):
                continue
            print(
                f"    {quota.get('label', quota_id):<28} "
                f"{quota.get('remaining')}/{quota.get('total')} remaining "
                f"({quota.get('usage_percentage')}% used)"
            )
        if extra := client.quotas.get("extra", {}).get("default", {}):
            state = "BLOCKED" if extra.get("is_blocked") else "ok"
            print(f"    {'extra features':<28} {state} ({extra.get('usage_percentage')}% used)")

        models = client.list_models() or []
        print(f"\n  Models discovered:  {len(models)}")
        for m in models:
            print(f"    {'*' if m.is_available else '-'} {m.model_name}")

        print()
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


# ---------------------------------------------------------------------------
# region - Cookie Management & Auto-Fetch Flow
# ---------------------------------------------------------------------------


def _save_cookie_file(save_path: str | Path, cookies_dict: dict[str, str]) -> Path:
    target = Path(save_path)
    existing = {}
    if target.is_file():
        with contextlib.suppress(Exception, SystemExit):
            existing, _ = _load_cookies_with_meta(target)
    merged = {**existing, **{k: v for k, v in cookies_dict.items() if v}}
    payload = {
        "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "cookies": dict(sorted(merged.items())),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target.resolve()


async def cmd_cookies_fetch(args):
    """Auto-fetch cookies from local browsers (CDP / browser-cookie3), verify, and save."""
    save_path = args.save or args.cookies_json or "cookies.json"
    browser = (args.browser or "auto").lower()
    cdp_port = getattr(args, "cdp_port", 9222)

    print(f"🔍 Đang quét cookie Google Gemini (chế độ: {browser})...")
    candidates: list[tuple[str, dict[str, str]]] = []

    if browser in ("auto", "cdp"):
        cdp_list = await load_cdp_cookies(domain_name="google.com", port=cdp_port, verbose=True)
        c_map = {c["name"]: c["value"] for c in cdp_list}
        if COOKIE_1PSID in c_map:
            candidates.append(
                (
                    f"CDP (port {cdp_port})",
                    {
                        COOKIE_1PSID: c_map[COOKIE_1PSID],
                        COOKIE_1PSIDTS: c_map.get(COOKIE_1PSIDTS, ""),
                    },
                )
            )

    if browser != "cdp":
        b_filter = None if browser == "auto" else [browser]
        bc3_map = load_browser_cookies(
            domain_name="google.com", verbose=args.verbose, browsers=b_filter
        )
        for b_name, c_list in bc3_map.items():
            c_map = {c["name"]: c["value"] for c in c_list}
            if COOKIE_1PSID in c_map:
                candidates.append(
                    (
                        f"Browser ({b_name})",
                        {
                            COOKIE_1PSID: c_map[COOKIE_1PSID],
                            COOKIE_1PSIDTS: c_map.get(COOKIE_1PSIDTS, ""),
                        },
                    )
                )

    if not candidates:
        print(
            "❌ Không tìm thấy cookie __Secure-1PSID trong trình duyệt.\n"
            "💡 Giải pháp:\n"
            "  1. Chạy: python cli.py cookies login   (Tự mở trình duyệt đăng nhập & hút cookie tự động)\n"
            "  2. Hoặc đăng nhập gemini.google.com trên Firefox rồi chạy lại lệnh này\n"
            "  3. Hoặc chạy: python cli.py cookies paste --raw \"__Secure-1PSID=...; __Secure-1PSIDTS=...\""
        )
        return 1

    for source_name, c_dict in candidates:
        print(f"  -> Tìm thấy từ {source_name} (có 1PSIDTS: {bool(c_dict.get(COOKIE_1PSIDTS))})")
        if args.no_verify:
            saved = _save_cookie_file(save_path, c_dict)
            print(f"✅ Đã lưu cookie vào: {saved}")
            return 0

        print(f"  -> Đang kiểm tra xác thực với Gemini từ {source_name}...")
        client = GeminiClient(
            secure_1psid=c_dict[COOKIE_1PSID],
            secure_1psidts=c_dict.get(COOKIE_1PSIDTS) or None,
            proxy=args.proxy,
            verify=not args.skip_verify,
        )
        try:
            await client.init(timeout=60, auto_refresh=False, verbose=args.verbose)
            active_psid = extract_cookie_value(client.cookies, COOKIE_1PSID) or c_dict[COOKIE_1PSID]
            active_ts = extract_cookie_value(client.cookies, COOKIE_1PSIDTS) or c_dict.get(
                COOKIE_1PSIDTS, ""
            )
            saved = _save_cookie_file(
                save_path, {COOKIE_1PSID: active_psid, COOKIE_1PSIDTS: active_ts}
            )
            models = [m.model_name for m in (client.list_models() or [])]
            print(f"✅ Xác thực thành công từ {source_name}!")
            print(f"   - Đã lưu vào: {saved}")
            print(f"   - Models khả dụng: {', '.join(models) or 'default'}")
            return 0
        except Exception as e:
            print(f"  ⚠️ Nguồn {source_name} không hợp lệ hoặc đã hết hạn: {e}")
        finally:
            await client.close()

    print("❌ Tất cả các nguồn cookie tìm thấy đều đã hết hạn. Hãy chạy 'python cli.py cookies login'.")
    return 1


def _find_chromium_binary(preferred: str = "auto") -> tuple[str, str] | None:
    candidates: list[tuple[str, list[str]]] = [
        (
            "edge",
            [
                shutil.which("msedge") or "",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
        ),
        (
            "chrome",
            [
                shutil.which("chrome") or "",
                shutil.which("google-chrome") or "",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ],
        ),
        (
            "brave",
            [
                shutil.which("brave") or "",
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                os.path.expandvars(
                    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"
                ),
            ],
        ),
    ]
    if preferred != "auto":
        candidates.sort(key=lambda item: 0 if item[0] == preferred else 1)

    for name, paths in candidates:
        for p in paths:
            if p and Path(p).is_file():
                return name, p
    return None


async def cmd_cookies_login(args):
    """Launch a browser with CDP remote debugging, wait for Google login, and auto-capture cookies."""
    save_path = args.save or args.cookies_json or "cookies.json"
    port = args.port
    found = _find_chromium_binary(args.browser)
    if not found:
        raise SystemExit("Không tìm thấy trình duyệt Edge/Chrome/Brave trên máy.")

    b_name, b_exe = found
    profile_dir = Path(tempfile.gettempdir()) / "gemini_webapi_cdp_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"🌐 Đang mở trình duyệt {b_name.upper()} (CDP port {port})...")
    print("👉 Hãy đăng nhập tài khoản Google tại trang https://gemini.google.com vừa mở.")
    print("   Hệ thống sẽ tự động bắt lấy cookie ngay khi bạn đăng nhập thành công!\n")

    proc = subprocess.Popen(
        [
            b_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://gemini.google.com",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                print("⚠️ Trình duyệt đã đóng trước khi lấy được cookie.")
                return 1

            cdp_cookies = await load_cdp_cookies(domain_name="google.com", port=port, verbose=False)
            c_map = {c["name"]: c["value"] for c in cdp_cookies}
            psid = c_map.get(COOKIE_1PSID)
            psidts = c_map.get(COOKIE_1PSIDTS, "")

            if psid and psidts:
                print("🎯 Đã phát hiện __Secure-1PSID & __Secure-1PSIDTS! Đang xác thực với Gemini...")
                client = GeminiClient(
                    secure_1psid=psid,
                    secure_1psidts=psidts,
                    proxy=args.proxy,
                    verify=not args.skip_verify,
                )
                try:
                    await client.init(timeout=45, auto_refresh=False, verbose=args.verbose)
                    saved = _save_cookie_file(
                        save_path, {COOKIE_1PSID: psid, COOKIE_1PSIDTS: psidts}
                    )
                    models = [m.model_name for m in (client.list_models() or [])]
                    print(f"✅ Đăng nhập & Auto-Fetch thành công!")
                    print(f"   - File đã lưu: {saved}")
                    print(f"   - Models: {', '.join(models) or 'default'}")
                    return 0
                except Exception:
                    pass
                finally:
                    await client.close()

            await asyncio.sleep(2.0)

        print("⏰ Đã hết thời gian chờ đăng nhập.")
        return 1
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()


async def cmd_cookies_paste(args):
    """Parse raw cookie header/JSON/Netscape string, verify, and save to cookies.json."""
    save_path = args.save or args.cookies_json or "cookies.json"
    raw = args.raw
    if not raw:
        print("Dán chuỗi Cookie (Header, JSON Cookie-Editor, hoặc __Secure-1PSID=...) rồi nhấn Enter:")
        raw = sys.stdin.readline().strip()
    elif Path(raw).is_file():
        raw = Path(raw).read_text(encoding="utf-8")

    parsed = parse_raw_cookies(raw)
    if COOKIE_1PSID not in parsed:
        raise SystemExit(
            f"❌ Không tìm thấy {COOKIE_1PSID} trong dữ liệu đã nhập. Các khóa tìm thấy: {list(parsed.keys())}"
        )

    saved = _save_cookie_file(save_path, parsed)
    print(f"💾 Đã lưu {len(parsed)} cookie vào: {saved}")

    if not args.no_verify:
        print("🔄 Đang kiểm tra kết nối với Gemini...")
        args.cookies_json = str(saved)
        client, json_cookies = await _init_client(args)
        try:
            models = [m.model_name for m in (client.list_models() or [])]
            print(
                f"✅ Cookie hoạt động tốt! (Nguồn: {client._cookie_source}, Models: {', '.join(models)})"
            )
        finally:
            await _cleanup(client, args, json_cookies)
    return 0


async def cmd_cookies_rotate(args):
    """Force rotate __Secure-1PSIDTS and persist to cookies.json and cache."""
    client, json_cookies = await _init_client(args)
    try:
        new_ts = await rotate_1psidts(
            client._live_client,
            base_psid=client._base_psid,
            verbose=True,
        )
        if new_ts:
            print(f"✅ Đã làm mới __Secure-1PSIDTS: {new_ts[:20]}...")
        else:
            print("ℹ️ Đã gửi yêu cầu rotate (cookie hiện tại vẫn còn mới hoặc vừa được làm mới < 60s).")
        return 0
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_cookies_status(args):
    """Show all cookie sources, cache files, and test authentication status."""
    print("=== Trạng thái Cookie & Nguồn Xác Thực ===\n")
    default_file = _resolve_default_cookies_path(args) or "cookies.json"
    p_file = Path(default_file)
    if p_file.is_file():
        try:
            c_dict, meta = _load_cookies_with_meta(p_file)
            psid = c_dict.get(COOKIE_1PSID, "")
            psidts = c_dict.get(COOKIE_1PSIDTS, "")
            print(f"1. File Cookie ({p_file.resolve()}): TỒN TẠI")
            print(f"   - __Secure-1PSID:   {psid[:16] + '...' if psid else 'THIẾU'}")
            print(f"   - __Secure-1PSIDTS: {psidts[:16] + '...' if psidts else 'THIẾU'}")
            if exp := meta.get(COOKIE_1PSIDTS, {}).get("expires_iso"):
                print(f"   - Hạn 1PSIDTS:      {exp}")
        except Exception as e:
            print(f"1. File Cookie ({p_file}): Lỗi đọc file ({e})")
    else:
        print(f"1. File Cookie ({p_file}): Chưa tạo")

    cache_dir = get_cookie_cache_dir()
    cache_files = list(cache_dir.glob(".cached_cookies_*.json")) if cache_dir.is_dir() else []
    print(f"\n2. Thư mục Auto-Refresh Cache ({cache_dir}): {len(cache_files)} file(s)")
    for cf in cache_files[:5]:
        mtime = datetime.fromtimestamp(cf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"   - {cf.name} (cập nhật lúc: {mtime})")

    print("\n3. Kiểm tra kết nối trực tiếp tới Gemini...")
    try:
        client, json_cookies = await _init_client(args)
        try:
            cache_path = get_cookies_cache_path(client.cookies, base_psid=client._base_psid)
            models = [m.model_name for m in (client.list_models() or [])]
            print(f"   ✅ Kết nối THÀNH CÔNG!")
            print(f"   - Nguồn cookie sử dụng: {client._cookie_source}")
            print(f"   - Trạng thái tài khoản: {client.account_status.name}")
            print(f"   - Cache file gắn kết:   {cache_path}")
            print(f"   - Models khả dụng:      {', '.join(models)}")
        finally:
            await _cleanup(client, args, json_cookies)
    except SystemExit as e:
        print(f"   ❌ Kết nối thất bại: {e}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# region - API Test Suite (Auth / Single / Stream / Multi-turn / Concurrent)
# ---------------------------------------------------------------------------


async def cmd_test(args):
    """Run comprehensive API tests including multi-thread/async concurrency."""
    mode = args.mode
    print(f"🧪 Bắt đầu chạy bộ kiểm thử Gemini WebAPI (chế độ: {mode.upper()})\n")
    t_init = time.perf_counter()
    client, json_cookies = await _init_client(args)
    init_sec = time.perf_counter() - t_init

    results_summary: list[tuple[str, bool, float, str]] = []
    try:
        models = [m.model_name for m in (client.list_models() or [])]
        info_detail = f"source={client._cookie_source}, status={client.account_status.name}, models={len(models)}"
        print(f"[1/5] Auth & Init: ✅ OK ({init_sec:.2f}s) -> {info_detail}")
        results_summary.append(("Auth & Init", True, init_sec, info_detail))

        if mode in ("all", "ask"):
            print("\n[2/5] Test Single-Turn Ask (temporary=True)...")
            t0 = time.perf_counter()
            try:
                out = await client.generate_content(
                    "Trả lời đúng 1 câu ngắn gọn: 2 + 2 bằng mấy?",
                    model=args.model,
                    temporary=True,
                )
                dt = time.perf_counter() - t0
                preview = out.text.strip().replace("\n", " ")[:80]
                print(f"  ✅ OK ({dt:.2f}s): {preview}")
                results_summary.append(("Single-Turn Ask", True, dt, preview))
            except Exception as e:
                dt = time.perf_counter() - t0
                print(f"  ❌ FAILED ({dt:.2f}s): {e}")
                results_summary.append(("Single-Turn Ask", False, dt, str(e)))

        if mode in ("all", "stream"):
            print("\n[3/5] Test Streaming & Time-To-First-Token (TTFT)...")
            t0 = time.perf_counter()
            ttft = None
            chunks = 0
            full_text = ""
            try:
                async for chunk in client.generate_content_stream(
                    "Đếm từ 1 đến 5 trên 1 dòng.",
                    model=args.model,
                    temporary=True,
                ):
                    if chunk.text_delta:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        chunks += 1
                        full_text += chunk.text_delta
                dt = time.perf_counter() - t0
                detail = f"TTFT={ttft or dt:.2f}s, chunks={chunks}, text={full_text.strip()[:50]!r}"
                print(f"  ✅ OK ({dt:.2f}s): {detail}")
                results_summary.append(("Streaming Mode", True, dt, detail))
            except Exception as e:
                dt = time.perf_counter() - t0
                print(f"  ❌ FAILED ({dt:.2f}s): {e}")
                results_summary.append(("Streaming Mode", False, dt, str(e)))

        if mode in ("all", "chat"):
            print("\n[4/5] Test Multi-Turn ChatSession (kiểm tra nhớ ngữ cảnh)...")
            t0 = time.perf_counter()
            chat = client.start_chat(model=args.model)
            try:
                await chat.send_message("Hãy nhớ mật mã của tôi là: BLUE-42. Chỉ trả lời OK.")
                r2 = await chat.send_message("Mật mã tôi vừa nói là gì?")
                dt = time.perf_counter() - t0
                ok = "42" in r2.text or "BLUE" in r2.text.upper()
                preview = r2.text.strip().replace("\n", " ")[:80]
                if chat.cid:
                    with contextlib.suppress(Exception):
                        await client.delete_chat(chat.cid)
                print(f"  {'✅ OK' if ok else '⚠️ WARN'} ({dt:.2f}s): {preview}")
                results_summary.append(("Multi-Turn Chat", ok, dt, preview))
            except Exception as e:
                dt = time.perf_counter() - t0
                print(f"  ❌ FAILED ({dt:.2f}s): {e}")
                results_summary.append(("Multi-Turn Chat", False, dt, str(e)))

        if mode in ("all", "concurrent"):
            total_req = args.requests
            conc = args.concurrency
            print(
                f"\n[5/5] Test Đa Luồng Song Song ({total_req} đoạn chat mới, Semaphore={conc})..."
            )
            sem = asyncio.Semaphore(conc)

            async def _worker(idx: int):
                w_t0 = time.perf_counter()
                async with sem:
                    try:
                        res = await client.generate_content(
                            f"Trả lời 1 dòng ngắn: Số {idx} nhân 10 bằng bao nhiêu?",
                            model=args.model,
                            temporary=True,
                        )
                        w_dt = time.perf_counter() - w_t0
                        text_clean = res.text.strip().replace("\n", " ")[:60]
                        print(f"  - Luồng #{idx}: ✅ {w_dt:.2f}s -> {text_clean}")
                        return True, w_dt
                    except Exception as e:
                        w_dt = time.perf_counter() - w_t0
                        print(f"  - Luồng #{idx}: ❌ {w_dt:.2f}s -> {type(e).__name__}: {e}")
                        return False, w_dt

            t0 = time.perf_counter()
            outcomes = await asyncio.gather(*(_worker(i + 1) for i in range(total_req)))
            dt = time.perf_counter() - t0
            succ = sum(1 for ok, _ in outcomes if ok)
            detail = f"{succ}/{total_req} thành công, tổng thời gian={dt:.2f}s"
            print(f"  => Kết quả Đa Luồng: {detail}")
            results_summary.append(("Concurrent Chats", succ == total_req, dt, detail))

        print("\n" + "=" * 72)
        print(f"{'Bài Test':<22} | {'Kết quả':<8} | {'Thời gian':<10} | Chi tiết")
        print("-" * 72)
        for name, ok, sec, det in results_summary:
            mark = "PASS" if ok else "FAIL"
            print(f"{name:<22} | {mark:<8} | {sec:>8.2f}s | {det}")
        print("=" * 72)
        return 0 if all(ok for _, ok, _, _ in results_summary) else 1
    finally:
        await _cleanup(client, args, json_cookies)


async def cmd_serve(args):
    """Run the local HTTP REST API Server & Web Playground."""
    from server import run_api_server

    cookies_path = _resolve_default_cookies_path(args) or "cookies.json"
    await run_api_server(
        host=args.host,
        port=args.port,
        cookies_path=cookies_path,
        proxy=args.proxy,
        max_concurrency=args.concurrency,
        auto_refresh=not args.no_auto_refresh,
        verbose=args.verbose,
    )
    return 0


# ---------------------------------------------------------------------------
# region - Argparse
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="CLI for gemini-webapi",
    )
    parser.add_argument(
        "--cookies-json",
        default=None,
        help="Path to JSON cookie file",
    )
    parser.add_argument(
        "--proxy",
        default=(
            os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
        ),
    )
    parser.add_argument(
        "--account-index",
        type=int,
        default=None,
        help="Google account index",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name, alias or id (see the 'models' command). Defaults to the account's default",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write updated cookies back",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=300,
        help="Per-request HTTP timeout in seconds",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip SSL certificate verification",
    )

    sub = parser.add_subparsers(dest="command")

    # ask
    p_ask = sub.add_parser("ask", help="Single-turn question")
    p_ask.add_argument("prompt", help="Prompt text")
    p_ask.add_argument("--no-stream", action="store_true")
    p_ask.add_argument("--image", default=None)

    # reply
    p_reply = sub.add_parser("reply", help="Continue chat")
    p_reply.add_argument("chat_id", help="Chat ID (c_...)")
    p_reply.add_argument("prompt", help="Prompt text")
    p_reply.add_argument("--no-stream", action="store_true")

    # research
    p_res = sub.add_parser("research", help="Deep research")
    res_sub = p_res.add_subparsers(dest="research_command")
    p_rs = res_sub.add_parser("send", help="Submit task")
    p_rs.add_argument("--prompt", required=True)
    p_rc = res_sub.add_parser("check", help="Check progress")
    p_rc.add_argument("chat_id")
    p_rg = res_sub.add_parser("get", help="Get result")
    p_rg.add_argument("chat_id")
    p_rg.add_argument("--output", default=None)

    # list
    sub.add_parser("list", help="List chats")

    # read
    p_read = sub.add_parser("read", help="Read chat")
    p_read.add_argument("chat_id")
    p_read.add_argument("--max-turns", type=int, default=30)
    p_read.add_argument("--output", default=None)

    # models
    sub.add_parser("models", help="List the models available to this account")

    # download
    p_dl = sub.add_parser("download", help="Download image")
    p_dl.add_argument("url", help="Image URL")
    p_dl.add_argument("--output", "-o", default=None)

    # inspect
    sub.add_parser("inspect", help="Account probe")

    # cookies (auto-fetch, login, paste, rotate, status)
    p_ck = sub.add_parser("cookies", help="Manage & auto-fetch Gemini cookies")
    ck_sub = p_ck.add_subparsers(dest="cookies_command")

    p_cf = ck_sub.add_parser("fetch", help="Auto-fetch cookies from local browser or CDP")
    p_cf.add_argument(
        "--browser",
        default="auto",
        choices=["auto", "cdp", "firefox", "chrome", "edge", "brave", "opera", "vivaldi"],
        help="Browser source (default: auto)",
    )
    p_cf.add_argument("--cdp-port", type=int, default=9222, help="Chrome/Edge CDP port")
    p_cf.add_argument("--save", default=None, help="Output JSON path (default: cookies.json)")
    p_cf.add_argument("--no-verify", action="store_true", help="Save without verifying")

    p_cl = ck_sub.add_parser(
        "login", help="Open browser with CDP to login & capture cookies automatically"
    )
    p_cl.add_argument(
        "--browser",
        default="auto",
        choices=["auto", "edge", "chrome", "brave"],
        help="Browser to launch (default: auto)",
    )
    p_cl.add_argument("--port", type=int, default=9222, help="CDP remote debugging port")
    p_cl.add_argument("--timeout", type=int, default=300, help="Max wait seconds for login")
    p_cl.add_argument("--save", default=None, help="Output JSON path (default: cookies.json)")

    p_cp = ck_sub.add_parser("paste", help="Parse & save cookies from header string, JSON, or file")
    p_cp.add_argument("--raw", default=None, help="Raw cookie string, JSON, or file path")
    p_cp.add_argument("--save", default=None, help="Output JSON path (default: cookies.json)")
    p_cp.add_argument("--no-verify", action="store_true", help="Save without verifying")

    ck_sub.add_parser("rotate", help="Force rotate __Secure-1PSIDTS now")
    ck_sub.add_parser("status", help="Show cookie sources, cache files, and auth status")

    # test (API test suite & concurrent multi-chat test)
    p_test = sub.add_parser("test", help="Run API test suite (auth, ask, stream, chat, concurrent)")
    p_test.add_argument(
        "--mode",
        default="all",
        choices=["all", "auth", "ask", "stream", "chat", "concurrent"],
        help="Test mode to run (default: all)",
    )
    p_test.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent threads/coroutines for concurrent test (default: 3)",
    )
    p_test.add_argument(
        "--requests",
        type=int,
        default=4,
        help="Total parallel chat requests in concurrent test (default: 4)",
    )

    # serve (HTTP API Server & Web Playground)
    p_srv = sub.add_parser("serve", help="Start local HTTP REST API Server & Web Playground")
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    p_srv.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent requests via Semaphore (default: 3)",
    )
    p_srv.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Disable background cookie auto-refresh",
    )

    return parser


# ---------------------------------------------------------------------------
# region - Dispatch
# ---------------------------------------------------------------------------


async def run(args):
    cmd = args.command
    if cmd == "ask":
        return await cmd_ask(args)
    if cmd == "download":
        return await cmd_download(args)
    if cmd == "inspect":
        return await cmd_inspect(args)
    if cmd == "list":
        return await cmd_list(args)
    if cmd == "models":
        return await cmd_models(args)
    if cmd == "read":
        return await cmd_read(args)
    if cmd == "reply":
        return await cmd_reply(args)
    if cmd == "research":
        rc = getattr(args, "research_command", None)
        if rc == "send":
            return await cmd_research_send(args)
        if rc == "check":
            return await cmd_research_check(args)
        if rc == "get":
            return await cmd_research_get(args)
        raise SystemExit("Usage: research {send|check|get}")
    if cmd == "cookies":
        cc = getattr(args, "cookies_command", None)
        if cc == "fetch":
            return await cmd_cookies_fetch(args)
        if cc in ("status", None):
            return await cmd_cookies_status(args)
        if cc == "login":
            return await cmd_cookies_login(args)
        if cc == "paste":
            return await cmd_cookies_paste(args)
        if cc == "rotate":
            return await cmd_cookies_rotate(args)
        raise SystemExit("Usage: cookies {fetch|login|paste|rotate|status}")
    if cmd == "test":
        return await cmd_test(args)
    if cmd == "serve":
        return await cmd_serve(args)
    raise SystemExit(
        "Usage: cli.py {ask|reply|research|list|read|models|download|inspect|cookies|test|serve}"
    )


def main():
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
