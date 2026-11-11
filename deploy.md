# Deploy Gemini WebAPI Provider lên VPS Ubuntu (`/opt/my-gemini-api`)

Hướng dẫn triển khai `my-gemini-api` chạy nội bộ (`localhost:8000`) cùng VPS với `/opt/exam-epu-web`, quản lý bằng **PM2**.

| Thành phần | Giá trị |
|---|---|
| Thư mục code | **`/opt/my-gemini-api`** |
| Cổng chạy | **`localhost:8000`** (Chỉ nội bộ VPS gọi được, bên ngoài internet không thể truy cập) |
| Secret Key | **Bắt buộc**, ≥ 16 ký tự, không có giá trị mặc định – cùng 1 giá trị trong `/opt/my-gemini-api/.env` và `/opt/exam-epu-web/.env` (thiếu thì server không khởi động) |
| Quản lý tiến trình | **PM2** (`my-gemini-api`) — chỉ tốn ~35 MB RAM |

```text
Next.js (/opt/exam-epu-web :3876) ──[POST http://localhost:8000/v1/chat + Secret Key]──> Python (/opt/my-gemini-api :8000)
```

---

## 1. Cài đặt Python venv & Clone dự án vào `/opt/my-gemini-api`

Chạy trực tiếp trên VPS (quyền `root`):

```bash
apt update
apt install -y python3 python3-venv python3-pip git

# Clone repo vào /opt/my-gemini-api
git clone https://github.com/minhdan190904/my-gemini-api.git /opt/my-gemini-api
cd /opt/my-gemini-api

# Tạo môi trường ảo (.venv) và cài đặt thư viện
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

> **Lưu ý nếu repo `my-gemini-api` để Private:** Tạo Deploy Key giống `exam-epu-web`:
> ```bash
> ssh-keygen -t ed25519 -C "vps-gemini-deploy" -f ~/.ssh/gemini_deploy -N ""
> cat ~/.ssh/gemini_deploy.pub
> ```
> Thêm key trên vào GitHub → Repo `my-gemini-api` → **Settings → Deploy keys**, sau đó thêm vào `~/.ssh/config`:
> ```bash
> cat >> ~/.ssh/config <<'EOF'
> Host github-gemini
>   HostName github.com
>   User git
>   IdentityFile ~/.ssh/gemini_deploy
>   IdentitiesOnly yes
> EOF
> git clone git@github-gemini:minhdan190904/my-gemini-api.git /opt/my-gemini-api
> ```

---

## 2. Tạo file `.env` tại `/opt/my-gemini-api/.env`

```bash
cd /opt/my-gemini-api
cp .env.example .env
```

Sinh khóa bí mật rồi điền vào `GEMINI_API_KEY` (dùng **cùng giá trị** cho `exam-epu-web` ở bước 5):
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Điền thêm `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID` để nhận báo lỗi / cảnh báo Cookie hết hạn / request chậm về Telegram nhóm Monitor giống `exam-epu-web`):
```env
GEMINI_HOST=localhost
GEMINI_PORT=8000
GEMINI_API_KEY=<khoa-bi-mat-vua-sinh>
GEMINI_MAX_CONCURRENCY=3
# Giới hạn request (chấm bài bằng ảnh gửi tối đa 4 ảnh)
GEMINI_MAX_BODY_MB=20
GEMINI_MAX_FILES=8
GEMINI_MAX_FILE_MB=8
GEMINI_QUEUE_TIMEOUT_MS=60000
GEMINI_REQUEST_TIMEOUT_MS=150000
GEMINI_COOKIES_FILE=cookies.json
HTTPS_PROXY=

# Telegram Monitor (giống exam-epu-web)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=-5280042435
MONITOR_ENABLED=true
MONITOR_SLOW_MS=15000
MONITOR_LAG_MS=500
MONITOR_DEBUG=0
```

---

## 3. Nạp Cookie (`cookies.json`) lên VPS (1 trong 2 cách)

### Cách A: Copy nội dung `cookies.json` từ máy tính của bạn dán thẳng lên VPS
Chạy trên VPS:
```bash
cat > /opt/my-gemini-api/cookies.json <<'EOF'
{
  "cookies": {
    "__Secure-1PSID": "DÁN_GIÁ_TRỊ_1PSID_CỦA_BẠN_VÀO_ĐÂY",
    "__Secure-1PSIDTS": "DÁN_GIÁ_TRỊ_1PSIDTS_CỦA_BẠN_VÀO_ĐÂY"
  }
}
EOF
chmod 600 /opt/my-gemini-api/cookies.json
```

### Cách B: Mở giao diện Web Studio (`http://localhost:8000`) qua SSH Tunnel
Khi server đã chạy ở Bước 4, trên máy Windows của bạn mở PowerShell gõ:
```powershell
ssh -L 8000:localhost:8000 root@45.63.60.154
```
Sau đó mở trình duyệt trên máy bạn vào **http://localhost:8000/** → tab **🔑 Quản Lý Cookie & Tài Khoản** → dán Cookie và bấm **💾 Lưu & Xác Thực**.

---

## 4. Chạy bằng PM2 (Tự khởi động lại khi VPS reboot)

```bash
cd /opt/my-gemini-api
pm2 start .venv/bin/python --name my-gemini-api -- cli.py serve
pm2 save
```

Kiểm tra ngay trên VPS:
```bash
pm2 status
curl -s http://localhost:8000/health
```

---

## 5. Khai báo trong `/opt/exam-epu-web/.env`

Đảm bảo trong file `/opt/exam-epu-web/.env` có các dòng sau rồi reload `exam-epu-web`:

```bash
cat >> /opt/exam-epu-web/.env <<'EOF'

# ---------- AI Provider (Gemini WebAPI nội bộ) ----------
GEMINI_API_URL=http://localhost:8000
GEMINI_API_KEY=<cung-khoa-voi-my-gemini-api>
GEMINI_MODEL_QUIZ_EXPLAIN=gemini-flash
GEMINI_MODEL_ESSAY_ASSIST=gemini-pro
GEMINI_MODEL_DOCUMENT_PARSE=gemini-pro
GEMINI_MODEL_QUICK_TASK=gemini-flash-lite
EOF

pm2 reload exam-epu-web --update-env
```

---

## 6. Cập nhật code mới sau này (1 lệnh duy nhất)

Mỗi khi bạn sửa code bên dự án Python và push lên GitHub, trên VPS chỉ cần chạy:

```bash
bash /opt/my-gemini-api/scripts/deploy.sh
```

---

## Các lệnh quản trị thường dùng trên VPS

| Việc cần làm | Lệnh |
|---|---|
| Xem trạng thái cả 2 dự án | `pm2 status` |
| Xem log của Gemini API | `pm2 logs my-gemini-api --lines 100` |
| Khởi động lại Gemini API | `pm2 restart my-gemini-api --update-env` |
| Kiểm tra trạng thái Cookie từ CLI | `cd /opt/my-gemini-api && .venv/bin/python cli.py cookies status` |
| Dán Cookie mới nhanh từ CLI | `cd /opt/my-gemini-api && .venv/bin/python cli.py cookies paste --raw "__Secure-1PSID=...; __Secure-1PSIDTS=..."` |
