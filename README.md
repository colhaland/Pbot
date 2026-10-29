# Pbot

Platform otomasi multi-website dengan dashboard, Telegram, Gemini, worker Playwright, jadwal, dan persetujuan manusia. Akses hanya website yang Anda miliki atau berwenang kelola. CAPTCHA/MFA tidak dilewati; tindakan tulis selalu membutuhkan persetujuan.

## Menjalankan lokal

Prasyarat: Docker Compose, Python 3.12+, Node.js 20+.

1. Salin `.env.example` ke `.env`. Isi `ENCRYPTION_MASTER_KEY` dengan 32 byte hex (`openssl rand -hex 32`), ganti `SESSION_SECRET` dengan nilai acak panjang, lalu isi `BOOTSTRAP_OWNER_EMAIL` dan `BOOTSTRAP_OWNER_PASSWORD` (minimal 12 karakter).
2. Jalankan `docker compose up --build -d`.
3. API membuat owner awal satu kali dari dua variabel bootstrap saat database masih kosong. Alternatifnya, buat owner interaktif melalui terminal API:

   ```sh
   docker compose exec api python -m app.bootstrap --email owner@example.com
   ```

4. Buka `http://localhost:3000` dan masuk dengan email/password bootstrap. Tidak ada akun/password default. Setelah owner dibuat, hapus `BOOTSTRAP_OWNER_PASSWORD` dari secret environment dan restart API.
5. Untuk mode tanpa Docker, jalankan PostgreSQL dan Redis, install `backend[dev,worker]`, jalankan API (`uvicorn app.main:app --reload`), worker (`python -m app.worker`), scheduler (`python -m app.scheduler`), dan frontend (`cd frontend && npm install && npm run dev`).

Database development dibuat otomatis saat API dimulai. Production harus menjalankan migrasi Alembic sebelum service dimulai. Kredensial website hanya dapat dienkripsi setelah `ENCRYPTION_MASTER_KEY` diatur. Status setup awal tersedia di `/api/v1/auth/bootstrap/status`; seed tidak mengubah akun yang sudah ada.

## Konfigurasi website

Daftarkan URL HTTPS, adapter ID, selector login, selector redaksi sensitif, whitelist aksi, dan template form melalui API owner. Adapter deklaratif `configured_selectors` membaca `adapter_config.login`, `extract.fields`, `pages`, `sensitive_selectors`, dan `forms`. Setiap website tetap memerlukan selector dan struktur halaman spesifik yang diverifikasi pada website milik pengguna. Job yang belum memiliki konfigurasi memadai gagal tertutup dengan kode yang disanitasi.

## Endpoint inti

- Liveness/readiness: `/healthz`, `/readyz`
- API: `/api/v1` (schema interaktif tersedia di `/docs` pada development)
- Perintah Telegram: `/help`, `/websites`, `/status`, `/jobs`, `/data`, `/screenshot`, `/test`, `/fill`, `/approve`, `/reject`, `/cancel`, `/schedules`, `/ask`
- Bootstrap owner dari environment dapat digunakan pada deployment pertama di development maupun production, hanya saat tabel user masih kosong. Status setup dapat diperiksa di `/api/v1/auth/bootstrap/status`; CLI menolak akun kedua dan pada production memerlukan `--confirm-production`.

## Secret dan production

Jangan menyimpan `.env` di repositori. Atur kredensial melalui secret manager. Hapus `BOOTSTRAP_OWNER_PASSWORD` setelah owner awal dibuat. Production membutuhkan `SESSION_COOKIE_SECURE=true`, `ENCRYPTION_MASTER_KEY`, `SESSION_SECRET`, TLS, migrasi database, dan object storage persisten untuk artefak. Compose lokal memakai volume artefak; ini bukan pengganti S3-compatible storage untuk deployment. Webhook Telegram harus didaftarkan dengan `TELEGRAM_WEBHOOK_SECRET` dan endpoint HTTPS.

## Validasi

```sh
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
cd frontend && npm run lint && npm run build
```

Adapter website konkret, uji staging milik pengguna, rotasi kunci, object storage signed URL, alert/metrics, 2FA TOTP, serta retensi artefak production perlu dikonfigurasi sebelum penggunaan produksi.