# Pbot

Platform otomasi multi-website dengan dashboard, Telegram, Gemini, worker Playwright, jadwal, dan persetujuan manusia. Akses hanya website yang Anda miliki atau berwenang kelola. CAPTCHA/MFA tidak dilewati; tindakan tulis selalu membutuhkan persetujuan.

## Menjalankan lokal

Prasyarat: Docker Compose, Python 3.12+, Node.js 20+.

1. Salin `.env.example` ke `.env`. Isi `ENCRYPTION_MASTER_KEY` dengan 32 byte hex (`openssl rand -hex 32`) dan ganti `SESSION_SECRET` dengan nilai acak panjang.
2. Jalankan `docker compose up --build -d`.
3. Buat owner pertama melalui terminal API:

   ```sh
   docker compose exec api python -m app.bootstrap --email owner@example.com
   ```

4. Buka `http://localhost:3000` dan masuk dengan kredensial owner.
5. Untuk mode tanpa Docker, jalankan PostgreSQL dan Redis, install `backend[dev,worker]`, jalankan API (`uvicorn app.main:app --reload`), worker (`python -m app.worker`), scheduler (`python -m app.scheduler`), dan frontend (`cd frontend && npm install && npm run dev`).

Database development dibuat otomatis saat API dimulai. Production harus menjalankan migrasi Alembic sebelum service dimulai. Kredensial website hanya dapat dienkripsi setelah `ENCRYPTION_MASTER_KEY` diatur.

## Konfigurasi website

Daftarkan URL HTTPS, adapter ID, selector login, selector redaksi sensitif, whitelist aksi, dan template form melalui API owner. Adapter deklaratif `configured_selectors` membaca `adapter_config.login`, `extract.fields`, `pages`, `sensitive_selectors`, dan `forms`. Setiap website tetap memerlukan selector dan struktur halaman spesifik yang diverifikasi pada website milik pengguna. Job yang belum memiliki konfigurasi memadai gagal tertutup dengan kode yang disanitasi.

## Endpoint inti

- Liveness/readiness: `/healthz`, `/readyz`
- API: `/api/v1` (schema interaktif tersedia di `/docs` pada development)
- Perintah Telegram: `/help`, `/websites`, `/status`, `/jobs`, `/data`, `/screenshot`, `/test`, `/fill`, `/approve`, `/reject`, `/cancel`, `/schedules`, `/ask`
- Endpoint bootstrap owner hanya berfungsi pada mode development dan menolak pembuatan akun jika pengguna sudah ada.

## Secret dan production

Jangan menyimpan `.env` di repositori. Atur kredensial melalui secret manager. Production membutuhkan `SESSION_COOKIE_SECURE=true`, `ENCRYPTION_MASTER_KEY`, `SESSION_SECRET`, TLS, migrasi database, dan object storage persisten untuk artefak. Compose lokal memakai volume artefak; ini bukan pengganti S3-compatible storage untuk deployment. Webhook Telegram harus didaftarkan dengan `TELEGRAM_WEBHOOK_SECRET` dan endpoint HTTPS.

## Validasi

```sh
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
cd frontend && npm run lint && npm run build
```

Adapter website konkret, uji staging milik pengguna, rotasi kunci, object storage signed URL, alert/metrics, 2FA TOTP, serta retensi artefak production perlu dikonfigurasi sebelum penggunaan produksi.