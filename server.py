#!/usr/bin/env python3
"""Full Multimodal Async HTTP API Server & Web Studio for gemini-webapi.

Supports:
- Multimodal File Uploads (Images, PDFs, Docs, Audio, Clipboard Paste)
- AI Image Generation & Editing (Nano Banana / Imagen) with local full-size proxy
- AI Video Generation (Veo) & AI Music/Audio Generation (Lyria) with auto-polling & inline player
- Multi-turn Chat, Chat History Browser, Gemini Gems (System Prompts), Extended Thinking
- Concurrent Multi-Thread Testing (asyncio.Semaphore)
- Cookie Auto-Fetch, Paste & Auto-Refresh
"""

import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from gemini_webapi import GeminiClient, logger
from gemini_webapi.constants import COOKIE_1PSID, COOKIE_1PSIDTS
from gemini_webapi.types.image import GeneratedImage
from gemini_webapi.utils import (
    extract_cookie_value,
    get_cookies_cache_path,
    load_browser_cookies,
    load_cdp_cookies,
    parse_raw_cookies,
    rotate_1psidts,
)

PROJECT_ROOT = Path(__file__).resolve().parent
MEDIA_DIR = PROJECT_ROOT / "media_outputs"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv(env_file: Path | None = None) -> None:
    """Nạp biến môi trường từ file .env trong thư mục dự án (không cần cài thêm thư viện ngoài)."""
    target = env_file or (PROJECT_ROOT / ".env")
    if not target.is_file():
        return
    with contextlib.suppress(Exception):
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()

PLAYGROUND_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Gemini WebAPI Studio — Multimodal, Image, Audio, Video & Multi-Thread</title>
  <style>
    :root {
      --bg: #0b1120;
      --panel: #151f32;
      --panel-alt: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-hover: #0ea5e9;
      --success: #22c55e;
      --warn: #f59e0b;
      --err: #ef4444;
      --purple: #a855f7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 20px;
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text);
      line-height: 1.5;
    }
    .container { max-width: 1360px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--border);
      flex-wrap: wrap; gap: 12px;
    }
    h1 { margin: 0; font-size: 1.45rem; display: flex; align-items: center; gap: 8px; }
    h2 { margin: 0 0 12px 0; font-size: 1.05rem; color: var(--accent); display: flex; align-items: center; gap: 6px; }
    .badge {
      padding: 6px 14px; border-radius: 999px; font-size: 0.82rem; font-weight: 600;
      background: #1e3a8a; color: #93c5fd;
    }
    .badge.ok { background: #14532d; color: #86efac; border: 1px solid #22c55e; }
    .badge.err { background: #7f1d1d; color: #fca5a5; border: 1px solid #ef4444; }

    /* Tabs */
    .tabs {
      display: flex; gap: 8px; margin-bottom: 18px; border-bottom: 1px solid var(--border);
      padding-bottom: 10px; flex-wrap: wrap;
    }
    .tab-btn {
      background: var(--panel); color: var(--muted); border: 1px solid var(--border);
      padding: 8px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.9rem;
    }
    .tab-btn.active {
      background: var(--accent); color: #0b1120; border-color: var(--accent);
    }
    .tab-content { display: none; }
    .tab-content.active { display: block; }

    .layout-studio {
      display: grid; grid-template-columns: 340px 1fr; gap: 18px;
    }
    @media (max-width: 980px) { .layout-studio { grid-template-columns: 1fr; } }

    .card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px; margin-bottom: 16px;
    }
    label { display: block; font-size: 0.82rem; color: var(--muted); margin-bottom: 5px; font-weight: 500; }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%; padding: 9px 12px; border-radius: 8px;
      border: 1px solid var(--border); background: #090e1a; color: var(--text);
      font-family: inherit; font-size: 0.9rem; margin-bottom: 10px;
    }
    textarea { min-height: 95px; resize: vertical; }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    button {
      cursor: pointer; padding: 8px 14px; border-radius: 8px; border: none;
      font-weight: 600; font-size: 0.86rem; background: var(--accent); color: #090e1a;
      transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px;
    }
    button:hover { opacity: 0.92; transform: translateY(-1px); }
    button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
    button.secondary { background: var(--panel-alt); color: var(--text); border: 1px solid var(--border); }
    button.success { background: var(--success); color: #052e16; }
    button.purple { background: var(--purple); color: #fff; }
    button.warn { background: var(--warn); color: #090e1a; }
    button.danger { background: var(--err); color: #fff; }

    /* Mode preset pills */
    .presets { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .preset-pill {
      padding: 6px 12px; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
      background: var(--panel-alt); color: var(--text); border: 1px solid var(--border);
      cursor: pointer;
    }
    .preset-pill:hover, .preset-pill.active {
      border-color: var(--accent); background: #1e3a8a; color: #bae6fd;
    }

    /* File dropzone */
    .dropzone {
      border: 2px dashed var(--border); border-radius: 10px; padding: 14px;
      text-align: center; background: #090e1a; cursor: pointer; margin-bottom: 12px;
      transition: border-color 0.2s;
    }
    .dropzone:hover, .dropzone.dragover { border-color: var(--accent); background: #0f172a; }
    .file-list {
      display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px;
    }
    .file-chip {
      display: flex; align-items: center; gap: 8px; background: var(--panel-alt);
      border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 0.8rem;
    }
    .file-chip img {
      width: 38px; height: 38px; object-fit: cover; border-radius: 4px;
    }
    .file-chip .remove {
      cursor: pointer; color: var(--err); font-weight: bold; font-size: 1rem; line-height: 1;
    }

    /* Output Feed */
    .chat-feed {
      display: flex; flex-direction: column; gap: 14px;
      max-height: 680px; overflow-y: auto; padding-right: 4px;
    }
    .msg-card {
      background: #090e1a; border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    }
    .msg-card.user { border-left: 4px solid var(--accent); }
    .msg-card.assistant { border-left: 4px solid var(--success); }
    .msg-header {
      display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--muted);
      margin-bottom: 8px;
    }
    .thoughts-box {
      background: #131c2e; border-left: 3px solid var(--purple); padding: 8px 12px;
      border-radius: 6px; font-size: 0.82rem; color: #cbd5e1; margin-bottom: 10px;
      white-space: pre-wrap;
    }
    .msg-text { white-space: pre-wrap; font-size: 0.92rem; line-height: 1.6; }

    /* Media Gallery inside message */
    .media-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 12px; margin-top: 12px;
    }
    .media-item {
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
      padding: 10px; display: flex; flex-direction: column; gap: 8px;
    }
    .media-item img, .media-item video {
      width: 100%; border-radius: 8px; max-height: 360px; object-fit: contain; background: #000;
    }
    .media-item audio { width: 100%; }
    .media-caption {
      font-size: 0.78rem; color: var(--muted); display: flex; justify-content: space-between; align-items: center;
    }
    .media-caption a {
      color: var(--accent); text-decoration: none; font-weight: 600;
    }

    pre {
      background: #090e1a; border: 1px solid var(--border); border-radius: 8px;
      padding: 12px; overflow-x: auto; font-size: 0.82rem; white-space: pre-wrap;
      max-height: 400px; overflow-y: auto; margin: 10px 0 0 0;
    }
    .history-item {
      padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
      margin-bottom: 6px; cursor: pointer; background: #090e1a;
      display: flex; justify-content: space-between; align-items: center; font-size: 0.83rem;
    }
    .history-item:hover { border-color: var(--accent); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>✨ Gemini WebAPI Studio</h1>
        <div style="color: var(--muted); font-size: 0.84rem;">
          Đính kèm Ảnh/File • Tạo & Sửa Ảnh AI • Tạo Video (Veo) • Tạo Nhạc/Audio (Lyria) • Deep Research • Đa Luồng
        </div>
      </div>
      <div class="row">
        <div style="display:flex; align-items:center; gap:6px; background:#090e1a; border:1px solid var(--border); padding:4px 10px; border-radius:8px;">
          <span style="font-size:0.78rem; color:var(--muted);">🔐 Secret Key:</span>
          <input type="password" id="apiKeyInput" value="minhdan2k4A@123" style="width:140px; margin:0; padding:4px 8px; font-size:0.8rem;" onchange="localStorage.setItem('gemini_api_key', this.value)" />
        </div>
        <a href="/docs" target="_blank" style="text-decoration:none;">
          <button class="purple">📘 Mở Swagger UI (/docs)</button>
        </a>
        <div id="statusBadge" class="badge">Đang kiểm tra...</div>
      </div>
    </header>

    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('studio', this)">🎨 Studio Đa Phương Thức (Chat / Ảnh / Nhạc / Video)</button>
      <button class="tab-btn" onclick="switchTab('concurrent', this)">🚀 Test Đa Luồng (Concurrent)</button>
      <button class="tab-btn" onclick="switchTab('cookies', this)">🔑 Quản Lý Cookie & Tài Khoản</button>
      <button class="tab-btn" onclick="switchTab('docs', this)">📡 Tài Liệu REST API</button>
    </div>

    <!-- TAB 1: MULTIMODAL STUDIO -->
    <div id="tab-studio" class="tab-content active">
      <div class="layout-studio">
        <!-- Cột cấu hình bên trái -->
        <div>
          <div class="card">
            <h2>⚙️ Cấu hình Phiên & Model</h2>
            <label>Chọn Model (Tự động nhận diện từ tài khoản):</label>
            <select id="chatModel">
              <option value="">Mặc định của tài khoản</option>
            </select>

            <label>Gem / System Prompt (Tùy chọn):</label>
            <div class="row" style="margin-bottom: 8px;">
              <select id="chatGem" style="flex:1; margin-bottom:0;">
                <option value="">-- Không dùng Gem --</option>
              </select>
              <button class="secondary" onclick="loadGems()" title="Tải danh sách Gems">🔄</button>
            </div>

            <label>Chat ID (Để trống = tạo đoạn chat mới):</label>
            <div class="row" style="margin-bottom: 10px;">
              <input type="text" id="chatId" placeholder="c_..." style="flex:1; margin-bottom:0;" />
              <button class="secondary" onclick="resetChatSession()">➕ Tạo mới</button>
            </div>

            <div style="display:flex; flex-direction:column; gap:8px; margin-top:8px;">
              <label style="display:flex; align-items:center; gap:8px; color:var(--accent); cursor:pointer; font-weight:600;">
                <input type="checkbox" id="chatStream" checked style="width:auto; margin:0;" />
                ⚡ Streaming (Hiện từng token/chữ theo thời gian thực như Web)
              </label>
              <label style="display:flex; align-items:center; gap:8px; color:var(--text); cursor:pointer;">
                <input type="checkbox" id="chatTemp" style="width:auto; margin:0;" />
                Temporary Chat (Không lưu vào lịch sử Gemini)
              </label>
              <label style="display:flex; align-items:center; gap:8px; color:var(--text); cursor:pointer;">
                <input type="checkbox" id="chatThinking" style="width:auto; margin:0;" />
                Extended Thinking (Suy luận sâu)
              </label>
              <label style="display:flex; align-items:center; gap:8px; color:var(--text); cursor:pointer;">
                <input type="checkbox" id="chatSaveMedia" checked style="width:auto; margin:0;" />
                Tự động tải Ảnh Full-HD / Video / Nhạc về Server để xem trực tiếp
              </label>
            </div>
          </div>

          <div class="card">
            <div class="row" style="justify-content:space-between; margin-bottom:10px;">
              <h2 style="margin:0;">🕒 Lịch sử Chat trên Gemini</h2>
              <button class="secondary" onclick="loadChatList()" style="padding:4px 10px; font-size:0.78rem;">🔄 Tải lại</button>
            </div>
            <div id="chatHistoryList" style="max-height:240px; overflow-y:auto;">
              <div style="color:var(--muted); font-size:0.82rem;">Bấm "Tải lại" để xem các đoạn chat gần đây.</div>
            </div>
          </div>
        </div>

        <!-- Cột khu vực làm việc chính bên phải -->
        <div>
          <div class="card">
            <h2>⚡ Chọn nhanh Chế độ (Presets)</h2>
            <div class="presets">
              <div class="preset-pill active" onclick="setPreset('chat', this)">💬 Chat / Phân tích Ảnh & File</div>
              <div class="preset-pill" onclick="setPreset('gen_image', this)">🖼️ Tạo Ảnh AI (Imagen / Nano Banana)</div>
              <div class="preset-pill" onclick="setPreset('edit_image', this)">✏️ Sửa Ảnh AI (Đính kèm ảnh + lệnh sửa)</div>
              <div class="preset-pill" onclick="setPreset('gen_music', this)">🎵 Tạo Nhạc / Bài hát (Lyria)</div>
              <div class="preset-pill" onclick="setPreset('gen_video', this)">🎬 Tạo Video AI (Veo)</div>
              <div class="preset-pill" onclick="setPreset('web_image', this)">🌐 Tìm Ảnh từ Web</div>
              <div class="preset-pill" onclick="setPreset('extension', this)">🔌 Gọi Extension (@Youtube / @Gmail)</div>
            </div>

            <!-- Khu vực Upload File / Ảnh -->
            <div class="dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
              <input type="file" id="fileInput" multiple style="display:none" onchange="handleFilesSelected(this.files)" />
              <div style="font-weight:600; color:var(--accent);">📎 Bấm để chọn Ảnh / File (PDF, TXT, Code, Audio...) hoặc Kéo thả / Dán ảnh (Ctrl+V) vào đây</div>
              <div style="font-size:0.78rem; color:var(--muted); margin-top:4px;">
                Hỗ trợ gửi nhiều ảnh và tài liệu cùng lúc để phân tích hoặc chỉnh sửa ảnh bằng AI
              </div>
            </div>
            <div id="filePreviewList" class="file-list"></div>

            <label>Nội dung Prompt gửi tới Gemini:</label>
            <textarea id="chatPrompt" placeholder="Nhập câu hỏi, yêu cầu tạo ảnh, tạo nhạc, tạo video, hoặc câu lệnh phân tích file..."></textarea>

            <div class="row" style="justify-content: space-between;">
              <div class="row">
                <button id="sendBtn" onclick="sendStudioRequest()">🚀 Gửi tới Gemini</button>
                <button class="secondary" onclick="clearFeed()">🧹 Xóa màn hình kết quả</button>
              </div>
              <div id="loadingIndicator" style="display:none; color:var(--warn); font-size:0.86rem; font-weight:600;">
                ⏳ Đang xử lý (Tạo ảnh/nhạc/video có thể mất 15–60 giây)...
              </div>
            </div>
          </div>

          <div class="card">
            <h2>📺 Kết quả trả về (Text, Suy luận, Ảnh Full-HD, Video & Audio Player)</h2>
            <div id="chatFeed" class="chat-feed">
              <div style="color:var(--muted); font-size:0.88rem; text-align:center; padding:24px;">
                Chưa có tin nhắn nào. Hãy chọn một chế độ ở trên, đính kèm ảnh/file hoặc nhập prompt rồi bấm <b>Gửi tới Gemini</b>!
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 2: CONCURRENT TEST -->
    <div id="tab-concurrent" class="tab-content">
      <div class="card">
        <h2>🚀 Kiểm thử Đa Luồng (Concurrent Multi-Chat via Semaphore)</h2>
        <p style="font-size: 0.86rem; color: var(--muted); margin-top: 0;">
          Mỗi luồng sẽ tự động tạo 1 đoạn chat độc lập (<code>temporary=true</code>) chạy song song trên cùng 1 phiên <code>GeminiClient</code>.
        </p>
        <div class="row">
          <div style="flex:1; min-width:200px;">
            <label>Tổng số Request cần bắn:</label>
            <input type="number" id="concTotal" value="4" min="1" max="20" />
          </div>
          <div style="flex:1; min-width:200px;">
            <label>Số luồng chạy đồng thời tối đa (Semaphore):</label>
            <input type="number" id="concLimit" value="3" min="1" max="10" />
          </div>
          <div style="flex:1; min-width:200px;">
            <label>Model:</label>
            <select id="concModel">
              <option value="">Mặc định của tài khoản</option>
            </select>
          </div>
        </div>
        <label>Prompt mẫu (sẽ tự đánh số [Request #1], [Request #2]...):</label>
        <input type="text" id="concPrompt" value="Trả lời đúng 1 câu ngắn gọn: Thủ đô của Việt Nam là gì?" />
        <button onclick="runConcurrentTest()" class="success">⚡ Bắt đầu Test Đa Luồng</button>
        <pre id="concOut">Chưa chạy test.</pre>
      </div>
    </div>

    <!-- TAB 3: COOKIE & ACCOUNT MANAGER -->
    <div id="tab-cookies" class="tab-content">
      <div class="layout-studio" style="grid-template-columns: 1fr 1fr;">
        <div class="card">
          <h2>1. Trạng thái Client, Models & Quotas</h2>
          <div id="healthInfo" style="font-size:0.88rem; line-height:1.7;">Đang tải...</div>
          <div class="row" style="margin-top:14px;">
            <button onclick="checkHealth()" class="secondary">🔄 Làm mới Trạng thái</button>
            <button onclick="autoFetchCookies()" class="success">🔍 Auto-Fetch từ Trình duyệt</button>
            <button onclick="rotateCookies()" class="secondary">♻️ Rotate __Secure-1PSIDTS</button>
          </div>
        </div>

        <div class="card">
          <h2>2. Cập nhật / Dán Cookie Thủ Công</h2>
          <label>Dán JSON, chuỗi Cookie Header, hoặc __Secure-1PSID=...:</label>
          <textarea id="rawCookieInput" style="min-height:120px;" placeholder='{"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}'></textarea>
          <button onclick="saveRawCookies()">💾 Lưu vào cookies.json & Khởi tạo lại</button>
          <pre id="cookieOut" style="display:none;"></pre>
        </div>
      </div>
    </div>

    <!-- TAB 4: API DOCS -->
    <div id="tab-docs" class="tab-content">
      <div class="card">
        <h2>📡 Danh sách REST API Endpoints (Gọi từ Web/App khác)</h2>
        <pre style="max-height:550px;">
1. Kiểm tra trạng thái & danh sách model:
   GET  http://127.0.0.1:8000/health
   GET  http://127.0.0.1:8000/v1/models

2. Gửi Chat Đa phương thức (Text + Upload Ảnh/File + Tạo Ảnh/Nhạc/Video):
   POST http://127.0.0.1:8000/v1/chat
   Body JSON:
   {
     "prompt": "Generate an image of a futuristic Hanoi city at night",
     "model": "gemini-pro",           // hoặc null, "gemini-flash", "gemini-flash-lite", "gemini-argon"
     "chat_id": null,                 // truyền "c_..." nếu muốn chat tiếp đoạn cũ
     "gem": null,                     // ID hoặc tên Gem nếu muốn áp dụng System Prompt
     "temporary": false,              // true = không lưu lịch sử
     "extended_thinking": false,
     "save_media": true,              // true = server tự tải ảnh full-size / video / nhạc về /media/...
     "files": [                       // Tùy chọn: đính kèm nhiều ảnh/file (Base64)
       {
         "name": "sample.png",
         "data": "iVBORw0KGgoAAAANSUhEUgAA..."
       }
     ]
   }

3. Chuẩn OpenAI Chat Completions (Tương thích SDK OpenAI):
   POST http://127.0.0.1:8000/v1/chat/completions
   Body JSON:
   {
     "model": "gemini-flash",
     "messages": [
       {"role": "user", "content": "Xin chào!"}
     ]
   }

4. Quản lý Lịch sử Chat & Gems:
   GET  http://127.0.0.1:8000/v1/chats
   POST http://127.0.0.1:8000/v1/chats/read    {"chat_id": "c_..."}
   POST http://127.0.0.1:8000/v1/chats/delete  {"chat_id": "c_..."}
   GET  http://127.0.0.1:8000/v1/gems</pre>
      </div>
    </div>
  </div>

  <script>
    let attachedFiles = []; // [{name, mime, base64, previewUrl}]
    const savedKey = localStorage.getItem('gemini_api_key');
    if (savedKey) document.getElementById('apiKeyInput').value = savedKey;

    function apiHeaders(extra = {}) {
      const key = (document.getElementById('apiKeyInput')?.value || 'minhdan2k4A@123').trim();
      return {
        'Authorization': 'Bearer ' + key,
        'X-API-Key': key,
        ...extra
      };
    }

    function switchTab(tabId, btn) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + tabId).classList.add('active');
      if (btn) btn.classList.add('active');
    }

    function setPreset(type, pill) {
      document.querySelectorAll('.preset-pill').forEach(el => el.classList.remove('active'));
      if (pill) pill.classList.add('active');
      const promptBox = document.getElementById('chatPrompt');
      if (type === 'chat') {
        promptBox.value = 'Hãy phân tích chi tiết nội dung (hoặc bức ảnh đính kèm) và trả lời bằng tiếng Việt.';
      } else if (type === 'gen_image') {
        promptBox.value = 'Generate an image of a cyberpunk street in Vietnam at night with neon signs, rain reflections, ultra detailed 8k.';
      } else if (type === 'edit_image') {
        promptBox.value = 'Use image generation tool to modify this attached image: change the background to a snowy mountain sunset and add warm cinematic lighting.';
      } else if (type === 'gen_music') {
        promptBox.value = 'Generate a relaxing lo-fi chillhop music track with piano and rain sounds for studying.';
      } else if (type === 'gen_video') {
        promptBox.value = 'Generate a short video of a cute golden retriever puppy running through a field of wildflowers at sunrise, cinematic slow motion.';
      } else if (type === 'web_image') {
        promptBox.value = 'Send me 3 real photos from the web of Ha Long Bay in Vietnam.';
      } else if (type === 'extension') {
        promptBox.value = '@Youtube Tìm giúp tôi các video mới nhất về lập trình Python AI.';
      }
      promptBox.focus();
    }

    // Drag & Drop + File Picker + Paste (Ctrl+V)
    const dropzone = document.getElementById('dropzone');
    dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length) handleFilesSelected(e.dataTransfer.files);
    });

    window.addEventListener('paste', e => {
      const items = (e.clipboardData || {}).items || [];
      const files = [];
      for (const item of items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) handleFilesSelected(files);
    });

    function handleFilesSelected(fileList) {
      for (const file of fileList) {
        const reader = new FileReader();
        reader.onload = e => {
          const dataUrl = e.target.result;
          const base64 = dataUrl.split(',')[1];
          attachedFiles.push({
            name: file.name || ('clipboard_' + Date.now() + '.png'),
            mime: file.type || 'application/octet-stream',
            data: base64,
            previewUrl: file.type.startsWith('image/') ? dataUrl : null
          });
          renderFileChips();
        };
        reader.readAsDataURL(file);
      }
    }

    function removeFile(idx) {
      attachedFiles.splice(idx, 1);
      renderFileChips();
    }

    function renderFileChips() {
      const container = document.getElementById('filePreviewList');
      container.innerHTML = attachedFiles.map((f, i) => `
        <div class="file-chip">
          ${f.previewUrl ? `<img src="${f.previewUrl}" alt="preview" />` : '📄'}
          <span>${f.name}</span>
          <span class="remove" onclick="removeFile(${i})" title="Xóa file">×</span>
        </div>
      `).join('');
    }

    function resetChatSession() {
      document.getElementById('chatId').value = '';
      clearFeed();
    }

    function clearFeed() {
      document.getElementById('chatFeed').innerHTML = '';
    }

    function escapeHtml(str) {
      return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }

    async function sendStudioRequest() {
      const prompt = document.getElementById('chatPrompt').value.trim();
      if (!prompt) {
        alert('Vui lòng nhập Prompt!');
        return;
      }

      const sendBtn = document.getElementById('sendBtn');
      const loader = document.getElementById('loadingIndicator');
      const feed = document.getElementById('chatFeed');
      if (feed.children.length === 1 && feed.children[0].tagName === 'DIV' && !feed.children[0].classList.contains('msg-card')) {
        feed.innerHTML = '';
      }

      const currentFiles = [...attachedFiles];
      const userCard = document.createElement('div');
      userCard.className = 'msg-card user';
      userCard.innerHTML = `
        <div class="msg-header">
          <span>👤 <b>BẠN</b> ${currentFiles.length ? `(Đính kèm ${currentFiles.length} file)` : ''}</span>
          <span>${new Date().toLocaleTimeString()}</span>
        </div>
        <div class="msg-text">${escapeHtml(prompt)}</div>
        ${currentFiles.length ? `<div class="file-list" style="margin-top:8px;">
          ${currentFiles.map(f => f.previewUrl ? `<img src="${f.previewUrl}" style="height:64px;border-radius:6px;border:1px solid var(--border);" />` : `<span class="file-chip">📄 ${escapeHtml(f.name)}</span>`).join('')}
        </div>` : ''}
      `;
      feed.appendChild(userCard);
      feed.scrollTop = feed.scrollHeight;

      sendBtn.disabled = true;
      loader.style.display = 'block';

      const useStream = document.getElementById('chatStream').checked;
      const payload = {
        prompt,
        stream: useStream,
        model: document.getElementById('chatModel').value || null,
        gem: document.getElementById('chatGem').value || null,
        chat_id: document.getElementById('chatId').value.trim() || null,
        temporary: document.getElementById('chatTemp').checked,
        extended_thinking: document.getElementById('chatThinking').checked,
        save_media: document.getElementById('chatSaveMedia').checked,
        files: currentFiles.map(f => ({ name: f.name, data: f.data }))
      };

      // Clear attached files after sending
      attachedFiles = [];
      renderFileChips();

      function buildMediaGalleryHtml(data) {
        const allMediaItems = [];
        for (const img of (data.images || [])) {
          const displayUrl = img.local_url || img.url;
          const badge = img.type === 'generated' ? '🎨 Ảnh AI (Full-HD)' : '🌐 Ảnh Web';
          allMediaItems.push(`
            <div class="media-item">
              <a href="${displayUrl}" target="_blank">
                <img src="${displayUrl}" alt="${escapeHtml(img.alt || img.title)}" loading="lazy" />
              </a>
              <div class="media-caption">
                <span>${badge}</span>
                <a href="${displayUrl}" download>⬇️ Tải ảnh</a>
              </div>
            </div>
          `);
        }
        for (const vid of (data.videos || [])) {
          const vUrl = vid.local_video_url || vid.url;
          const thumbAttr = vid.local_thumb_url ? `poster="${vid.local_thumb_url}"` : '';
          allMediaItems.push(`
            <div class="media-item">
              <video controls preload="metadata" ${thumbAttr} src="${vUrl}"></video>
              <div class="media-caption">
                <span>🎬 Video AI</span>
                <a href="${vUrl}" download>⬇️ Tải MP4</a>
              </div>
            </div>
          `);
        }
        for (const m of (data.media || [])) {
          const aUrl = m.local_audio_url || m.mp3_url;
          const vUrl = m.local_video_url || m.mp4_url;
          const thumb = m.local_thumb_url || m.mp3_thumbnail;
          allMediaItems.push(`
            <div class="media-item">
              ${thumb ? `<img src="${thumb}" style="max-height:180px;" alt="Cover" />` : ''}
              ${aUrl ? `<audio controls src="${aUrl}"></audio>` : ''}
              ${vUrl ? `<video controls preload="metadata" src="${vUrl}"></video>` : ''}
              <div class="media-caption">
                <span>🎵 ${escapeHtml(m.title || 'Audio / Nhạc AI')}</span>
                <span>
                  ${aUrl ? `<a href="${aUrl}" download style="margin-right:8px;">⬇️ MP3</a>` : ''}
                  ${vUrl ? `<a href="${vUrl}" download>⬇️ MP4</a>` : ''}
                </span>
              </div>
            </div>
          `);
        }
        return allMediaItems.length ? `<div class="media-grid">${allMediaItems.join('')}</div>` : '';
      }

      try {
        const res = await fetch('/v1/chat', {
          method: 'POST',
          headers: apiHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload)
        });

        const botCard = document.createElement('div');
        botCard.className = 'msg-card assistant';
        feed.appendChild(botCard);

        if (useStream && res.headers.get('content-type')?.includes('text/event-stream')) {
          botCard.innerHTML = `
            <div class="msg-header">
              <span class="hdr-left">✨ <b>GEMINI</b> (${escapeHtml(payload.model || 'default')}) | <span style="color:var(--accent);">⚡ Đang gõ từng token...</span></span>
              <span class="hdr-right">⏱️ ...</span>
            </div>
            <div class="thoughts-box" style="display:none;"></div>
            <div class="msg-text"></div>
            <div class="media-holder"></div>
          `;
          const hdrLeft = botCard.querySelector('.hdr-left');
          const hdrRight = botCard.querySelector('.hdr-right');
          const thoughtsEl = botCard.querySelector('.thoughts-box');
          const textEl = botCard.querySelector('.msg-text');
          const mediaHolder = botCard.querySelector('.media-holder');

          let accThoughts = '';
          let accText = '';
          const reader = res.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let buffer = '';

          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const block of lines) {
              const dataLine = block.split('\n').find(l => l.startsWith('data: '));
              if (!dataLine) continue;
              const rawJson = dataLine.slice(6).trim();
              if (!rawJson || rawJson === '[DONE]') continue;
              let evt;
              try { evt = JSON.parse(rawJson); } catch { continue; }

              if (evt.type === 'delta') {
                if (evt.thoughts_delta) {
                  accThoughts += evt.thoughts_delta;
                  thoughtsEl.style.display = 'block';
                  thoughtsEl.textContent = '💭 Suy luận (Thoughts):\n' + accThoughts;
                }
                if (evt.text_delta) {
                  accText += evt.text_delta;
                  textEl.textContent = accText;
                }
                feed.scrollTop = feed.scrollHeight;
              } else if (evt.type === 'done') {
                if (evt.chat_id && !payload.temporary) {
                  document.getElementById('chatId').value = evt.chat_id;
                }
                if (evt.thoughts && !accThoughts) {
                  thoughtsEl.style.display = 'block';
                  thoughtsEl.textContent = '💭 Suy luận (Thoughts):\n' + evt.thoughts;
                }
                if (evt.text && !accText) {
                  textEl.textContent = evt.text;
                }
                hdrLeft.innerHTML = `✨ <b>GEMINI</b> (${escapeHtml(payload.model || 'default')}) | Chat ID: <code>${escapeHtml(evt.chat_id || 'Temporary')}</code>`;
                hdrRight.textContent = `⏱️ TTFT: ${evt.ttft_sec ?? '-'}s | Tổng: ${evt.elapsed_sec}s`;
                mediaHolder.innerHTML = buildMediaGalleryHtml(evt);
                feed.scrollTop = feed.scrollHeight;
              } else if (evt.type === 'error') {
                botCard.style.borderLeftColor = 'var(--err)';
                textEl.style.color = '#fca5a5';
                textEl.textContent = '❌ Lỗi: ' + evt.error;
              }
            }
          }
        } else {
          const data = await res.json();
          if (data.error) {
            botCard.style.borderLeftColor = 'var(--err)';
            botCard.innerHTML = `
              <div class="msg-header"><span>❌ <b>LỖI</b></span><span>${new Date().toLocaleTimeString()}</span></div>
              <div class="msg-text" style="color:#fca5a5;">${escapeHtml(data.error)}</div>
            `;
          } else {
            if (data.chat_id && !payload.temporary) {
              document.getElementById('chatId').value = data.chat_id;
            }
            const mediaHtml = buildMediaGalleryHtml(data);
            botCard.innerHTML = `
              <div class="msg-header">
                <span>✨ <b>GEMINI</b> (${escapeHtml(payload.model || 'default')}) | Chat ID: <code>${escapeHtml(data.chat_id || 'Temporary')}</code></span>
                <span>⏱️ ${data.elapsed_sec}s</span>
              </div>
              ${data.thoughts ? `<div class="thoughts-box">💭 <b>Suy luận (Thoughts):</b>\n${escapeHtml(data.thoughts)}</div>` : ''}
              <div class="msg-text">${escapeHtml(data.text)}</div>
              ${mediaHtml}
            `;
          }
          feed.scrollTop = feed.scrollHeight;
        }
      } catch (err) {
        alert('Lỗi kết nối tới server: ' + err);
      } finally {
        sendBtn.disabled = false;
        loader.style.display = 'none';
      }
    }

    async function checkHealth() {
      const badge = document.getElementById('statusBadge');
      const info = document.getElementById('healthInfo');
      try {
        const res = await fetch('/health');
        const data = await res.json();
        if (data.initialized && data.account_status === 'AVAILABLE') {
          badge.className = 'badge ok';
          badge.textContent = '● Đã xác thực (' + (data.cookie_source || 'OK') + ')';
        } else if (data.initialized) {
          badge.className = 'badge err';
          badge.textContent = '○ Chế độ Guest (' + (data.account_status || 'Chưa có Cookie') + ')';
        } else {
          badge.className = 'badge err';
          badge.textContent = '○ Chưa xác thực Cookie';
        }

        // Populate model selects dynamically
        const models = data.models_detail || [];
        if (models.length) {
          for (const selId of ['chatModel', 'concModel']) {
            const sel = document.getElementById(selId);
            const cur = sel.value;
            sel.innerHTML = '<option value="">Mặc định của tài khoản</option>' +
              models.map(m => `<option value="${m.name}" ${!m.available ? 'disabled' : ''}>${m.name} — ${m.display_name}${!m.available ? ' (Unavailable)' : ''}</option>`).join('');
            sel.value = cur;
          }
        }

        info.innerHTML = `
          <div><b>Trạng thái Client:</b> ${data.initialized ? 'Đã khởi tạo (Ready)' : 'Chưa khởi tạo'}</div>
          <div><b>Nguồn Cookie:</b> ${data.cookie_source || 'Chưa có'}</div>
          <div><b>Trạng thái Tài khoản:</b> <code>${data.account_status || 'N/A'}</code></div>
          <div><b>File Cookie:</b> <code>${data.cookies_file || 'cookies.json'}</code> (${data.cookies_file_exists ? 'Đã lưu' : 'Chưa có'})</div>
          <div><b>Thư mục Media đã tải:</b> <code>${data.media_dir || 'media_outputs/'}</code></div>
          <div><b>Giới hạn Đa luồng (Semaphore):</b> Tối đa <b>${data.max_concurrency}</b> luồng (Đang chạy: ${data.active_requests})</div>
          <div><b>Models mở khóa:</b> ${(data.models || []).join(', ') || 'Chưa có'}</div>
          ${data.last_error ? `<div style="color:var(--err);"><b>Lỗi gần nhất:</b> ${escapeHtml(data.last_error)}</div>` : ''}
        `;
      } catch (e) {
        badge.className = 'badge err';
        badge.textContent = 'Lỗi kết nối Server';
        info.textContent = e.toString();
      }
    }

    async function loadGems() {
      const sel = document.getElementById('chatGem');
      const res = await fetch('/v1/gems', { headers: apiHeaders() });
      const data = await res.json();
      if (data.gems) {
        sel.innerHTML = '<option value="">-- Không dùng Gem --</option>' +
          data.gems.map(g => `<option value="${g.id}">${escapeHtml(g.name)} ${g.predefined ? '(System)' : '(Custom)'}</option>`).join('');
      }
    }

    async function loadChatList() {
      const box = document.getElementById('chatHistoryList');
      box.innerHTML = '<div style="color:var(--muted);font-size:0.82rem;">Đang tải...</div>';
      const res = await fetch('/v1/chats', { headers: apiHeaders() });
      const data = await res.json();
      const chats = data.chats || [];
      if (!chats.length) {
        box.innerHTML = '<div style="color:var(--muted);font-size:0.82rem;">Không có đoạn chat nào.</div>';
        return;
      }
      box.innerHTML = chats.map(c => `
        <div class="history-item" onclick="selectHistoryChat('${c.cid}')">
          <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px;" title="${escapeHtml(c.title)}">
            💬 <b>${escapeHtml(c.title || c.cid)}</b>
          </div>
          <span style="color:var(--err);font-weight:bold;padding:0 4px;" onclick="event.stopPropagation();deleteHistoryChat('${c.cid}')" title="Xóa đoạn chat">🗑️</span>
        </div>
      `).join('');
    }

    async function selectHistoryChat(cid) {
      document.getElementById('chatId').value = cid;
      const res = await fetch('/v1/chats/read', {
        method: 'POST',
        headers: apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ chat_id: cid })
      });
      const data = await res.json();
      const feed = document.getElementById('chatFeed');
      feed.innerHTML = '';
      for (const turn of (data.turns || []).reverse()) {
        const card = document.createElement('div');
        card.className = 'msg-card ' + (turn.role === 'user' ? 'user' : 'assistant');
        card.innerHTML = `
          <div class="msg-header"><span><b>${turn.role.toUpperCase()}</b></span><span>${cid}</span></div>
          <div class="msg-text">${escapeHtml(turn.text)}</div>
        `;
        feed.appendChild(card);
      }
    }

    async function deleteHistoryChat(cid) {
      if (!confirm('Xóa đoạn chat ' + cid + ' khỏi lịch sử Gemini?')) return;
      await fetch('/v1/chats/delete', {
        method: 'POST',
        headers: apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ chat_id: cid })
      });
      if (document.getElementById('chatId').value === cid) {
        document.getElementById('chatId').value = '';
      }
      await loadChatList();
    }

    async function autoFetchCookies() {
      const out = document.getElementById('cookieOut');
      out.style.display = 'block';
      out.textContent = 'Đang quét cookie từ trình duyệt...';
      const res = await fetch('/v1/cookies/fetch', { method: 'POST', headers: apiHeaders({'Content-Type': 'application/json'}), body: '{}' });
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      await checkHealth();
    }

    async function rotateCookies() {
      const out = document.getElementById('cookieOut');
      out.style.display = 'block';
      out.textContent = 'Đang xoay vòng __Secure-1PSIDTS...';
      const res = await fetch('/v1/cookies/rotate', { method: 'POST', headers: apiHeaders() });
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      await checkHealth();
    }

    async function saveRawCookies() {
      const raw = document.getElementById('rawCookieInput').value;
      const out = document.getElementById('cookieOut');
      out.style.display = 'block';
      out.textContent = 'Đang lưu và xác thực cookie...';
      const res = await fetch('/v1/cookies/paste', {
        method: 'POST',
        headers: apiHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify({ raw })
      });
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      await checkHealth();
    }

    async function runConcurrentTest() {
      const out = document.getElementById('concOut');
      const requests = parseInt(document.getElementById('concTotal').value || '4', 10);
      const concurrency = parseInt(document.getElementById('concLimit').value || '3', 10);
      const prompt = document.getElementById('concPrompt').value;
      const model = document.getElementById('concModel').value || null;
      out.textContent = `⏳ Đang bắn ${requests} đoạn chat song song (Semaphore = ${concurrency})...`;
      const res = await fetch('/v1/test/concurrent', {
        method: 'POST',
        headers: apiHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify({ prompt, requests, concurrency, model })
      });
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
    }

    checkHealth();
  </script>
</body>
</html>
"""


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", name.strip())
    return cleaned or f"upload_{int(time.time())}.bin"


def _extract_json_from_text(text: str) -> Any:
    """Extract and parse a JSON object or array from model output (strips markdown fences)."""
    raw = (text or "").strip()
    if not raw:
        return None
    # 1. Try direct parse
    with contextlib.suppress(Exception):
        return json.loads(raw)
    # 2. Try ```json ... ``` fenced block
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fenced:
        with contextlib.suppress(Exception):
            return json.loads(fenced.group(1).strip())
    # 3. Try outermost {...} or [...]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            with contextlib.suppress(Exception):
                return json.loads(raw[start : end + 1])
    return None


SWAGGER_UI_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <title>Gemini WebAPI — Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  <style>
    body { margin: 0; background: #fafafa; }
    .topbar-custom {
      background: #0f172a; color: #f8fafc; padding: 12px 24px;
      display: flex; justify-content: space-between; align-items: center;
      font-family: system-ui, -apple-system, sans-serif;
    }
    .topbar-custom a { color: #38bdf8; text-decoration: none; font-weight: 600; }
  </style>
</head>
<body>
  <div class="topbar-custom">
    <div><b>✨ Gemini WebAPI — Tài liệu OpenAPI 3.0 & Swagger UI</b></div>
    <div>
      <a href="/">⬅️ Quay lại Studio UI</a> &nbsp;|&nbsp;
      <a href="/openapi.json" target="_blank">📄 Xem /openapi.json</a>
    </div>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {
      const ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        defaultModelsExpandDepth: 1,
        tryItOutEnabled: true,
        onComplete: () => {
          const defaultKey = localStorage.getItem('gemini_api_key') || 'minhdan2k4A@123';
          ui.preauthorizeApiKey('BearerAuth', defaultKey);
          ui.preauthorizeApiKey('ApiKeyAuth', defaultKey);
        }
      });
      window.ui = ui;
    };
  </script>
</body>
</html>
"""

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "Gemini WebAPI Provider — Multimodal REST Server",
        "version": "2.2.0",
        "description": (
            "Core AI Provider Server đóng gói Google Gemini Web (`gemini.google.com`) cung cấp đầy đủ các API chuẩn cho mọi Backend:\n"
            "- 🔐 **Bảo mật bằng Secret Key (`GEMINI_API_KEY`):** Truyền qua header `Authorization: Bearer <secret_key>` hoặc `X-API-Key: <secret_key>` (Mặc định: `minhdan2k4A@123`)\n"
            "- 💬 **Text & Multimodal Chat (`/v1/chat`):** Chat đơn lượt/đa lượt, đính kèm Ảnh/PDF/Audio Base64, ép trả về JSON (`response_format: 'json'`), Streaming từng token (`stream: true`)\n"
            "- 🖼️ **Image Generation & Editing (`/v1/images/generations`):** Tạo ảnh AI Full-HD (Imagen / Nano Banana) hoặc sửa ảnh đính kèm\n"
            "- 🎬 **Video Generation (`/v1/videos/generations`):** Tạo video AI (Google Veo)\n"
            "- 🎵 **Music & Audio Generation (`/v1/audio/generations`):** Tạo nhạc & bài hát AI (Google Lyria)\n"
            "- 🤖 **OpenAI SDK Compatible (`/v1/chat/completions`, `/v1/models`):** Tương thích trực tiếp thư viện OpenAI SDK"
        ),
    },
    "servers": [{"url": "http://127.0.0.1:8000", "description": "Gemini Provider Server"}],
    "components": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Nhập Secret Key (mặc định: minhdan2k4A@123)",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "Nhập Secret Key (mặc định: minhdan2k4A@123)",
            },
        }
    },
    "security": [{"BearerAuth": []}, {"ApiKeyAuth": []}],
    "paths": {
        "/health": {
            "get": {
                "tags": ["8. System & Load Testing"],
                "summary": "Kiểm tra trạng thái kết nối, cookie, danh sách model & số luồng (Public)",
                "security": [],
                "responses": {
                    "200": {
                        "description": "Thông tin trạng thái hiện tại của GeminiClient",
                        "content": {
                            "application/json": {
                                "example": {
                                    "initialized": True,
                                    "cookie_source": "Cache",
                                    "account_status": "AVAILABLE",
                                    "auth_required": True,
                                    "cookies_file": "D:\\gemini-wrapper-api\\Gemini-API\\cookies.json",
                                    "cookies_file_exists": True,
                                    "max_concurrency": 3,
                                    "active_requests": 0,
                                    "models": [
                                        "gemini-flash-lite",
                                        "gemini-flash",
                                        "gemini-pro",
                                        "gemini-argon",
                                    ],
                                }
                            }
                        },
                    }
                },
            }
        },
        "/v1/chat": {
            "post": {
                "tags": ["1. Text & Multimodal Chat"],
                "summary": "Gửi Prompt đa phương thức (Text, JSON Mode, SSE Token Streaming, Đính kèm Ảnh/PDF/File)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["prompt"],
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "Nội dung câu hỏi hoặc yêu cầu gửi tới Gemini",
                                        "example": "Phân tích ưu nhược điểm của kiến trúc Microservices.",
                                    },
                                    "system_prompt": {
                                        "type": "string",
                                        "nullable": True,
                                        "description": "Chỉ dẫn hệ thống (System Instruction) ghép vào đầu prompt",
                                        "example": "Bạn là chuyên gia kiến trúc phần mềm. Hãy trả lời súc tích bằng tiếng Việt.",
                                    },
                                    "model": {
                                        "type": "string",
                                        "nullable": True,
                                        "description": "Tên model (`gemini-flash-lite`, `gemini-flash`, `gemini-pro`, `gemini-argon` hoặc `null` để dùng mặc định)",
                                        "example": "gemini-flash",
                                    },
                                    "stream": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": "Nếu `true`, trả về luồng Server-Sent Events (`text/event-stream`) gõ từng token theo thời gian thực.",
                                    },
                                    "response_format": {
                                        "type": "string",
                                        "enum": ["text", "json"],
                                        "default": "text",
                                        "description": "Đặt là `json` để ép Gemini trả về JSON sạch và server tự parse vào trường `json` trong response.",
                                    },
                                    "chat_id": {
                                        "type": "string",
                                        "nullable": True,
                                        "description": "Truyền `cid` (ví dụ `c_...`) để chat nối tiếp hội thoại trước, hoặc `null` để tạo phiên mới.",
                                        "example": None,
                                    },
                                    "temporary": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Nếu `true`, không lưu đoạn chat vào lịch sử tài khoản Gemini.",
                                    },
                                    "extended_thinking": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": "Bật chế độ suy luận sâu (Extended Thinking).",
                                    },
                                    "gem": {
                                        "type": "string",
                                        "nullable": True,
                                        "description": "ID hoặc tên Gem nếu muốn áp dụng Gem có sẵn trong tài khoản.",
                                        "example": None,
                                    },
                                    "save_media": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Tự động tải ảnh Full-HD / video / nhạc về `/media/<filename>`.",
                                    },
                                    "files": {
                                        "type": "array",
                                        "description": "Danh sách file/ảnh đính kèm mã hóa Base64",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string", "example": "diagram.png"},
                                                "data": {
                                                    "type": "string",
                                                    "description": "Chuỗi Base64 của file (có hoặc không có tiền tố data:...;base64,)",
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            "examples": {
                                "1_Text_Chat": {
                                    "summary": "1. Chat văn bản chuẩn (stream = false)",
                                    "value": {
                                        "prompt": "Giải thích ngắn gọn định luật bảo toàn năng lượng.",
                                        "model": "gemini-flash",
                                        "temporary": True,
                                        "stream": False,
                                    },
                                },
                                "2_Structured_JSON": {
                                    "summary": "2. Ép trả về cấu trúc JSON (response_format = 'json')",
                                    "value": {
                                        "prompt": "Liệt kê 3 ngôn ngữ lập trình phổ biến theo JSON: {\"languages\": [{\"name\": \"...\", \"use_case\": \"...\"}]}",
                                        "model": "gemini-flash",
                                        "response_format": "json",
                                        "temporary": True,
                                    },
                                },
                                "3_SSE_Streaming": {
                                    "summary": "3. Streaming từng token theo thời gian thực (stream = true)",
                                    "value": {
                                        "prompt": "Viết một đoạn văn ngắn giới thiệu về Trí tuệ nhân tạo.",
                                        "model": "gemini-flash",
                                        "stream": True,
                                        "temporary": True,
                                    },
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Kết quả trả về từ Gemini (hoặc luồng `text/event-stream` nếu `stream: true`)"
                    },
                    "401": {"description": "Sai hoặc thiếu Secret Key (`Authorization` / `X-API-Key`)"},
                },
            }
        },
        "/v1/images/generations": {
            "post": {
                "tags": ["2. Image Generation & Editing (Imagen)"],
                "summary": "Tạo Ảnh AI Full-HD hoặc Chỉnh sửa Ảnh (khi đính kèm ảnh gốc trong `files`)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["prompt"],
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "Mô tả bức ảnh cần tạo hoặc yêu cầu chỉnh sửa ảnh đính kèm",
                                        "example": "A futuristic cyberpunk street in Hanoi at night, neon lights, rain reflection, 8k photorealistic",
                                    },
                                    "model": {
                                        "type": "string",
                                        "nullable": True,
                                        "example": "gemini-pro",
                                    },
                                    "save_media": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Tải ảnh Full-HD (s2048) về `/media/<filename>` trên server",
                                    },
                                    "files": {
                                        "type": "array",
                                        "description": "(Tùy chọn) Đính kèm ảnh gốc Base64 nếu muốn sửa ảnh (Image-to-Image)",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string", "example": "input.png"},
                                                "data": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Danh sách ảnh Full-HD đã tạo (`images` gồm `url` gốc và `local_url` trên server)"
                    }
                },
            }
        },
        "/v1/videos/generations": {
            "post": {
                "tags": ["3. Video Generation (Veo)"],
                "summary": "Tạo Video AI bằng Google Veo (Text-to-Video hoặc Image-to-Video)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["prompt"],
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "Mô tả cảnh chuyển động video cần tạo",
                                        "example": "A cinematic drone shot flying over Ha Long Bay at sunrise with mist over emerald water, slow motion",
                                    },
                                    "model": {
                                        "type": "string",
                                        "nullable": True,
                                        "example": "gemini-pro",
                                    },
                                    "save_media": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Tải file MP4 và thumbnail về `/media/<filename>` trên server",
                                    },
                                    "files": {
                                        "type": "array",
                                        "description": "(Tùy chọn) Ảnh khởi đầu Base64 để tạo video từ ảnh",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "data": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Danh sách video MP4 (`videos` gồm `url`, `local_video_url`, `local_thumb_url`)"
                    }
                },
            }
        },
        "/v1/audio/generations": {
            "post": {
                "tags": ["4. Music & Audio Generation (Lyria)"],
                "summary": "Tạo Nhạc / Bài hát / Audio AI bằng Google Lyria (Trả về MP3, MP4 & Lời bài hát)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["prompt"],
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "Mô tả giai điệu, thể loại nhạc, nhạc cụ hoặc chủ đề bài hát",
                                        "example": "A relaxing lo-fi chillhop beat with warm electric piano and soft vinyl crackle for coding",
                                    },
                                    "model": {
                                        "type": "string",
                                        "nullable": True,
                                        "example": "gemini-pro",
                                    },
                                    "save_media": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Tải file nhạc MP3, video MP4 và ảnh bìa về `/media/<filename>`",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Danh sách bản nhạc (`media` gồm `title`, `mp3_url`, `mp4_url`, `local_audio_url`, `local_video_url`)"
                    }
                },
            }
        },
        "/v1/chat/completions": {
            "post": {
                "tags": ["5. OpenAI SDK Compatible"],
                "summary": "Endpoint tương thích chuẩn OpenAI Chat Completions (Hỗ trợ cả stream: true/false & response_format)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["messages"],
                                "properties": {
                                    "model": {"type": "string", "example": "gemini-flash"},
                                    "stream": {"type": "boolean", "default": False},
                                    "response_format": {
                                        "type": "string",
                                        "enum": ["text", "json"],
                                        "default": "text",
                                    },
                                    "messages": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "role": {"type": "string", "example": "user"},
                                                "content": {
                                                    "type": "string",
                                                    "example": "Xin chào!",
                                                },
                                            },
                                        },
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OpenAI ChatCompletion object"}},
            }
        },
        "/v1/models": {
            "get": {
                "tags": ["5. OpenAI SDK Compatible"],
                "summary": "Lấy danh sách các model khả dụng của tài khoản",
                "responses": {"200": {"description": "Danh sách model chuẩn OpenAI"}},
            }
        },
        "/v1/chats": {
            "get": {
                "tags": ["6. Chat History & Gems"],
                "summary": "Lấy danh sách lịch sử các cuộc hội thoại trên Gemini",
                "responses": {"200": {"description": "Danh sách chat (`cid`, `title`, `timestamp`)"}},
            }
        },
        "/v1/chats/read": {
            "post": {
                "tags": ["6. Chat History & Gems"],
                "summary": "Đọc toàn bộ nội dung các lượt hội thoại của một `chat_id`",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["chat_id"],
                                "properties": {
                                    "chat_id": {"type": "string", "example": "c_1234567890"},
                                    "limit": {"type": "integer", "default": 20},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Danh sách các lượt chat (`turns`)"}},
            }
        },
        "/v1/chats/delete": {
            "post": {
                "tags": ["6. Chat History & Gems"],
                "summary": "Xóa một cuộc hội thoại khỏi lịch sử Gemini",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["chat_id"],
                                "properties": {
                                    "chat_id": {"type": "string", "example": "c_1234567890"},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Trạng thái xóa"}},
            }
        },
        "/v1/gems": {
            "get": {
                "tags": ["6. Chat History & Gems"],
                "summary": "Lấy danh sách Gems (System Prompts) trong tài khoản",
                "responses": {"200": {"description": "Danh sách Gems"}},
            }
        },
        "/v1/cookies/fetch": {
            "post": {
                "tags": ["7. Cookies & Session Management"],
                "summary": "Tự động quét và nạp Cookie từ trình duyệt trên máy chủ",
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "browser": {"type": "string", "default": "auto"},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Kết quả quét cookie"}},
            }
        },
        "/v1/cookies/paste": {
            "post": {
                "tags": ["7. Cookies & Session Management"],
                "summary": "Nạp/Cập nhật Cookie từ chuỗi Header hoặc JSON và lưu vào cookies.json",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["raw"],
                                "properties": {
                                    "raw": {
                                        "type": "string",
                                        "example": "__Secure-1PSID=...; __Secure-1PSIDTS=...",
                                    }
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Trạng thái lưu và khởi tạo lại client"}},
            }
        },
        "/v1/cookies/rotate": {
            "post": {
                "tags": ["7. Cookies & Session Management"],
                "summary": "Ép xoay vòng (rotate) __Secure-1PSIDTS ngay lập tức",
                "responses": {"200": {"description": "Kết quả xoay vòng cookie"}},
            }
        },
        "/v1/test/concurrent": {
            "post": {
                "tags": ["8. System & Load Testing"],
                "summary": "Test bắn nhiều luồng chat song song qua Semaphore",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "example": "Trả lời ngắn gọn 1 câu: Thủ đô của Việt Nam là gì?",
                                    },
                                    "requests": {"type": "integer", "default": 4},
                                    "concurrency": {"type": "integer", "default": 3},
                                    "model": {"type": "string", "nullable": True},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Thống kê thời gian và kết quả từng luồng"}},
            }
        },
    },
}


class GeminiAPIServer:
    """Manages a shared `GeminiClient` instance, concurrency semaphore, media proxy, and HTTP routes."""

    def __init__(
        self,
        cookies_path: str | Path = "cookies.json",
        proxy: str | None = None,
        max_concurrency: int = 3,
        auto_refresh: bool = True,
        api_key: str = "minhdan2k4A@123",
        verbose: bool = False,
    ):
        self.cookies_path = Path(cookies_path)
        self.proxy = proxy
        self.max_concurrency = max(1, max_concurrency)
        self.semaphore = asyncio.Semaphore(self.max_concurrency)
        self.active_requests = 0
        self.auto_refresh = auto_refresh
        self.api_key = (api_key or "").strip()
        self.verbose = verbose
        self.client: GeminiClient | None = None
        self.last_error: str | None = None
        self._init_lock = asyncio.Lock()

    def _verify_api_key(self, headers: dict[str, str]) -> bool:
        if not self.api_key:
            return True
        x_key = headers.get("x-api-key", "").strip()
        if x_key and x_key == self.api_key:
            return True
        auth = headers.get("authorization", "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token == self.api_key:
                return True
        elif auth == self.api_key:
            return True
        return False

    def _load_file_cookies(self) -> dict[str, str]:
        if not self.cookies_path.is_file():
            return {}
        with contextlib.suppress(Exception):
            return parse_raw_cookies(self.cookies_path.read_text(encoding="utf-8"))
        return {}

    def _save_file_cookies(self, cookies_dict: dict[str, str]) -> None:
        existing = self._load_file_cookies()
        merged = {**existing, **{k: v for k, v in cookies_dict.items() if v}}
        payload = {
            "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "cookies": dict(sorted(merged.items())),
        }
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        self.cookies_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def ensure_client(self, force_reinit: bool = False) -> GeminiClient:
        async with self._init_lock:
            if self.client and self.client._running and not force_reinit:
                return self.client

            if self.client:
                with contextlib.suppress(Exception):
                    await self.client.close()
                self.client = None

            file_cookies = self._load_file_cookies()
            psid = file_cookies.get(COOKIE_1PSID)
            psidts = file_cookies.get(COOKIE_1PSIDTS)
            extra = {
                k: v for k, v in file_cookies.items() if k not in {COOKIE_1PSID, COOKIE_1PSIDTS}
            }

            client = GeminiClient(
                secure_1psid=psid,
                secure_1psidts=psidts,
                proxy=self.proxy,
            )
            if extra:
                client.cookies = extra

            try:
                await client.init(
                    timeout=360,
                    auto_close=False,
                    auto_refresh=self.auto_refresh,
                    verbose=self.verbose,
                )
                self.client = client
                self.last_error = None

                active_psid = extract_cookie_value(client.cookies, COOKIE_1PSID)
                active_psidts = extract_cookie_value(client.cookies, COOKIE_1PSIDTS)
                if active_psid:
                    to_save = {COOKIE_1PSID: active_psid}
                    if active_psidts:
                        to_save[COOKIE_1PSIDTS] = active_psidts
                    self._save_file_cookies(to_save)

                return client
            except Exception as e:
                self.last_error = str(e)
                raise

    async def handle_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=20.0)
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
            if len(parts) < 2:
                return
            method, raw_path = parts[0].upper(), parts[1]
            parsed_url = urlparse(raw_path)
            path = parsed_url.path
            _ = parse_qs(parsed_url.query)

            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=15.0)
                if not line or line in (b"\r\n", b"\n"):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_length = int(headers.get("content-length", "0"))
            body_bytes = b""
            if content_length > 0:
                body_bytes = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=120.0
                )

            if method == "OPTIONS":
                await self._send_json(writer, 200, {"ok": True})
                return

            if method == "GET" and path == "/":
                await self._send_html(writer, 200, PLAYGROUND_HTML)
                return

            if method == "GET" and path in ("/docs", "/swagger"):
                await self._send_html(writer, 200, SWAGGER_UI_HTML)
                return

            if method == "GET" and path == "/openapi.json":
                await self._send_json(writer, 200, OPENAPI_SPEC)
                return

            if method == "GET" and path.startswith("/media/"):
                filename = Path(unquote(path.removeprefix("/media/"))).name
                await self._serve_media_file(writer, filename)
                return

            if method == "GET" and path == "/health":
                await self._handle_health(writer)
                return

            # Mọi endpoint /v1/* đều bắt buộc xác thực bằng Secret Key (Authorization: Bearer <key> hoặc X-API-Key: <key>)
            if path.startswith("/v1/") and not self._verify_api_key(headers):
                await self._send_json(
                    writer,
                    401,
                    {
                        "error": (
                            "Unauthorized: Sai hoặc thiếu Secret Key. "
                            "Hãy truyền header 'Authorization: Bearer <secret_key>' hoặc 'X-API-Key: <secret_key>'."
                        )
                    },
                )
                return

            if method == "GET" and path == "/v1/models":
                await self._handle_models(writer)
                return

            if method == "GET" and path == "/v1/chats":
                await self._handle_list_chats(writer)
                return

            if method == "GET" and path == "/v1/gems":
                await self._handle_list_gems(writer)
                return

            body_json: dict[str, Any] = {}
            if body_bytes:
                with contextlib.suppress(Exception):
                    body_json = json.loads(body_bytes.decode("utf-8"))

            if method == "POST" and path == "/v1/chat":
                await self._handle_chat(writer, body_json)
                return

            if method == "POST" and path == "/v1/images/generations":
                await self._handle_images_generate(writer, body_json)
                return

            if method == "POST" and path == "/v1/videos/generations":
                await self._handle_videos_generate(writer, body_json)
                return

            if method == "POST" and path in ("/v1/audio/generations", "/v1/music/generations"):
                await self._handle_audio_generate(writer, body_json)
                return

            if method == "POST" and path == "/v1/chats/read":
                await self._handle_read_chat(writer, body_json)
                return

            if method == "POST" and path == "/v1/chats/delete":
                await self._handle_delete_chat(writer, body_json)
                return

            if method == "POST" and path == "/v1/chat/completions":
                await self._handle_openai_chat(writer, body_json)
                return

            if method == "POST" and path == "/v1/test/concurrent":
                await self._handle_concurrent_test(writer, body_json)
                return

            if method == "POST" and path == "/v1/cookies/fetch":
                await self._handle_cookies_fetch(writer, body_json)
                return

            if method == "POST" and path == "/v1/cookies/paste":
                await self._handle_cookies_paste(writer, body_json)
                return

            if method == "POST" and path == "/v1/cookies/rotate":
                await self._handle_cookies_rotate(writer)
                return

            await self._send_json(writer, 404, {"error": f"Not found: {method} {path}"})
        except Exception as e:
            logger.debug(f"Server request error: {e}")
            with contextlib.suppress(Exception):
                await self._send_json(writer, 500, {"error": str(e)})
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _serve_media_file(self, writer: asyncio.StreamWriter, filename: str) -> None:
        target = (MEDIA_DIR / filename).resolve()
        if not target.is_file() or target.parent != MEDIA_DIR.resolve():
            await self._send_json(writer, 404, {"error": "Media file not found"})
            return
        mime, _ = mimetypes.guess_type(str(target))
        content_type = mime or "application/octet-stream"
        data = target.read_bytes()
        header = (
            "HTTP/1.1 200 OK\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(data)}\r\n"
            "Cache-Control: public, max-age=86400\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + data)
        await writer.drain()

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        if not self.client or not self.client._running:
            with contextlib.suppress(Exception):
                await self.ensure_client()

        client = self.client
        initialized = bool(client and client._running)
        raw_models = (client.list_models() or []) if initialized and client else []
        models = [m.model_name for m in raw_models]
        models_detail = [
            {
                "name": m.model_name,
                "display_name": m.display_name,
                "available": m.is_available,
            }
            for m in raw_models
        ]
        cache_path = (
            str(get_cookies_cache_path(client.cookies, base_psid=client._base_psid))
            if initialized and client
            else None
        )
        await self._send_json(
            writer,
            200,
            {
                "initialized": initialized,
                "cookie_source": getattr(client, "_cookie_source", None) if initialized else None,
                "account_status": client.account_status.name if initialized and client else None,
                "cookies_file": str(self.cookies_path.resolve()),
                "cookies_file_exists": self.cookies_path.is_file(),
                "media_dir": str(MEDIA_DIR),
                "cache_file": cache_path,
                "max_concurrency": self.max_concurrency,
                "active_requests": self.active_requests,
                "models": models,
                "models_detail": models_detail,
                "last_error": self.last_error,
            },
        )

    async def _handle_models(self, writer: asyncio.StreamWriter) -> None:
        client = await self.ensure_client()
        models = client.list_models() or []
        await self._send_json(
            writer,
            200,
            {
                "object": "list",
                "data": [
                    {
                        "id": m.model_name,
                        "object": "model",
                        "display_name": m.display_name,
                        "model_id": m.model_id,
                        "is_available": m.is_available,
                    }
                    for m in models
                ],
            },
        )

    async def _handle_list_chats(self, writer: asyncio.StreamWriter) -> None:
        client = await self.ensure_client()
        chats = client.list_chats() or []
        await self._send_json(
            writer,
            200,
            {
                "chats": [
                    {"cid": c.cid, "title": c.title, "timestamp": c.timestamp} for c in chats
                ]
            },
        )

    async def _handle_read_chat(self, writer: asyncio.StreamWriter, body: dict[str, Any]) -> None:
        cid = str(body.get("chat_id") or "").strip()
        if not cid:
            await self._send_json(writer, 400, {"error": "Missing chat_id"})
            return
        client = await self.ensure_client()
        history = await client.read_chat(cid, limit=int(body.get("limit", 20)))
        turns = []
        if history and history.turns:
            for t in history.turns:
                turns.append({"role": t.role, "text": t.text})
        await self._send_json(writer, 200, {"chat_id": cid, "turns": turns})

    async def _handle_delete_chat(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        cid = str(body.get("chat_id") or "").strip()
        if not cid:
            await self._send_json(writer, 400, {"error": "Missing chat_id"})
            return
        client = await self.ensure_client()
        await client.delete_chat(cid)
        await self._send_json(writer, 200, {"ok": True, "deleted": cid})

    async def _handle_list_gems(self, writer: asyncio.StreamWriter) -> None:
        client = await self.ensure_client()
        await client.fetch_gems(include_hidden=False)
        gems = [
            {
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "predefined": g.predefined,
            }
            for g in (client.gems or []).values()
        ]
        await self._send_json(writer, 200, {"gems": gems})

    async def _extract_and_save_media(
        self, output: Any, save_media: bool = True
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        images_out: list[dict[str, Any]] = []
        for img in (output.images if output else None) or []:
            is_gen = isinstance(img, GeneratedImage)
            local_url = None
            if save_media:
                with contextlib.suppress(Exception):
                    saved_path = await img.save(path=str(MEDIA_DIR), full_size=True)
                    if saved_path:
                        local_url = f"/media/{Path(saved_path).name}"
            images_out.append(
                {
                    "url": img.url,
                    "local_url": local_url,
                    "title": img.title,
                    "alt": img.alt,
                    "type": "generated" if is_gen else "web",
                }
            )

        videos_out: list[dict[str, Any]] = []
        for vid in (output.videos if output else None) or []:
            local_video_url = None
            local_thumb_url = None
            if save_media:
                with contextlib.suppress(Exception):
                    saved_dict = await vid.save(path=str(MEDIA_DIR))
                    if isinstance(saved_dict, dict):
                        if v_path := saved_dict.get("video"):
                            local_video_url = f"/media/{Path(v_path).name}"
                        if t_path := saved_dict.get("video_thumbnail"):
                            local_thumb_url = f"/media/{Path(t_path).name}"
            videos_out.append(
                {
                    "url": vid.url,
                    "thumbnail": getattr(vid, "thumbnail", ""),
                    "local_video_url": local_video_url,
                    "local_thumb_url": local_thumb_url,
                }
            )

        media_out: list[dict[str, Any]] = []
        for m in (output.media if output else None) or []:
            local_audio_url = None
            local_video_url = None
            local_thumb_url = None
            if save_media:
                with contextlib.suppress(Exception):
                    saved_dict = await m.save(path=str(MEDIA_DIR), download_type="both")
                    if isinstance(saved_dict, dict):
                        if a_path := saved_dict.get("audio"):
                            local_audio_url = f"/media/{Path(a_path).name}"
                        if v_path := saved_dict.get("video"):
                            local_video_url = f"/media/{Path(v_path).name}"
                        if t_path := (
                            saved_dict.get("audio_thumbnail")
                            or saved_dict.get("video_thumbnail")
                        ):
                            local_thumb_url = f"/media/{Path(t_path).name}"
            media_out.append(
                {
                    "title": m.title,
                    "mp3_url": m.mp3_url,
                    "mp4_url": m.mp4_url,
                    "mp3_thumbnail": m.mp3_thumbnail,
                    "local_audio_url": local_audio_url,
                    "local_video_url": local_video_url,
                    "local_thumb_url": local_thumb_url,
                }
            )
        return images_out, videos_out, media_out

    async def _send_sse_headers(self, writer: asyncio.StreamWriter) -> None:
        header = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "Cache-Control: no-cache, no-transform\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            "Access-Control-Allow-Headers: Content-Type, Authorization, X-API-Key\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header)
        await writer.drain()

    async def _send_sse_event(self, writer: asyncio.StreamWriter, data: dict[str, Any]) -> None:
        line = f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
        writer.write(line)
        await writer.drain()

    async def _handle_chat(self, writer: asyncio.StreamWriter, body: dict[str, Any]) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            await self._send_json(writer, 400, {"error": "Missing 'prompt' in request body."})
            return

        system_prompt = str(body.get("system_prompt") or "").strip()
        if system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"

        stream = bool(body.get("stream", False))
        response_format = str(body.get("response_format") or "text").lower()
        if response_format == "json":
            prompt = (
                f"{prompt}\n\n"
                "IMPORTANT: Reply ONLY with a valid JSON object or JSON array. "
                "Do not include any extra explanation or markdown outside the JSON."
            )
        model = body.get("model") or None
        gem = body.get("gem") or None
        chat_id = body.get("chat_id") or None
        temporary = bool(body.get("temporary", False))
        extended_thinking = bool(body.get("extended_thinking", False))
        save_media = bool(body.get("save_media", True))
        raw_files = body.get("files") or []

        # Materialize uploaded Base64 files into a temporary directory so filenames are preserved
        temp_upload_dir = Path(tempfile.mkdtemp(prefix="gemini_upload_"))
        upload_paths: list[Path] = []
        try:
            for item in raw_files:
                if isinstance(item, dict) and item.get("data"):
                    fname = _safe_filename(str(item.get("name") or "file.bin"))
                    raw_b64 = str(item["data"])
                    if "," in raw_b64:
                        raw_b64 = raw_b64.split(",", 1)[1]
                    fpath = temp_upload_dir / fname
                    fpath.write_bytes(base64.b64decode(raw_b64))
                    upload_paths.append(fpath)

            client = await self.ensure_client()
            t0 = time.perf_counter()

            if stream:
                await self._send_sse_headers(writer)
                ttft: float | None = None
                output = None
                try:
                    async with self.semaphore:
                        self.active_requests += 1
                        try:
                            if chat_id:
                                latest = await client.fetch_latest_chat_response(chat_id)
                                if latest:
                                    chat = client.start_chat(
                                        metadata=list(latest.metadata),
                                        cid=chat_id,
                                        rcid=latest.rcid,
                                        model=model,
                                        gem=gem,
                                    )
                                else:
                                    chat = client.start_chat(cid=chat_id, model=model, gem=gem)
                                stream_gen = chat.send_message_stream(
                                    prompt,
                                    files=upload_paths or None,
                                    temporary=temporary,
                                    extended_thinking=extended_thinking,
                                )
                            else:
                                stream_gen = client.generate_content_stream(
                                    prompt,
                                    files=upload_paths or None,
                                    model=model,
                                    gem=gem,
                                    temporary=temporary,
                                    extended_thinking=extended_thinking,
                                )

                            async for chunk in stream_gen:
                                output = chunk
                                if chunk.text_delta or chunk.thoughts_delta:
                                    if ttft is None:
                                        ttft = round(time.perf_counter() - t0, 3)
                                    await self._send_sse_event(
                                        writer,
                                        {
                                            "type": "delta",
                                            "text_delta": chunk.text_delta,
                                            "thoughts_delta": chunk.thoughts_delta,
                                        },
                                    )
                        finally:
                            self.active_requests -= 1

                    images_out, videos_out, media_out = await self._extract_and_save_media(
                        output, save_media=save_media
                    )
                    elapsed = round(time.perf_counter() - t0, 3)
                    cid = output.metadata[0] if (output and output.metadata) else chat_id
                    final_text = output.text if output else ""
                    parsed_json = (
                        _extract_json_from_text(final_text)
                        if response_format == "json"
                        else None
                    )
                    await self._send_sse_event(
                        writer,
                        {
                            "type": "done",
                            "text": final_text,
                            "json": parsed_json,
                            "thoughts": output.thoughts if output else None,
                            "chat_id": cid,
                            "rcid": output.rcid if output else None,
                            "images": images_out,
                            "videos": videos_out,
                            "media": media_out,
                            "ttft_sec": ttft,
                            "elapsed_sec": elapsed,
                        },
                    )
                    writer.write(b"data: [DONE]\n\n")
                    await writer.drain()
                except Exception as e:
                    await self._send_sse_event(writer, {"type": "error", "error": str(e)})
                return

            async with self.semaphore:
                self.active_requests += 1
                try:
                    if chat_id:
                        latest = await client.fetch_latest_chat_response(chat_id)
                        if latest:
                            chat = client.start_chat(
                                metadata=list(latest.metadata),
                                cid=chat_id,
                                rcid=latest.rcid,
                                model=model,
                                gem=gem,
                            )
                        else:
                            chat = client.start_chat(cid=chat_id, model=model, gem=gem)
                        output = await chat.send_message(
                            prompt,
                            files=upload_paths or None,
                            temporary=temporary,
                            extended_thinking=extended_thinking,
                        )
                    else:
                        output = await client.generate_content(
                            prompt,
                            files=upload_paths or None,
                            model=model,
                            gem=gem,
                            temporary=temporary,
                            extended_thinking=extended_thinking,
                        )
                finally:
                    self.active_requests -= 1

            images_out, videos_out, media_out = await self._extract_and_save_media(
                output, save_media=save_media
            )
            elapsed = round(time.perf_counter() - t0, 3)
            cid = output.metadata[0] if output.metadata else chat_id
            parsed_json = (
                _extract_json_from_text(output.text) if response_format == "json" else None
            )
            await self._send_json(
                writer,
                200,
                {
                    "text": output.text,
                    "json": parsed_json,
                    "thoughts": output.thoughts,
                    "chat_id": cid,
                    "rcid": output.rcid,
                    "images": images_out,
                    "videos": videos_out,
                    "media": media_out,
                    "elapsed_sec": elapsed,
                },
            )
        finally:
            shutil.rmtree(temp_upload_dir, ignore_errors=True)

    async def _handle_images_generate(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            await self._send_json(writer, 400, {"error": "Missing 'prompt' for image generation."})
            return
        files = body.get("files") or []
        if files and not re.search(r"\b(image|generate|edit|modify|vẽ|tạo ảnh|sửa ảnh)\b", prompt, re.I):
            prompt = f"Use image generation tool to edit this attached image: {prompt}"
        elif not re.search(r"\b(image|generate|draw|photo|vẽ|tạo ảnh)\b", prompt, re.I):
            prompt = f"Generate a high-resolution image: {prompt}"

        chat_body = {
            "prompt": prompt,
            "model": body.get("model") or None,
            "stream": False,
            "temporary": bool(body.get("temporary", True)),
            "save_media": bool(body.get("save_media", True)),
            "files": files,
        }
        await self._handle_chat(writer, chat_body)

    async def _handle_videos_generate(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            await self._send_json(writer, 400, {"error": "Missing 'prompt' for video generation."})
            return
        if not re.search(r"\b(video|clip|animate|tạo video)\b", prompt, re.I):
            prompt = f"Generate a video: {prompt}"

        chat_body = {
            "prompt": prompt,
            "model": body.get("model") or None,
            "stream": False,
            "temporary": bool(body.get("temporary", True)),
            "save_media": bool(body.get("save_media", True)),
            "files": body.get("files") or [],
        }
        await self._handle_chat(writer, chat_body)

    async def _handle_audio_generate(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            await self._send_json(writer, 400, {"error": "Missing 'prompt' for audio/music generation."})
            return
        if not re.search(r"\b(music|song|audio|track|beat|nhạc|bài hát)\b", prompt, re.I):
            prompt = f"Generate a music track: {prompt}"

        chat_body = {
            "prompt": prompt,
            "model": body.get("model") or None,
            "stream": False,
            "temporary": bool(body.get("temporary", True)),
            "save_media": bool(body.get("save_media", True)),
        }
        await self._handle_chat(writer, chat_body)

    async def _handle_openai_chat(self, writer: asyncio.StreamWriter, body: dict[str, Any]) -> None:
        messages = body.get("messages") or []
        if not messages:
            await self._send_json(writer, 400, {"error": "Missing 'messages' list."})
            return

        stream = bool(body.get("stream", False))
        rf_raw = body.get("response_format")
        if isinstance(rf_raw, dict):
            response_format = str(rf_raw.get("type", "text")).lower()
        else:
            response_format = str(rf_raw or "text").lower()

        model = body.get("model") or None
        parts = []
        for msg in messages:
            role = str(msg.get("role", "user")).upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(c.get("text", "")) for c in content if isinstance(c, dict)
                )
            parts.append(f"[{role}]: {content}" if len(messages) > 1 else str(content))
        prompt = "\n\n".join(parts)

        if response_format in ("json", "json_object"):
            prompt = (
                f"{prompt}\n\n"
                "IMPORTANT: Reply ONLY with a valid JSON object or JSON array. "
                "Do not include any extra explanation or markdown outside the JSON."
            )

        client = await self.ensure_client()
        now_ts = int(time.time())

        if stream:
            await self._send_sse_headers(writer)
            async with self.semaphore:
                self.active_requests += 1
                try:
                    async for chunk in client.generate_content_stream(
                        prompt,
                        model=model,
                        temporary=bool(body.get("temporary", True)),
                    ):
                        if chunk.text_delta or chunk.thoughts_delta:
                            delta_obj: dict[str, Any] = {}
                            if chunk.text_delta:
                                delta_obj["content"] = chunk.text_delta
                            if chunk.thoughts_delta:
                                delta_obj["reasoning_content"] = chunk.thoughts_delta
                            await self._send_sse_event(
                                writer,
                                {
                                    "id": f"chatcmpl-{chunk.rcid or now_ts}",
                                    "object": "chat.completion.chunk",
                                    "created": now_ts,
                                    "model": model or "gemini-default",
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": delta_obj,
                                            "finish_reason": None,
                                        }
                                    ],
                                },
                            )
                finally:
                    self.active_requests -= 1
            await self._send_sse_event(
                writer,
                {
                    "id": f"chatcmpl-{now_ts}",
                    "object": "chat.completion.chunk",
                    "created": now_ts,
                    "model": model or "gemini-default",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            )
            writer.write(b"data: [DONE]\n\n")
            await writer.drain()
            return

        async with self.semaphore:
            self.active_requests += 1
            try:
                output = await client.generate_content(
                    prompt,
                    model=model,
                    temporary=bool(body.get("temporary", True)),
                )
            finally:
                self.active_requests -= 1

        await self._send_json(
            writer,
            200,
            {
                "id": f"chatcmpl-{output.rcid or now_ts}",
                "object": "chat.completion",
                "created": now_ts,
                "model": model or "gemini-default",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": output.text,
                            "reasoning_content": output.thoughts,
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def _handle_concurrent_test(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        base_prompt = str(
            body.get("prompt") or "Trả lời ngắn gọn 1 câu: Thủ đô của Việt Nam là gì?"
        )
        total_requests = min(20, max(1, int(body.get("requests", 4))))
        concurrency = min(10, max(1, int(body.get("concurrency", 3))))
        model = body.get("model") or None

        client = await self.ensure_client()
        local_sem = asyncio.Semaphore(concurrency)

        async def _worker(idx: int) -> dict[str, Any]:
            t_start = time.perf_counter()
            async with local_sem:
                self.active_requests += 1
                try:
                    out = await client.generate_content(
                        f"[Request #{idx}] {base_prompt}",
                        model=model,
                        temporary=True,
                    )
                    return {
                        "index": idx,
                        "ok": True,
                        "elapsed_sec": round(time.perf_counter() - t_start, 3),
                        "text_preview": out.text[:160],
                    }
                except Exception as e:
                    return {
                        "index": idx,
                        "ok": False,
                        "elapsed_sec": round(time.perf_counter() - t_start, 3),
                        "error": f"{type(e).__name__}: {e}",
                    }
                finally:
                    self.active_requests -= 1

        t0 = time.perf_counter()
        results = await asyncio.gather(*(_worker(i + 1) for i in range(total_requests)))
        total_elapsed = round(time.perf_counter() - t0, 3)
        succeeded = sum(1 for r in results if r["ok"])

        await self._send_json(
            writer,
            200,
            {
                "total_requests": total_requests,
                "concurrency": concurrency,
                "succeeded": succeeded,
                "failed": total_requests - succeeded,
                "total_elapsed_sec": total_elapsed,
                "avg_req_sec": round(
                    sum(r["elapsed_sec"] for r in results) / len(results), 3
                ),
                "results": results,
            },
        )

    async def _handle_cookies_fetch(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        browser_filter = body.get("browser")
        browsers = [browser_filter] if browser_filter and browser_filter != "auto" else None
        found_sources: dict[str, dict[str, str]] = {}

        if cdp_list := await load_cdp_cookies(domain_name="google.com", verbose=True):
            c_map = {c["name"]: c["value"] for c in cdp_list}
            if COOKIE_1PSID in c_map:
                found_sources["cdp"] = {
                    COOKIE_1PSID: c_map[COOKIE_1PSID],
                    COOKIE_1PSIDTS: c_map.get(COOKIE_1PSIDTS, ""),
                }

        bc3_map = load_browser_cookies(domain_name="google.com", verbose=True, browsers=browsers)
        for b_name, c_list in bc3_map.items():
            c_map = {c["name"]: c["value"] for c in c_list}
            if COOKIE_1PSID in c_map:
                found_sources[b_name] = {
                    COOKIE_1PSID: c_map[COOKIE_1PSID],
                    COOKIE_1PSIDTS: c_map.get(COOKIE_1PSIDTS, ""),
                }

        if not found_sources:
            await self._send_json(
                writer,
                404,
                {
                    "ok": False,
                    "message": (
                        "Không tìm thấy cookie __Secure-1PSID từ trình duyệt. "
                        "Hãy dán trực tiếp cookie vào ô bên dưới."
                    ),
                },
            )
            return

        chosen_source, chosen_cookies = next(iter(found_sources.items()))
        self._save_file_cookies(chosen_cookies)
        try:
            await self.ensure_client(force_reinit=True)
            await self._send_json(
                writer,
                200,
                {
                    "ok": True,
                    "source": chosen_source,
                    "all_detected_sources": list(found_sources.keys()),
                    "saved_to": str(self.cookies_path.resolve()),
                    "has_1psidts": bool(chosen_cookies.get(COOKIE_1PSIDTS)),
                },
            )
        except Exception as e:
            await self._send_json(
                writer,
                400,
                {
                    "ok": False,
                    "source": chosen_source,
                    "error": f"Đã tìm thấy cookie nhưng xác thực thất bại: {e}",
                },
            )

    async def _handle_cookies_paste(
        self, writer: asyncio.StreamWriter, body: dict[str, Any]
    ) -> None:
        raw = str(body.get("raw") or "")
        parsed = parse_raw_cookies(raw)
        if COOKIE_1PSID not in parsed:
            await self._send_json(
                writer,
                400,
                {
                    "ok": False,
                    "error": "Không tìm thấy __Secure-1PSID trong dữ liệu bạn dán.",
                    "parsed_keys": list(parsed.keys()),
                },
            )
            return

        self._save_file_cookies(parsed)
        try:
            await self.ensure_client(force_reinit=True)
            await self._send_json(
                writer,
                200,
                {
                    "ok": True,
                    "saved_to": str(self.cookies_path.resolve()),
                    "cookie_source": getattr(self.client, "_cookie_source", "Base Cookies"),
                    "models": [m.model_name for m in (self.client.list_models() or [])]
                    if self.client
                    else [],
                },
            )
        except Exception as e:
            await self._send_json(
                writer,
                400,
                {
                    "ok": False,
                    "error": f"Cookie đã lưu nhưng khởi tạo GeminiClient thất bại: {e}",
                },
            )

    async def _handle_cookies_rotate(self, writer: asyncio.StreamWriter) -> None:
        client = await self.ensure_client()
        new_ts = await rotate_1psidts(
            client._live_client,
            base_psid=client._base_psid,
            verbose=True,
        )
        if new_ts:
            self._save_file_cookies({COOKIE_1PSIDTS: new_ts})
        await self._send_json(
            writer,
            200,
            {
                "ok": True,
                "rotated": bool(new_ts),
                "new_1psidts_prefix": (new_ts[:18] + "...") if new_ts else None,
            },
        )

    async def _send_json(
        self, writer: asyncio.StreamWriter, status: int, data: dict[str, Any]
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        status_text = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            500: "Internal Server Error",
        }.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            "Access-Control-Allow-Headers: Content-Type, Authorization, X-API-Key\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + payload)
        await writer.drain()

    async def _send_html(self, writer: asyncio.StreamWriter, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        header = (
            f"HTTP/1.1 {status} OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + payload)
        await writer.drain()


async def run_api_server(
    host: str = os.getenv("GEMINI_HOST", "localhost"),
    port: int = int(os.getenv("GEMINI_PORT", "8000")),
    cookies_path: str | Path = "cookies.json",
    proxy: str | None = None,
    max_concurrency: int = 3,
    auto_refresh: bool = True,
    api_key: str = os.getenv("GEMINI_API_KEY", "minhdan2k4A@123"),
    verbose: bool = False,
) -> None:
    app = GeminiAPIServer(
        cookies_path=cookies_path,
        proxy=proxy,
        max_concurrency=max_concurrency,
        auto_refresh=auto_refresh,
        api_key=api_key,
        verbose=verbose,
    )
    is_local_only = host in ("127.0.0.1", "localhost", "::1")
    print(f"🚀 Khởi động Gemini WebAPI Provider tại: http://{host}:{port}")
    print(
        f"   - Chế độ mạng:                 "
        f"{'🔒 Localhost-Only (Chặn truy cập từ bên ngoài VPS)' if is_local_only else '🌐 Public'}"
    )
    print(f"   - Giao diện Multimodal Studio: http://{host}:{port}/")
    print(f"   - Tài liệu Swagger UI:         http://{host}:{port}/docs")
    print(f"   - Secret Key (/v1/*):          {api_key}")
    print(f"   - Concurrency Semaphore:       tối đa {max_concurrency} luồng song song")

    try:
        await app.ensure_client()
        print(
            f"✅ Đã kết nối Gemini thành công! (Nguồn cookie: {app.client._cookie_source if app.client else 'N/A'})"
        )
    except Exception as e:
        print(f"⚠️  Chưa khởi tạo được GeminiClient ngay lúc bật: {e}")
        print(f"👉 Bạn có thể mở http://{host}:{port}/ trên trình duyệt để nạp Cookie!")

    server = await asyncio.start_server(app.handle_request, host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_api_server())
    except KeyboardInterrupt:
        print("\nĐã dừng server.")
    except Exception:
        traceback.print_exc()
