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

MEDIA_DIR = Path(__file__).resolve().parent / "media_outputs"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

PLAYGROUND_HTML = """<!DOCTYPE html>
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

      const payload = {
        prompt,
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

      try {
        const res = await fetch('/v1/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        const botCard = document.createElement('div');
        botCard.className = 'msg-card assistant';

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

          let mediaHtml = '';
          const allMediaItems = [];

          // 1. Images (Generated or Web)
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

          // 2. Videos (GeneratedVideo)
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

          // 3. Music / Audio (GeneratedMedia)
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

          if (allMediaItems.length) {
            mediaHtml = `<div class="media-grid">${allMediaItems.join('')}</div>`;
          }

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
        feed.appendChild(botCard);
        feed.scrollTop = feed.scrollHeight;
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
      const res = await fetch('/v1/gems');
      const data = await res.json();
      if (data.gems) {
        sel.innerHTML = '<option value="">-- Không dùng Gem --</option>' +
          data.gems.map(g => `<option value="${g.id}">${escapeHtml(g.name)} ${g.predefined ? '(System)' : '(Custom)'}</option>`).join('');
      }
    }

    async function loadChatList() {
      const box = document.getElementById('chatHistoryList');
      box.innerHTML = '<div style="color:var(--muted);font-size:0.82rem;">Đang tải...</div>';
      const res = await fetch('/v1/chats');
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
        headers: { 'Content-Type': 'application/json' },
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
        headers: { 'Content-Type': 'application/json' },
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
      const res = await fetch('/v1/cookies/fetch', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      await checkHealth();
    }

    async function rotateCookies() {
      const out = document.getElementById('cookieOut');
      out.style.display = 'block';
      out.textContent = 'Đang xoay vòng __Secure-1PSIDTS...';
      const res = await fetch('/v1/cookies/rotate', { method: 'POST' });
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
        headers: {'Content-Type': 'application/json'},
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
        headers: {'Content-Type': 'application/json'},
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


class GeminiAPIServer:
    """Manages a shared `GeminiClient` instance, concurrency semaphore, media proxy, and HTTP routes."""

    def __init__(
        self,
        cookies_path: str | Path = "cookies.json",
        proxy: str | None = None,
        max_concurrency: int = 3,
        auto_refresh: bool = True,
        verbose: bool = False,
    ):
        self.cookies_path = Path(cookies_path)
        self.proxy = proxy
        self.max_concurrency = max(1, max_concurrency)
        self.semaphore = asyncio.Semaphore(self.max_concurrency)
        self.active_requests = 0
        self.auto_refresh = auto_refresh
        self.verbose = verbose
        self.client: GeminiClient | None = None
        self.last_error: str | None = None
        self._init_lock = asyncio.Lock()

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

            if method == "GET" and path.startswith("/media/"):
                filename = Path(unquote(path.removeprefix("/media/"))).name
                await self._serve_media_file(writer, filename)
                return

            if method == "GET" and path == "/health":
                await self._handle_health(writer)
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

    async def _handle_chat(self, writer: asyncio.StreamWriter, body: dict[str, Any]) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            await self._send_json(writer, 400, {"error": "Missing 'prompt' in request body."})
            return

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

            # Process & optionally save images, videos, and audio locally so browser can play/view directly
            images_out: list[dict[str, Any]] = []
            for img in output.images or []:
                is_gen = isinstance(img, GeneratedImage)
                local_url = None
                if save_media:
                    with contextlib.suppress(Exception):
                        saved_path = await img.save(
                            path=str(MEDIA_DIR),
                            full_size=True,
                        )
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
            for vid in output.videos or []:
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
            for m in output.media or []:
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

            elapsed = round(time.perf_counter() - t0, 3)
            cid = output.metadata[0] if output.metadata else chat_id
            await self._send_json(
                writer,
                200,
                {
                    "text": output.text,
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

    async def _handle_openai_chat(self, writer: asyncio.StreamWriter, body: dict[str, Any]) -> None:
        messages = body.get("messages") or []
        if not messages:
            await self._send_json(writer, 400, {"error": "Missing 'messages' list."})
            return

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

        client = await self.ensure_client()
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

        now_ts = int(time.time())
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
            "Access-Control-Allow-Headers: Content-Type, Authorization\r\n"
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
    host: str = "127.0.0.1",
    port: int = 8000,
    cookies_path: str | Path = "cookies.json",
    proxy: str | None = None,
    max_concurrency: int = 3,
    auto_refresh: bool = True,
    verbose: bool = False,
) -> None:
    app = GeminiAPIServer(
        cookies_path=cookies_path,
        proxy=proxy,
        max_concurrency=max_concurrency,
        auto_refresh=auto_refresh,
        verbose=verbose,
    )
    print(f"🚀 Khởi động Gemini WebAPI Studio tại: http://{host}:{port}")
    print(f"   - Giao diện Multimodal Studio: http://{host}:{port}/")
    print(f"   - OpenAI Compatible Endpoint:  POST http://{host}:{port}/v1/chat/completions")
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
