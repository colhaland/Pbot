# Spesifikasi Teknis Lengkap: Platform Otomasi Multi-Website via Telegram

**Versi:** 1.0 (draf untuk implementasi)
**Status:** Siap ditinjau
**Bahasa dokumen:** Indonesia

---

## 1. Ringkasan

### 1.1 Tujuan

Platform ini memungkinkan pemilik untuk mengelola otomasi pada 3–10 website miliknya sendiri melalui dua kanal: dashboard web untuk konfigurasi dan Telegram untuk perintah harian, notifikasi, dan persetujuan. Setiap website memiliki satu akun aktif. AI Gemini membantu memahami instruksi dalam bahasa Indonesia, tetapi tidak pernah memiliki kendali penuh atas eksekusi.

### 1.2 Prinsip Desain

1. **AI memahami, mesin memutuskan.** Keluaran AI hanya berupa usulan terstruktur. Mesin otomasi memvalidasi usulan tersebut terhadap kebijakan dan daftar tindakan yang diizinkan.
2. **Kredensial tidak pernah masuk ke AI.** Prompt, log, dan respons AI tidak boleh berisi password, cookie, token sesi, atau API key.
3. **Least privilege.** Setiap adapter website hanya mendapat izin untuk tindakan yang didefinisikan secara eksplisit.
4. **Human-in-the-loop untuk perubahan.** Semua tindakan tulis membutuhkan persetujuan yang terikat pada parameter spesifik.
5. **Deterministik dan dapat diaudit.** Setiap job dapat ditelusuri dari permintaan awal sampai hasil akhir.
6. **Konfigurasi per website.** Tidak ada skrip universal. Setiap website memiliki adapter tersendiri.

### 1.3 Ruang Lingkup

**Termasuk dalam versi 1.0:**
- Pendaftaran dan konfigurasi hingga 10 website
- Login otomatis dengan satu akun per website
- Ekstraksi data terstruktur dari halaman yang diizinkan
- Screenshot halaman dengan redaksi dasar
- Pengisian dan pengiriman form dengan persetujuan
- Jadwal harian, mingguan, dan cron
- Integrasi Telegram untuk perintah, hasil, dan persetujuan
- Integrasi Google Gemini dengan daftar model dinamis
- Audit log dan riwayat eksekusi

**Di luar ruang lingkup v1.0:**
- Bypass CAPTCHA, MFA, atau mekanisme verifikasi keamanan lain
- Akses ke website pihak ketiga yang bukan milik pengguna
- Multi-akun per website
- Multi-tenant untuk banyak pengguna berbeda
- Aplikasi mobile native
- Penghapusan data otomatis tanpa persetujuan
- Scraping skala besar yang melanggar ketentuan layanan website

### 1.4 Asumsi dan Batasan

- Pengguna memiliki hak sah atas semua akun website yang didaftarkan.
- Website target memiliki halaman login dan dashboard yang dapat diakses dengan browser standar.
- Sebagian website mungkin tidak memiliki API resmi, sehingga otomasi berbasis browser menjadi pilihan.
- Perubahan struktur halaman website dapat memutus adapter. Ini dianggap risiko operasional yang rutin, bukan kegagalan sistem.
- Ketersediaan model Gemini bergantung pada API key, akses akun, wilayah, dan kebijakan Google.

### 1.5 Pemangku Kepentingan dan Peran

| Peran | Hak akses utama |
|---|---|
| Owner | Semua fitur, termasuk manajemen kredensial, AI, dan pengguna Telegram |
| Operator | Menjalankan job, melihat hasil, menyetujui tindakan. Tidak dapat melihat kredensial |
| Viewer | Hanya melihat dashboard dan riwayat. Tidak dapat menjalankan atau menyetujui job |
| Telegram User (diizinkan) | Perintah sesuai peran yang dipetakan dari tabel `telegram_users` |
| Sistem (scheduler) | Membuat job terjadwal dengan izin yang sama seperti operator |

---

## 2. Arsitektur Sistem

### 2.1 Komponen

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────────┐
│ Telegram    │◄────►│ Backend API      │◄────►│ PostgreSQL      │
│ (Bot)       │webhook│ (FastAPI)        │      │                 │
└─────────────┘      │ - Auth           │      └─────────────────┘
                     │ - Validasi       │              ▲
┌─────────────┐      │ - Job dispatch   │              │
│ Frontend    │◄────►│ - Audit          │      ┌───────┴─────────┐
│ (Next.js)   │      └────────┬─────────┘      │ Redis           │
└─────────────┘               │                │ (antrean, lock) │
                              ▼                └───────┬─────────┘
                     ┌──────────────────┐              │
                     │ AI Gateway       │              ▼
                     │ (Gemini)         │      ┌─────────────────┐
                     └──────────────────┘      │ Automation      │
                                               │ Worker          │
                                               │ (Playwright)    │
                                               └────────┬────────┘
                                                        ▼
                                               ┌─────────────────┐
                                               │ Website target  │
                                               │ (3–10 adapter)  │
                                               └─────────────────┘
```

### 2.2 Tanggung Jawab Komponen

**Frontend (Next.js + TypeScript)**
Dashboard pengaturan, manajemen website, konfigurasi AI, jadwal, dan persetujuan. Frontend tidak pernah menerima password, API key, atau cookie dalam bentuk plaintext setelah disimpan. Frontend hanya menampilkan status dan metadata yang sudah disamarkan (misalnya `AIza••••3f9a`).

**Backend API (Python + FastAPI)**
Titik masuk tunggal untuk dashboard dan webhook Telegram. Bertugas melakukan autentikasi, otorisasi, validasi input, pembuatan job, pengelolaan persetujuan, dan penulisan audit log. Backend tidak menjalankan Playwright secara langsung.

**AI Gateway (modul di dalam backend)**
Satu-satunya tempat yang berkomunikasi dengan Gemini API. Bertugas membangun prompt, memanggil model, memvalidasi keluaran terhadap JSON schema, dan mengembalikan usulan job. Lihat bagian 6 untuk detail.

**Automation Worker (Python + Playwright)**
Menjalankan job dari antrean. Membuka sesi browser, memanggil adapter website yang sesuai, mengambil data atau screenshot, dan mengeksekusi tindakan yang sudah disetujui. Worker menjalankan kebijakan izin yang sama baik untuk job manual maupun job terjadwal.

**Scheduler**
Proses terpisah yang memeriksa tabel `schedules` setiap menit dan membuat job baru pada waktu yang sesuai. Scheduler tidak mengeksekusi browser dan tidak melewati pemeriksaan kebijakan.

**Telegram Bot**
Menerima perintah dari pengguna yang diizinkan, mengirim notifikasi, menampilkan pratinjau persetujuan, dan meneruskan keputusan ke backend.

**PostgreSQL**
Penyimpanan utama untuk konfigurasi, job, persetujuan, jadwal, artefak, dan audit log.

**Redis**
Antrean job (RQ atau Celery), lock per website, dan penyimpanan sementara untuk token persetujuan yang sedang aktif.

**Object storage (S3-compatible atau sejenis)**
Penyimpanan screenshot dan artefak besar. Disarankan memakai layanan eksternal karena filesystem Render tidak cocok untuk data yang harus bertahan setelah restart.

### 2.3 Alur Data Utama

**Alur A: Ekstraksi data (read-only)**

1. Pengguna mengirim `/data site_01` di Telegram.
2. Bot memeriksa apakah Telegram user ID diizinkan dan memiliki peran `operator` atau `owner`.
3. Backend membuat job dengan status `queued` dan mengembalikan `job_id`.
4. Worker mengambil job, menerapkan lock pada `site_01`, dan membuka sesi browser.
5. Adapter `site_01` login menggunakan kredensial terenkripsi, lalu mengambil data sesuai selector.
6. Hasil dinormalisasi, disimpan di tabel `artifacts`, dan job berubah menjadi `succeeded`.
7. Bot mengirim ringkasan dan tautan dashboard.

**Alur B: Perubahan data (write, memerlukan persetujuan)**

1. Pengguna mengirim prompt: "Isi formulir pembaruan di website A dengan data dari draft 123."
2. AI Gateway menghasilkan usulan JSON. Backend memvalidasi usulan tersebut.
3. Worker menjalankan tahap "persiapan" tanpa menekan tombol simpan. Worker mengisi form di sesi browser, mengambil ringkasan perubahan, lalu membuat pratinjau.
4. Job berubah menjadi `awaiting_approval`. Sistem membuat approval dengan token acak dan batas waktu.
5. Bot mengirim pratinjau dengan tombol Setujui dan Tolak.
6. Setelah disetujui, backend memverifikasi hash payload. Jika cocok, job masuk ke `executing`.
7. Worker mengulang langkah pengisian dan menekan tombol simpan, lalu memverifikasi hasilnya.
8. Hasil dan screenshot konfirmasi dikirim ke Telegram.

Penting: pada alur B, sesi browser pada tahap persiapan tidak boleh tetap terbuka selama menunggu persetujuan. Worker harus menutup sesi dan menjalankan ulang langkah pengisian saat eksekusi. Hal ini mencegah kondisi sesi kedaluwarsa atau perubahan data di sisi website di antara dua tahap.

---

## 3. Spesifikasi Fungsional

### 3.1 Modul Autentikasi Dashboard

| ID | Persyaratan |
|---|---|
| AUTH-01 | Login menggunakan email dan password dengan hashing Argon2id atau bcrypt |
| AUTH-02 | Dashboard mendukung 2FA berbasis TOTP untuk role owner |
| AUTH-03 | Sesi dashboard menggunakan cookie HttpOnly, Secure, SameSite=Strict |
| AUTH-04 | Sesi kedaluwarsa setelah 12 jam tidak aktif atau 24 jam sejak login |
| AUTH-05 | Setelah 5 kali gagal login dalam 15 menit, akun dikunci sementara selama 15 menit |
| AUTH-06 | Semua endpoint manajemen memeriksa role dan kepemilikan sumber daya |

### 3.2 Modul Website

| ID | Persyaratan |
|---|---|
| WEB-01 | Pengguna dapat menambah website dengan nama, base URL, URL login, URL dashboard, dan adapter ID |
| WEB-02 | Base URL harus berupa HTTPS. Pengecualian HTTP hanya untuk lingkungan pengembangan lokal |
| WEB-03 | Sistem menolak URL yang tidak termasuk daftar domain terdaftar (lihat SSRF, bagian 9.4) |
| WEB-04 | Setiap website memiliki status: `active`, `disabled`, `needs_attention` |
| WEB-05 | Tombol "Uji koneksi" menjalankan test-login dan mengembalikan hasil dalam waktu maksimal 60 detik |
| WEB-06 | Konfigurasi selector disimpan per website dalam format JSON yang tervalidasi |
| WEB-07 | Website yang dinonaktifkan tidak dapat menerima job baru, tetapi riwayatnya tetap tersimpan |
| WEB-08 | Maksimal 10 website aktif pada versi 1.0 (batas dapat dinaikkan melalui konfigurasi) |

Contoh konfigurasi website (disimpan sebagai JSON di kolom `adapter_config`):

```json
{
  "adapter_id": "site_a",
  "login": {
    "url": "https://site-a.example.com/login",
    "username_selector": "input[name='email']",
    "password_selector": "input[name='password']",
    "submit_selector": "button[type='submit']",
    "success_indicator": "[data-testid='user-menu']",
    "failure_indicator": ".alert-error"
  },
  "dashboard_url": "https://site-a.example.com/dashboard",
  "rate_limit": {
    "min_interval_seconds": 30
  },
  "timeouts": {
    "navigation_ms": 30000,
    "action_ms": 10000
  }
}
```

### 3.3 Modul Kredensial

| ID | Persyaratan |
|---|---|
| CRD-01 | Username dan password disimpan terenkripsi (AES-256-GCM) |
| CRD-02 | Password tidak pernah ditampilkan ulang di UI. Hanya tersedia opsi "ganti password" |
| CRD-03 | Setiap penggunaan kredensial dicatat di audit log tanpa nilai kredensial |
| CRD-04 | Kredensial hanya dapat didekripsi oleh proses worker dan layanan kredensial di dalam memori |
| CRD-05 | Setiap website memiliki tepat satu kredensial aktif pada versi 1.0 |
| CRD-06 | Kunci enkripsi memiliki versi (`key_version`) untuk mendukung rotasi |

### 3.4 Modul Pengaturan AI

| ID | Persyaratan |
|---|---|
| AI-01 | Pengguna memasukkan API key Gemini. Key langsung dienkripsi dan tidak ditampilkan ulang |
| AI-02 | Backend mengambil daftar model dari endpoint model Gemini. Frontend tidak menyimpan daftar model secara permanen |
| AI-03 | Model hanya dapat dipilih jika mendukung kebutuhan: generasi teks dan keluaran JSON terstruktur |
| AI-04 | Pengguna dapat mengatur temperature (default 0.1), `max_output_tokens`, timeout (default 30 detik), dan instruksi sistem |
| AI-05 | Mode penggunaan: `structured_only` (hanya perintah dari daftar template) atau `hybrid` (perintah dan prompt bebas) |
| AI-06 | Model cadangan opsional. Diaktifkan hanya jika model utama gagal karena error sementara atau tidak tersedia |
| AI-07 | Pengaturan "izin tulis AI" bersifat default nonaktif. Jika nonaktif, AI hanya boleh mengusulkan tindakan baca |
| AI-08 | Tombol "uji koneksi" mengirim prompt uji tanpa data sensitif dan memeriksa format JSON yang dikembalikan |

### 3.5 Modul Jadwal

| ID | Persyaratan |
|---|---|
| SCH-01 | Jadwal mendukung ekspresi cron standar dan preset (harian, mingguan) |
| SCH-02 | Setiap jadwal memiliki zona waktu IANA (misalnya `Asia/Jakarta`) |
| SCH-03 | Jadwal hanya dapat menjalankan tindakan dengan risiko `read` |
| SCH-04 | Jadwal dapat diaktifkan, dinonaktifkan, atau diubah tanpa mengubah riwayat job |
| SCH-05 | Setiap eksekusi jadwal menggunakan idempotency key agar tidak terbuat dua kali |
| SCH-06 | Jika layanan mati saat jadwal seharusnya berjalan, jadwal yang terlewat dijalankan maksimal sekali saat layanan kembali (misfire policy: `run_once_latest`) |
| SCH-07 | Riwayat eksekusi jadwal ditampilkan di dashboard dengan status per run |

Catatan: jadwal yang melibatkan tindakan tulis tidak diizinkan pada v1.0 karena akan menghasilkan persetujuan yang tidak dapat ditanggapi secara real-time. Jika kebutuhan ini muncul, gunakan alur "pratinjau terjadwal" yang dibahas pada roadmap (bagian 14).

### 3.6 Modul Job

| ID | Persyaratan |
|---|---|
| JOB-01 | Setiap job memiliki `job_id` (UUID v7), jenis, website, payload, status, pembuat, dan waktu |
| JOB-02 | Payload disimpan dalam bentuk JSON yang sudah divalidasi dan dinormalisasi |
| JOB-03 | Sebelum antrean, sistem memeriksa duplikat berdasarkan `idempotency_key` dalam 10 menit terakhir |
| JOB-04 | Job dapat dibatalkan selama status `queued` atau `awaiting_approval` |
| JOB-05 | Job yang sedang `running` hanya dapat dihentikan dengan sinyal cancel yang diperiksa di antara langkah |
| JOB-06 | Setiap job menyimpan `attempt_count`, `last_error_code`, dan `last_error_message` yang sudah disanitasi |
| JOB-07 | Job yang tidak selesai dalam batas waktu (default 15 menit) ditandai `failed` dengan kode `TIMEOUT` |

### 3.7 Modul Persetujuan

Dijelaskan lengkap pada bagian 5.

### 3.8 Modul Telegram

Dijelaskan lengkap pada bagian 7.

### 3.9 Modul Artefak

| ID | Persyaratan |
|---|---|
| ART-01 | Screenshot disimpan dengan nama `{website_id}/{job_id}/{timestamp}.png` di object storage |
| ART-02 | Setiap artefak memiliki metadata: job_id, website_id, jenis, ukuran, checksum SHA-256, dan waktu |
| ART-03 | Sebelum disimpan, screenshot diproses untuk menutupi elemen yang didaftarkan sebagai sensitif (misalnya field password, nomor kartu, token di halaman) |
| ART-04 | Tautan artefak di Telegram berupa URL yang ditandatangani dan memiliki masa berlaku (default 15 menit) |
| ART-05 | Artefak dihapus otomatis setelah masa retensi (default 30 hari), kecuali ditandai pinned |
| ART-06 | Hasil ekstraksi disimpan sebagai JSON dan juga dapat diunduh sebagai CSV |

---

## 4. Spesifikasi Non-Fungsional

### 4.1 Keamanan

Dibahas rinci pada bagian 9. Persyaratan ringkas:

- Semua komunikasi menggunakan HTTPS/TLS.
- Secret tidak pernah disimpan dalam kode atau repositori.
- Log dan respons API dibersihkan dari pola sensitif.
- Akses dashboard dan Telegram dibatasi dengan autentikasi dan allowlist.

### 4.2 Keandalan

| Metrik | Target |
|---|---|
| Ketersediaan API dan webhook | ≥ 99% per bulan (bergantung pada paket Render) |
| Job terjadwal yang tereksekusi tepat waktu | ≥ 98% dalam toleransi 5 menit |
| Job yang hilang akibat restart layanan | 0 (semua job tersimpan di PostgreSQL) |
| Job duplikat akibat retry | 0 untuk tindakan tulis |

### 4.3 Performa

| Operasi | Target latensi |
|---|---|
| Respons API manajemen (non-job) | p95 ≤ 500 ms |
| Pembuatan job (sampai status `queued`) | p95 ≤ 1 detik |
| Ekstraksi data sederhana (satu halaman) | p95 ≤ 60 detik |
| Pembuatan pratinjau persetujuan | p95 ≤ 90 detik |
| Respons AI untuk perintah terstruktur | p95 ≤ 10 detik (di luar waktu model) |

### 4.4 Skalabilitas

- Versi 1.0 dirancang untuk 3–10 website dengan maksimal 3 sesi browser paralel.
- Paralelisme dibatasi per website: maksimal 1 sesi aktif per website pada satu waktu.
- Penambahan worker dilakukan secara horizontal setelah pengujian beban pada paket Render yang dipilih.

### 4.5 Observability

- Log terstruktur dalam format JSON dengan `request_id`, `job_id`, `website_id`, dan `level`.
- Metrik: jumlah job per status, durasi eksekusi per adapter, tingkat kegagalan login, latensi Gemini, dan jumlah approval yang kedaluwarsa.
- Health check: `/healthz` (proses hidup) dan `/readyz` (database, Redis, dan Gemini dapat dijangkau).
- Alert: kegagalan login beruntun, antrean menumpuk lebih dari 20 job, dan error rate di atas 10% dalam 15 menit.

### 4.6 Pemeliharaan

- Setiap adapter memiliki unit test dengan fixture HTML yang disimpan di `tests/fixtures/`.
- Setiap adapter memiliki versi (`adapter_version`) yang dicatat pada setiap job.
- Perubahan selector ditangani melalui pembaruan konfigurasi, bukan perubahan kode, bila memungkinkan.

### 4.7 Kepatuhan dan Etika Penggunaan

- Sistem hanya boleh mengakses akun milik pengguna sendiri.
- Frekuensi akses ke setiap website dibatasi (rate limit per adapter) agar tidak membebani server target.
- Sistem tidak boleh melewati CAPTCHA, MFA, atau mekanisme anti-bot. Jika muncul, job berhenti dengan status `needs_manual_action`.
- Pengguna bertanggung jawab memastikan penggunaan sesuai ketentuan layanan masing-masing website.

---

## 5. Sistem Persetujuan (Detail)

### 5.1 Status Job

```
           ┌──────────┐
   create  │ queued   │
  ────────►│          │──── cancel ────► cancelled
           └────┬─────┘
                │ worker ambil
                ▼
           ┌──────────┐
           │ running  │──── error ─────► failed
           │(prepare) │──── timeout ───► failed
           └────┬─────┘
                │ tindakan tulis
                ▼
     ┌─────────────────────┐
     │ awaiting_approval   │──── reject ────► rejected
     └──────────┬──────────┘──── expired ───► expired
                │ approve (hash cocok)
                ▼
           ┌──────────┐
           │executing │──── error ─────► failed
           └────┬─────┘──── manual ────► needs_manual_action
                │ sukses
                ▼
           ┌──────────┐
           │succeeded │
           └──────────┘
```

Tindakan baca (`read`) melewati `awaiting_approval` dan langsung dari `running` ke `succeeded`.

### 5.2 Status Persetujuan

| Status | Arti |
|---|---|
| `pending` | Menunggu keputusan |
| `approved` | Disetujui oleh pengguna yang berwenang |
| `rejected` | Ditolak |
| `expired` | Melewati batas waktu (default 30 menit) |
| `superseded` | Digantikan oleh persetujuan baru karena pratinjau berubah |

### 5.3 Pengikatan Persetujuan ke Payload

Persetujuan harus merujuk pada tepat satu kombinasi berikut:

```
approval_hash = SHA-256(
    canonical_json({
        "job_id": ...,
        "website_id": ...,
        "adapter_version": ...,
        "action": ...,
        "parameters": ...,
        "preview_summary": ...
    })
)
```

Aturan:

1. Hash dihitung saat pratinjau dibuat dan disimpan di tabel `approvals`.
2. Saat tombol Setujui ditekan, backend menghitung ulang hash dari data terbaru di database.
3. Jika hash tidak cocok, persetujuan ditolak dan pengguna diberi tahu bahwa pratinjau sudah berubah.
4. Jika parameter diubah setelah persetujuan, persetujuan lama otomatis berstatus `superseded`.

### 5.4 Token Persetujuan

- Token dibuat dengan `secrets.token_urlsafe(32)`.
- Yang disimpan di database hanya hash token (SHA-256), bukan token asli.
- Token hanya berlaku sekali (single-use). Setelah keputusan, token tidak dapat dipakai lagi.
- Masa berlaku default 30 menit. Dapat dikonfigurasi per jenis tindakan (tindakan berisiko tinggi maksimal 10 menit).

### 5.5 Otorisasi Keputusan

- Keputusan hanya diterima dari Telegram user ID yang terdaftar di `telegram_users` dengan peran `owner` atau `operator`.
- Pemohon dan pemutus tidak harus sama, tetapi peran `viewer` tidak dapat memutuskan.
- Setiap keputusan dicatat dengan `decided_by`, `decided_at`, `decision`, dan `reason` (opsional).

### 5.6 Kebijakan Risiko Tindakan

| Risiko | Contoh tindakan | Kebijakan |
|---|---|---|
| `read` | Ekstraksi data, screenshot, test login | Otomatis, sesuai jadwal atau perintah |
| `write_low` | Mengisi dan menyimpan form non-kritis | Persetujuan wajib, satu kali konfirmasi |
| `write_high` | Penghapusan, publikasi, perubahan pengaturan akun | Persetujuan wajib, konfirmasi dua langkah, batas waktu 10 menit. Pada v1.0 default-nya dinonaktifkan |

### 5.7 Pemeriksaan Ulang Sebelum Eksekusi

Sebelum tombol simpan ditekan, worker wajib:

1. Membuka halaman dan memastikan sesi masih valid.
2. Memeriksa apakah nilai yang akan diisi masih sama dengan pratinjau. Jika berbeda, job dibatalkan dengan kode `PREVIEW_MISMATCH`.
3. Memastikan halaman tidak menampilkan pesan error atau perubahan struktur yang tidak dikenal.

---

## 6. AI Gateway (Detail)

### 6.1 Peran AI

AI memiliki tiga tugas:
1. Mengubah instruksi bahasa Indonesia menjadi usulan tindakan terstruktur.
2. Meringkas hasil ekstraksi jika diminta.
3. Menjelaskan hasil atau kegagalan kepada pengguna dalam bahasa sederhana.

AI tidak melakukan:
- Memilih URL atau domain di luar daftar website terdaftar
- Membuat kode yang langsung dieksekusi
- Mengakses kredensial, cookie, atau token
- Menyetujui tindakan sendiri

### 6.2 Data yang Dikirim ke AI

Prompt sistem menyertakan:
- Daftar website dalam bentuk `website_id`, `name`, dan daftar `allowed_actions` (tanpa URL dan tanpa kredensial)
- Daftar template perintah yang tersedia
- Instruksi sistem dari pengaturan AI
- Skema JSON keluaran
- Instruksi pengguna

Daftar yang tidak boleh dikirim:
- Username, password, cookie, token sesi, API key, dan secret lain
- URL login
- Isi halaman mentah yang tidak diperlukan
- Data pribadi di luar kebutuhan tugas

### 6.3 Skema Keluaran

Skema dipaksa melalui fitur structured output Gemini (response MIME type JSON dengan response schema), lalu divalidasi ulang oleh backend.

```json
{
  "type": "object",
  "required": ["intent", "website_id", "action", "risk_level", "requires_approval", "parameters"],
  "properties": {
    "intent": { "type": "string", "enum": ["execute_job", "clarify", "reject"] },
    "website_id": { "type": ["string", "null"] },
    "action": {
      "type": ["string", "null"],
      "enum": ["extract_data", "take_screenshot", "test_login", "fill_form", "submit_form", null]
    },
    "risk_level": { "type": "string", "enum": ["read", "write_low", "write_high"] },
    "requires_approval": { "type": "boolean" },
    "parameters": { "type": "object" },
    "clarification_question": { "type": ["string", "null"] },
    "user_summary": { "type": "string", "maxLength": 500 }
  },
  "additionalProperties": false
}
```

### 6.4 Validasi Backend

Setiap keluaran AI melewati urutan pemeriksaan berikut. Kegagalan pada langkah mana pun menghentikan proses:

1. **Parsing JSON.** Respons harus dapat di-parse dan sesuai skema.
2. **Whitelist website.** `website_id` harus ada dan berstatus `active`.
3. **Whitelist action.** `action` harus termasuk `allowed_actions` untuk website tersebut.
4. **Validasi parameter.** Parameter diperiksa terhadap skema khusus per action (tipe data, rentang tanggal, panjang maksimum, karakter yang diizinkan).
5. **Kebijakan risiko.** `risk_level` yang dikembalikan AI diabaikan. Backend menghitung ulang risiko dari tabel kebijakan (bagian 5.6).
6. **Kebijakan AI.** Jika izin tulis AI nonaktif dan action adalah tulis, usulan ditolak.
7. **Persetujuan.** `requires_approval` dari AI diabaikan. Backend menentukan sendiri berdasarkan risiko.

Catatan: langkah 5 dan 7 penting. Keputusan keamanan tidak boleh bergantung pada apa yang dikatakan AI.

### 6.5 Konfigurasi Model

| Parameter | Default | Keterangan |
|---|---|---|
| `model_id` | dipilih pengguna dari daftar dinamis | Divalidasi terhadap daftar model yang tersedia |
| `temperature` | 0.1 | Rendah untuk konsistensi |
| `max_output_tokens` | 1024 | Cukup untuk skema tindakan |
| `timeout_seconds` | 30 | Jika terlampaui, dianggap error sementara |
| `fallback_model_id` | null | Opsional |
| `retry_on_invalid_json` | 1 kali | Dengan prompt perbaikan yang lebih ketat |

### 6.6 Penanganan Error AI

| Kondisi | Respons sistem |
|---|---|
| API key tidak valid | Tandai konfigurasi `invalid`, beri tahu owner, nonaktifkan AI |
| Model tidak ditemukan atau dihentikan | Coba model cadangan jika ada, atau minta pengguna memilih ulang |
| Rate limit (429) | Retry dengan backoff, maksimal 3 kali |
| Timeout | Retry sekali. Jika gagal, minta pengguna memakai perintah terstruktur |
| JSON tidak valid | Retry sekali dengan prompt perbaikan. Jika gagal, tolak dengan pesan ramah |
| Keluaran di luar whitelist | Tolak dan catat di audit log sebagai `ai_policy_violation` |

### 6.7 Mode Perintah Terstruktur

Untuk pengguna atau situasi yang membutuhkan kepastian, sistem menyediakan perintah tanpa AI:

```
/data site_01 range=today
/screenshot site_01 page=dashboard
/fill site_01 template=update_profile draft=123
```

Perintah terstruktur tidak membutuhkan Gemini dan tetap melewati validasi kebijakan yang sama.

### 6.8 Perlindungan dari Prompt Injection

Konten halaman website bersifat tidak tepercaya. Jika hasil ekstraksi dikirim ke AI untuk diringkas:

- Konten dibungkus dalam blok data yang diberi label jelas, misalnya `<untrusted_page_content>...</untrusted_page_content>`.
- Instruksi sistem menegaskan bahwa isi blok tersebut hanya data dan tidak boleh diikuti sebagai perintah.
- Keluaran AI tetap melewati validasi (bagian 6.4). Walaupun AI tertipu, tindakan tidak dapat melewati kebijakan.

---

## 7. Telegram Bot (Detail)

### 7.1 Keamanan Akses

- Webhook menggunakan `secret_token` yang divalidasi pada header `X-Telegram-Bot-Api-Secret-Token`.
- Pesan dari user ID yang tidak terdaftar dibalas dengan pesan generik tanpa informasi sistem, lalu dicatat di audit log.
- Setiap pesan diberi batas laju (misalnya 20 pesan per menit per user).
- Bot tidak menerima kredensial. Jika pengguna mengirim password atau API key di chat, bot menghapus pesan jika memungkinkan, memberi peringatan, dan menganggap nilai tersebut sudah bocor sehingga owner disarankan mengganti kredensial tersebut.

### 7.2 Daftar Perintah

| Perintah | Peran minimum | Fungsi |
|---|---|---|
| `/help` | viewer | Bantuan penggunaan |
| `/websites` | viewer | Daftar website dan statusnya |
| `/status` | viewer | Ringkasan job aktif dan kesehatan worker |
| `/jobs [n]` | viewer | Riwayat n job terakhir (default 10) |
| `/data <site_id> [range=...]` | operator | Membuat job ekstraksi |
| `/screenshot <site_id> [page=...]` | operator | Membuat job screenshot |
| `/test <site_id>` | operator | Menguji login |
| `/fill <site_id> template=... draft=...` | operator | Membuat job pengisian form (memerlukan persetujuan) |
| `/approve <job_id>` | operator | Menyetujui (alternatif tombol) |
| `/reject <job_id>` | operator | Menolak |
| `/cancel <job_id>` | operator | Membatalkan job |
| `/schedules` | viewer | Daftar jadwal aktif |
| `/ask <teks>` | operator | Mengirim instruksi bahasa Indonesia ke AI Gateway |

### 7.3 Alur Persetujuan di Telegram

Contoh pesan pratinjau:

```
⚠️ Persetujuan diperlukan

Website : Site A
Akun    : akun-utama
Aksi    : Isi dan simpan form pembaruan
Job ID  : 01J9X...K3

Perubahan:
• Nama tampilan : "Budi" → "Budi Santoso"
• Kontak        : (tidak berubah)

Dampak: data profil akan diubah di website Site A.
Berlaku sampai: 14:35 WIB (30 menit)

[ ✅ Setujui ]  [ ❌ Tolak ]  [ 🔍 Lihat detail ]
```

Batasan teknis:
- `callback_data` pada Telegram maksimal 64 byte. Oleh karena itu, callback hanya berisi `a:<approval_id_pendek>:<aksi>` dan token asli tidak dimasukkan ke callback.
- Verifikasi keputusan dilakukan melalui kombinasi approval_id, Telegram user ID, dan token yang dikirim dalam pesan terenkripsi di sisi server. Untuk versi 1.0, cukup gunakan `approval_id` acak (UUID v4) yang memiliki masa berlaku dan verifikasi Telegram user ID.
- Tombol tidak dapat ditekan ulang setelah keputusan. Pesan diperbarui menjadi "Disetujui oleh @username pada 14:31" atau sejenisnya.

### 7.4 Notifikasi

| Peristiwa | Dikirim ke | Isi |
|---|---|---|
| Job selesai | Pembuat job | Ringkasan, tautan artefak |
| Approval dibutuhkan | Semua operator aktif | Pratinjau dan tombol |
| Approval kedaluwarsa | Pembuat dan operator | Pemberitahuan singkat |
| Login gagal beruntun (3x) | Owner | Nama website dan waktu |
| Perlu tindakan manual (CAPTCHA/MFA) | Pembuat job dan owner | Instruksi buka dashboard |
| Jadwal gagal | Owner | Nama jadwal dan kode error |
| Model AI gagal | Owner | Nama model dan kode error |

Notifikasi dibatasi agar tidak spam: maksimal satu notifikasi per job per peristiwa, dan ringkasan harian bila jumlah notifikasi melebihi 20 dalam satu jam.

### 7.5 Pesan Kesalahan yang Ramah

Contoh:

```
❌ Login ke Site A gagal.
Penyebab yang mungkin: password berubah atau akun dikunci.
Tindakan: periksa kredensial di dashboard. Bot tidak akan mencoba lagi
sampai kredensial diperbarui.
```

Pesan tidak boleh berisi stack trace, URL internal, selector, atau nilai parameter sensitif.

---

## 8. Rancangan Data

### 8.1 Entitas dan Kolom Utama

**users**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | Primary key |
| email | VARCHAR(255) | Unik |
| password_hash | TEXT | Argon2id |
| totp_secret_enc | BYTEA | Terenkripsi, nullable |
| role | ENUM | owner, operator, viewer |
| is_active | BOOLEAN | |
| failed_login_count | INT | |
| locked_until | TIMESTAMPTZ | |
| created_at, updated_at | TIMESTAMPTZ | |

**telegram_users**

| Kolom | Tipe | Keterangan |
|---|---|---|
| telegram_user_id | BIGINT | Primary key |
| user_id | UUID | FK ke users (nullable) |
| display_name | VARCHAR(100) | |
| role | ENUM | owner, operator, viewer |
| is_active | BOOLEAN | |
| created_at | TIMESTAMPTZ | |

**websites**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | VARCHAR(32) | Primary key, misalnya `site_01` |
| name | VARCHAR(100) | |
| base_url | TEXT | HTTPS |
| adapter_id | VARCHAR(64) | Mengarah ke modul adapter |
| adapter_version | VARCHAR(32) | |
| adapter_config | JSONB | Selector, timeout, rate limit |
| allowed_actions | TEXT[] | Whitelist tindakan |
| status | ENUM | active, disabled, needs_attention |
| last_login_ok_at | TIMESTAMPTZ | |
| last_login_error | VARCHAR(64) | Kode error, bukan pesan mentah |
| created_at, updated_at | TIMESTAMPTZ | |

**website_credentials**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | |
| website_id | VARCHAR(32) | FK, unik untuk status aktif |
| username_enc | BYTEA | |
| password_enc | BYTEA | |
| key_version | INT | Versi kunci enkripsi |
| rotated_at | TIMESTAMPTZ | |
| is_active | BOOLEAN | |

**ai_configs**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | |
| provider | VARCHAR(32) | Nilai awal: `gemini` |
| api_key_enc | BYTEA | |
| api_key_last4 | VARCHAR(4) | Untuk tampilan |
| primary_model_id | VARCHAR(128) | |
| fallback_model_id | VARCHAR(128) | Nullable |
| temperature | NUMERIC(3,2) | |
| max_output_tokens | INT | |
| timeout_seconds | INT | |
| mode | ENUM | structured_only, hybrid |
| allow_ai_write_proposals | BOOLEAN | Default false |
| system_instruction | TEXT | |
| status | ENUM | valid, invalid, unverified |
| updated_at | TIMESTAMPTZ | |

**jobs**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID v7 | Primary key |
| website_id | VARCHAR(32) | FK |
| action | VARCHAR(64) | |
| risk_level | ENUM | read, write_low, write_high |
| source | ENUM | telegram, dashboard, schedule, ai |
| requested_by | UUID | Nullable |
| requested_by_telegram | BIGINT | Nullable |
| payload | JSONB | Parameter tervalidasi |
| payload_hash | CHAR(64) | SHA-256 |
| idempotency_key | VARCHAR(128) | Unik dalam jendela waktu |
| status | ENUM | Lihat bagian 5.1 |
| adapter_version | VARCHAR(32) | |
| attempt_count | INT | |
| max_attempts | INT | |
| cancel_requested | BOOLEAN | |
| last_error_code | VARCHAR(64) | |
| last_error_message | TEXT | Sudah disanitasi |
| started_at, finished_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

**approvals**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | |
| job_id | UUID | FK, unik |
| approval_hash | CHAR(64) | Hash payload saat pratinjau |
| token_hash | CHAR(64) | SHA-256 dari token |
| preview_summary | JSONB | Ringkasan yang ditampilkan |
| status | ENUM | pending, approved, rejected, expired, superseded |
| expires_at | TIMESTAMPTZ | |
| decided_by_telegram | BIGINT | Nullable |
| decided_by_user | UUID | Nullable |
| decided_at | TIMESTAMPTZ | |
| decision_reason | TEXT | Opsional |

**schedules**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | |
| name | VARCHAR(100) | |
| website_id | VARCHAR(32) | |
| action | VARCHAR(64) | Harus berisiko `read` |
| payload | JSONB | |
| cron_expr | VARCHAR(64) | |
| timezone | VARCHAR(64) | IANA |
| is_active | BOOLEAN | |
| misfire_policy | ENUM | run_once_latest, skip |
| next_run_at | TIMESTAMPTZ | Indeks untuk scheduler |
| last_run_at | TIMESTAMPTZ | |

**artifacts**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | UUID | |
| job_id | UUID | FK |
| website_id | VARCHAR(32) | |
| kind | ENUM | screenshot, extract_json, extract_csv |
| storage_key | TEXT | Kunci di object storage |
| checksum_sha256 | CHAR(64) | |
| size_bytes | BIGINT | |
| pinned | BOOLEAN | |
| expires_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

**audit_logs**

| Kolom | Tipe | Keterangan |
|---|---|---|
| id | BIGSERIAL | |
| occurred_at | TIMESTAMPTZ | |
| actor_type | ENUM | user, telegram, scheduler, system, ai |
| actor_id | VARCHAR(64) | |
| action | VARCHAR(64) | Misalnya `job.created`, `approval.approved` |
| target_type | VARCHAR(32) | |
| target_id | VARCHAR(64) | |
| result | ENUM | success, failure, denied |
| metadata | JSONB | Hanya field yang sudah disaring |
| request_id | UUID | |

Audit log bersifat append-only. Akun aplikasi tidak memiliki izin UPDATE atau DELETE pada tabel ini.

**browser_sessions** (opsional, untuk optimasi)

| Kolom | Tipe | Keterangan |
|---|---|---|
| website_id | VARCHAR(32) | PK |
| state_enc | BYTEA | Storage state Playwright terenkripsi |
| expires_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Tabel ini tidak wajib pada v1.0. Login ulang setiap job lebih aman, tetapi lebih lambat dan lebih sering memicu verifikasi tambahan dari website.

### 8.2 Indeks dan Constraint

- `jobs(status, created_at)` untuk antrean dan dashboard.
- `jobs(idempotency_key)` unik dalam jendela waktu, diverifikasi di aplikasi.
- `schedules(next_run_at) WHERE is_active`.
- `website_credentials(website_id) WHERE is_active` unik parsial.
- `audit_logs(occurred_at)` dan `audit_logs(target_type, target_id)`.

---

## 9. Keamanan (Detail)

### 9.1 Manajemen Secret

| Secret | Lokasi penyimpanan | Catatan |
|---|---|---|
| Master encryption key | Environment secret Render (atau secret manager) | Dirotasi setiap 90 hari atau saat dicurigai bocor |
| Telegram bot token | Environment secret | Jangan pernah dicetak di log |
| Telegram webhook secret | Environment secret | Divalidasi setiap request |
| Gemini API key | Terenkripsi di `ai_configs` | Didekripsi hanya saat memanggil AI |
| Kredensial website | Terenkripsi di `website_credentials` | Didekripsi hanya oleh worker |
| Database URL | Environment secret | |
| Session secret dashboard | Environment secret | |

### 9.2 Enkripsi

- Algoritma: AES-256-GCM dengan nonce acak 96-bit untuk setiap nilai.
- Format penyimpanan: `key_version || nonce || ciphertext || tag`.
- Penambahan AAD (additional authenticated data) berupa nama tabel dan ID record, sehingga ciphertext tidak dapat dipindahkan antar record.
- Rotasi kunci: data baru dienkripsi dengan versi terbaru. Data lama didekripsi dengan versi yang tercatat, lalu dienkripsi ulang melalui job maintenance.

### 9.3 Pembersihan Log dan Respons

Filter diterapkan pada logger dan serializer respons API:
- Menghapus field dengan nama `password`, `api_key`, `token`, `secret`, `cookie`, `authorization`.
- Mengganti pola yang menyerupai API key Gemini (`AIza[0-9A-Za-z_-]{35}`) dengan placeholder.
- Mengganti header `Authorization`, `Cookie`, dan `Set-Cookie`.
- Mengganti pesan error mentah dari Playwright yang mungkin memuat nilai input.

Pengujian wajib: unit test yang memastikan password dummy tidak muncul di log untuk skenario login gagal, timeout, dan exception.

### 9.4 Pencegahan SSRF

Worker hanya boleh membuka URL yang:
- Berasal dari `base_url`, `login.url`, atau `dashboard_url` yang tersimpan untuk website tersebut
- Memakai HTTPS
- Domainnya cocok dengan allowlist website

Navigasi ke domain lain diblokir pada level browser (request interception). Redirect ke domain di luar allowlist dihentikan dan job ditandai `failed` dengan kode `NAVIGATION_BLOCKED`.

Selain itu, backend menolak URL yang mengarah ke alamat privat atau loopback (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, link-local, dan metadata cloud) saat pengguna memasukkan URL.

### 9.5 Kontrol Akses

- Dashboard: RBAC berdasarkan role (bagian 1.5).
- API: setiap endpoint menerapkan dependency `require_role(...)` dan memeriksa kepemilikan sumber daya.
- Telegram: allowlist user ID dan role.
- Database: pengguna aplikasi terpisah untuk API, worker, dan migrasi, dengan hak minimum.
- Audit log: hanya INSERT dan SELECT untuk pengguna aplikasi.

### 9.6 Perlindungan Aplikasi Web

- CSRF token untuk semua request state-changing dari dashboard.
- Rate limiting pada endpoint login, endpoint AI test, dan endpoint pembuatan job.
- Header keamanan: `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`.
- Validasi input dengan Pydantic (backend) dan Zod (frontend), tetapi validasi backend tetap menjadi sumber kebenaran.
- Upload file tidak diperlukan pada v1.0. Jika ditambahkan kemudian, wajib validasi tipe dan ukuran.

### 9.7 Ancaman dan Mitigasi

| Ancaman | Mitigasi |
|---|---|
| Pengguna Telegram tidak sah mengirim perintah | Allowlist user ID, validasi webhook secret |
| Replay persetujuan | Token single-use, status berubah setelah keputusan |
| Perubahan parameter setelah persetujuan | Verifikasi hash payload saat eksekusi |
| Prompt injection dari konten website | Pemisahan data tidak tepercaya, validasi keluaran AI, kebijakan di mesin |
| AI mengusulkan website atau aksi di luar daftar | Whitelist server-side |
| Kebocoran kredensial di log | Filter log, pengujian otomatis |
| Kebocoran database | Enkripsi kolom sensitif, akses database terbatas |
| Job ganda untuk tindakan tulis | Idempotency key, lock per website, pemeriksaan status sebelum eksekusi |
| Akun website terkunci akibat retry | Batas retry login, circuit breaker per website |
| Screenshot memuat data sensitif | Redaksi selector sensitif, akses artefak melalui URL bertanda tangan |
| Penyalahgunaan dashboard karena sesi dicuri | Cookie HttpOnly, 2FA, sesi pendek, opsi logout dari semua perangkat |

---

## 10. Automation Worker dan Adapter

### 10.1 Kontrak Adapter

Setiap website diimplementasikan sebagai kelas yang memenuhi kontrak berikut:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class AdapterContext:
    job_id: str
    website_id: str
    page: "Page"              # Playwright page, sudah dalam sesi terisolasi
    config: dict[str, Any]    # adapter_config dari database
    credentials: "Credentials"  # hanya tersedia di dalam adapter, tidak dicatat

class SiteAdapter(ABC):
    adapter_id: str
    version: str

    @abstractmethod
    async def login(self, ctx: AdapterContext) -> None:
        """Melakukan login. Melempar LoginError jika gagal."""

    @abstractmethod
    async def ensure_session(self, ctx: AdapterContext) -> None:
        """Memastikan sesi valid. Melakukan login ulang bila perlu."""

    async def extract(self, ctx: AdapterContext, params: dict) -> dict:
        raise NotImplementedError

    async def screenshot(self, ctx: AdapterContext, params: dict) -> bytes:
        raise NotImplementedError

    async def prepare_form(self, ctx: AdapterContext, params: dict) -> "FormPreview":
        """Mengisi form tanpa menyimpan. Mengembalikan pratinjau perubahan."""
        raise NotImplementedError

    async def execute_form(self, ctx: AdapterContext, params: dict, preview_hash: str) -> "FormResult":
        """Mengisi ulang dan menyimpan setelah persetujuan. Memverifikasi hasil."""
        raise NotImplementedError
```

Adapter tidak boleh:
- Mencetak kredensial, cookie, atau HTML lengkap ke log
- Membuka URL di luar konfigurasi
- Menekan tombol yang tidak didefinisikan dalam adapter
- Melewati pemeriksaan persetujuan

### 10.2 Alur Eksekusi Worker

```
1. Ambil job dari antrean
2. Periksa cancel_requested → jika ya, batalkan
3. Acquire lock website:{id} (Redis, TTL 20 menit, dengan heartbeat)
   - Jika gagal, kembalikan job ke antrean dengan delay 30 detik
4. Muat konfigurasi website dan dekripsi kredensial (dalam memori)
5. Buka browser context baru (terisolasi, tanpa penyimpanan permanen)
6. Jalankan adapter.ensure_session() → login jika perlu
7. Jalankan tindakan:
   - read → extract / screenshot → simpan artefak → succeeded
   - write → prepare_form → simpan preview → awaiting_approval → tutup browser
8. Setelah persetujuan:
   - Verifikasi status job dan approval_hash
   - Buka browser baru → ensure_session → execute_form
   - Verifikasi hasil → succeeded / failed / needs_manual_action
9. Tutup browser context, hapus data sementara, lepaskan lock
10. Tulis audit log
```

### 10.3 Penanganan Login

- Login hanya dicoba maksimal 2 kali per job.
- Setelah tiga kegagalan login berturut-turut pada satu website, status website menjadi `needs_attention` dan job lanjutan diblokir sampai owner memeriksa kredensial.
- Deteksi keberhasilan menggunakan `success_indicator`. Deteksi gagal menggunakan `failure_indicator`. Jika keduanya tidak ditemukan dalam timeout, dianggap `LOGIN_UNKNOWN_STATE`.
- Jika terdeteksi CAPTCHA, MFA, atau verifikasi perangkat (lewat selector atau URL yang dikenali adapter), job berhenti dengan `needs_manual_action`. Pengguna diberi tahu untuk login manual di dashboard atau lewat sesi yang disediakan. Sistem tidak mencoba menyelesaikannya.

### 10.4 Rate Limit dan Perilaku Browser

- Jeda minimum antar-request ke satu website sesuai `min_interval_seconds`.
- Jeda acak kecil (jitter 0.5–2 detik) antara aksi manusia-simulasi, tetapi tidak digunakan untuk menghindari deteksi. Tujuannya hanya mencegah beban berlebihan.
- Tidak mengunduh aset yang tidak diperlukan (gambar, font, media). Request yang tidak perlu diblokir untuk menghemat RAM dan bandwidth.
- Browser memakai user agent standar dan tidak menyamarkan identitas.

### 10.5 Ekstraksi Data

- Hasil ekstraksi dinormalisasi ke skema JSON yang didefinisikan per adapter, misalnya:

```json
{
  "site_id": "site_01",
  "extracted_at": "2026-10-29T08:00:00+07:00",
  "date_range": "today",
  "summary": {
    "orders": 14,
    "revenue": 2450000,
    "currency": "IDR"
  },
  "adapter_version": "1.2.0"
}
```

- Angka dan tanggal diparsing dengan aturan khusus per website (format desimal, pemisah ribuan, zona waktu).
- Jika parsing gagal untuk field wajib, job ditandai `failed` dengan `PARSE_ERROR` dan HTML tidak disimpan di artefak.

### 10.6 Pengisian Form

- Setiap field form memiliki aturan validasi di adapter (tipe, panjang, pola, nilai yang diizinkan).
- Nilai yang tidak valid dihentikan sebelum pengisian.
- Pratinjau berisi nilai lama dan baru untuk setiap field yang berubah.
- Saat `execute_form`, worker memeriksa ulang nilai lama. Jika nilai lama sudah berbeda dari saat pratinjau, eksekusi dibatalkan dengan `PREVIEW_MISMATCH`.
- Setelah menyimpan, adapter membaca kembali data dan membandingkannya dengan nilai yang diminta. Jika tidak cocok, status menjadi `failed` dengan `VERIFY_FAILED` dan screenshot diambil untuk diagnosis.

### 10.7 Penanganan Error Standar

| Kode | Arti | Retry | Tindakan |
|---|---|---|---|
| `LOGIN_FAILED` | Kredensial ditolak | Tidak | Set `needs_attention` |
| `LOGIN_UNKNOWN_STATE` | Tidak jelas berhasil atau gagal | 1 kali | Screenshot diagnosis |
| `NEEDS_MANUAL_ACTION` | CAPTCHA/MFA | Tidak | Notifikasi |
| `SESSION_EXPIRED` | Sesi berakhir di tengah job | 1 kali (login ulang) | Lanjutkan |
| `TIMEOUT` | Melebihi batas waktu | Ya, max 2 | Backoff |
| `SELECTOR_NOT_FOUND` | Struktur halaman berubah | Tidak | Set adapter `needs_attention` |
| `PARSE_ERROR` | Data tidak sesuai format | Tidak | Simpan konteks non-sensitif |
| `NAVIGATION_BLOCKED` | URL di luar allowlist | Tidak | Catat sebagai pelanggaran |
| `PREVIEW_MISMATCH` | Data berubah sejak pratinjau | Tidak | Minta pratinjau ulang |
| `VERIFY_FAILED` | Hasil simpan tidak sesuai | Tidak | Screenshot, notifikasi |
| `RATE_LIMITED_BY_SITE` | Website membatasi akses | Backoff panjang (15 menit) | Kurangi frekuensi |
| `WORKER_CRASH` | Proses mati di tengah | Ya, dengan pemeriksaan state | Job ditandai untuk audit |

---

## 11. Job Engine, Antrean, dan Scheduler

### 11.1 Antrean

- Redis menyimpan antrean dengan tiga prioritas: `high` (perintah manual), `normal` (jadwal), `low` (screenshot massal).
- Job yang sedang berjalan dipantau dengan heartbeat setiap 30 detik. Jika heartbeat berhenti lebih dari 3 menit, job ditandai `failed` dengan `WORKER_CRASH` dan dapat diulang sesuai aturan retry.

### 11.2 Kebijakan Retry

| Jenis error | Maksimum percobaan | Backoff |
|---|---|---|
| Timeout jaringan | 3 | Eksponensial: 30 s, 90 s, 270 s dengan jitter |
| Error 5xx dari website | 3 | Sama dengan di atas |
| Rate limit | 2 | 15 menit |
| Login gagal | 1 | Tidak ada retry tambahan |
| Error tulis | 1 | Tidak diulang otomatis. Memerlukan pengguna untuk memutuskan |

Aturan khusus: untuk tindakan tulis, retry hanya dilakukan jika worker dapat membuktikan bahwa perubahan belum tersimpan (dengan membaca data terlebih dahulu). Jika tidak dapat dibuktikan, job masuk `needs_manual_action`.

### 11.3 Idempotensi

- Setiap job memiliki `idempotency_key`. Untuk jadwal: `schedule:{schedule_id}:{scheduled_for_iso}`. Untuk perintah manual: hash dari pengguna, perintah, dan jendela 60 detik.
- Pembuatan job yang memiliki key sama dalam 10 menit terakhir mengembalikan job yang sudah ada.

### 11.4 Penjadwalan

- Scheduler berjalan sebagai proses terpisah dengan loop setiap 30 detik.
- Mengambil jadwal dengan `next_run_at <= now()` menggunakan `SELECT ... FOR UPDATE SKIP LOCKED` untuk mencegah dua instance scheduler membuat job yang sama.
- Setelah job dibuat, `next_run_at` dihitung ulang berdasarkan cron dan zona waktu, lalu disimpan dalam transaksi yang sama.
- Pergantian zona waktu (misalnya DST) ditangani dengan library cron yang menghitung dalam zona IANA. Untuk Indonesia (WIB, tanpa DST), tetap gunakan `Asia/Jakarta` agar konsisten.

### 11.5 Misfire

- `run_once_latest`: jika layanan mati dan beberapa run terlewat, hanya satu job dibuat saat layanan kembali, lalu jadwal dihitung ulang dari waktu sekarang.
- `skip`: run yang terlewat diabaikan.

### 11.6 Lock dan Konkurensi

- Maksimal satu sesi browser aktif per website.
- Maksimal tiga worker paralel secara keseluruhan pada versi awal (dapat dinaikkan setelah uji beban).
- Lock disimpan di Redis dengan TTL dan heartbeat. Jika worker mati, lock berakhir otomatis setelah TTL.

---

## 12. Spesifikasi API

### 12.1 Konvensi

- Base path: `/api/v1`
- Format: JSON, UTF-8
- Autentikasi: cookie sesi (dashboard) atau header `Authorization: Bearer` untuk integrasi internal (opsional)
- Waktu: ISO 8601 dengan offset zona waktu
- ID: UUID, kecuali `website_id` yang berupa slug (misalnya `site_01`)
- Pagination: `?limit=20&cursor=...`

Format error standar:

```json
{
  "error": {
    "code": "WEBSITE_NOT_ALLOWED",
    "message": "Website tidak aktif atau tidak terdaftar.",
    "request_id": "7f3c..."
  }
}
```

### 12.2 Daftar Endpoint

| Method | Endpoint | Role | Fungsi |
|---|---|---|---|
| POST | `/auth/login` | Publik (rate limit) | Login dashboard |
| POST | `/auth/logout` | Terautentikasi | Logout |
| POST | `/auth/totp/verify` | Terautentikasi | Verifikasi 2FA |
| GET | `/websites` | viewer+ | Daftar website |
| POST | `/websites` | owner | Tambah website |
| GET | `/websites/{id}` | viewer+ | Detail (tanpa kredensial) |
| PUT | `/websites/{id}` | owner | Perbarui konfigurasi |
| PATCH | `/websites/{id}/status` | owner | Aktif/nonaktif |
| PUT | `/websites/{id}/credentials` | owner | Set atau ganti kredensial (tanpa membaca balik) |
| POST | `/websites/{id}/test-login` | operator+ | Membuat job test login |
| GET | `/jobs` | viewer+ | Daftar job dengan filter |
| POST | `/jobs` | operator+ | Membuat job |
| GET | `/jobs/{id}` | viewer+ | Status dan hasil |
| POST | `/jobs/{id}/cancel` | operator+ | Membatalkan |
| GET | `/approvals` | operator+ | Daftar pending |
| POST | `/approvals/{id}/approve` | operator+ | Setujui (dengan hash) |
| POST | `/approvals/{id}/reject` | operator+ | Tolak |
| GET | `/artifacts/{id}/url` | viewer+ | URL bertanda tangan sementara |
| GET | `/ai/config` | owner | Konfigurasi AI (API key termasking) |
| PUT | `/ai/config` | owner | Perbarui konfigurasi AI |
| GET | `/ai/models` | owner | Daftar model dari Gemini |
| POST | `/ai/test` | owner | Uji koneksi dan format JSON |
| POST | `/ai/interpret` | operator+ | Interpretasi prompt (dipanggil dari Telegram atau dashboard) |
| GET | `/schedules` | viewer+ | Daftar jadwal |
| POST | `/schedules` | owner | Buat jadwal |
| PUT | `/schedules/{id}` | owner | Ubah jadwal |
| DELETE | `/schedules/{id}` | owner | Hapus (soft delete) |
| GET | `/audit-logs` | owner | Audit log dengan filter |
| POST | `/telegram/webhook` | Secret token | Webhook Telegram |
| GET | `/healthz` | Publik | Liveness |
| GET | `/readyz` | Internal | Readiness |

### 12.3 Contoh Detail Endpoint

**GET /ai/models**

Respons:

```json
{
  "provider": "gemini",
  "fetched_at": "2026-10-29T08:00:00+07:00",
  "models": [
    {
      "id": "models/gemini-2.5-flash",
      "display_name": "Gemini 2.5 Flash",
      "supports_json_output": true,
      "supports_text_generation": true,
      "input_token_limit": 1048576,
      "output_token_limit": 65536
    }
  ]
}
```

Catatan: contoh nama model hanya ilustrasi. Respons nyata harus berasal dari API.

Aturan: daftar di-cache maksimal 1 jam. Jika pemanggilan gagal, tampilkan pesan dan pilihan terakhir yang masih valid.

**POST /jobs**

Permintaan:

```json
{
  "website_id": "site_01",
  "action": "extract_data",
  "payload": { "date_range": "today" },
  "idempotency_key": "client-generated-uuid"
}
```

Respons `202 Accepted`:

```json
{
  "job_id": "01J9XK3...",
  "status": "queued",
  "created_at": "2026-10-29T08:01:12+07:00"
}
```

Error yang mungkin: `WEBSITE_NOT_ALLOWED` (403), `ACTION_NOT_ALLOWED` (403), `VALIDATION_ERROR` (422), `DUPLICATE_REQUEST` (409, mengembalikan job_id yang ada).

**POST /approvals/{id}/approve**

Permintaan:

```json
{
  "approval_hash": "a3f1...e9"
}
```

Respons `200 OK`:

```json
{
  "approval_id": "...",
  "status": "approved",
  "job_status": "executing"
}
```

Error: `APPROVAL_EXPIRED` (410), `APPROVAL_ALREADY_DECIDED` (409), `PREVIEW_CHANGED` (409), `FORBIDDEN` (403).

**PUT /websites/{id}/credentials**

Permintaan:

```json
{
  "username": "akun@example.com",
  "password": "********"
}
```

Respons `204 No Content`. Tidak ada endpoint untuk membaca kredensial kembali.

### 12.4 Rate Limit API

| Endpoint | Batas |
|---|---|
| `/auth/login` | 5 per menit per IP, dan 10 per jam per email |
| `/ai/test`, `/ai/interpret` | 20 per menit per pengguna |
| `/jobs` (POST) | 30 per menit per pengguna |
| Endpoint lainnya | 120 per menit per pengguna |

---

## 13. Frontend (Detail)

### 13.1 Halaman

| Route | Isi |
|---|---|
| `/dashboard` | Ringkasan website, status login, job aktif, jadwal berikutnya, aktivitas terbaru, approval pending |
| `/websites` | Daftar website, tambah, edit, uji koneksi |
| `/websites/[id]` | Detail konfigurasi, selector, riwayat login, kredensial (form ganti saja) |
| `/ai-settings` | Konfigurasi Gemini |
| `/schedules` | Daftar dan editor jadwal |
| `/jobs` | Riwayat job dengan filter |
| `/jobs/[id]` | Detail job, log yang disanitasi, artefak |
| `/approvals` | Daftar persetujuan pending dengan pratinjau |
| `/audit` | Audit log (owner) |
| `/settings/telegram` | Kelola Telegram user ID yang diizinkan |
| `/login` | Login dan 2FA |

### 13.2 Perilaku Kunci

- **Halaman persetujuan** menampilkan pratinjau yang sama dengan yang dikirim ke Telegram. Tombol Setujui dan Tolak hanya aktif jika status masih `pending`.
- **Status job** diperbarui melalui polling setiap 3 detik untuk job aktif, atau melalui Server-Sent Events jika tersedia. Polling berhenti saat job selesai.
- **Form kredensial** tidak menampilkan nilai lama. Field kosong berarti tidak diubah.
- **Form AI** menampilkan dropdown model dari `/ai/models`. Jika daftar kosong atau gagal dimuat, dropdown dinonaktifkan dan pesan kesalahan ditampilkan.
- **Tampilan API key** hanya menunjukkan 4 karakter terakhir.

### 13.3 Aksesibilitas dan Responsivitas

- Dapat digunakan di desktop dan tablet. Tampilan mobile dasar untuk persetujuan dan dashboard.
- Mengikuti WCAG 2.1 level AA untuk kontras dan navigasi keyboard.
- Bahasa antarmuka Indonesia.

---

## 14. Deployment di Render

### 14.1 Layanan

| Layanan | Tipe Render | Catatan |
|---|---|---|
| frontend | Web Service (Node) | Next.js dengan output standalone |
| api | Web Service (Docker) | FastAPI + webhook Telegram |
| worker | Background Worker (Docker) | Image Playwright resmi, RQ/Celery worker |
| scheduler | Background Worker (Docker) | Proses terpisah, hanya membuat job |
| db | PostgreSQL | Backup otomatis sesuai paket |
| redis | Key Value (Redis-compatible) | Antrean dan lock |
| object storage | Layanan eksternal S3-compatible | Screenshot dan artefak |

Catatan: periksa kapasitas layanan Redis dan kebijakan paket Render terkini sebelum produksi. Nama tipe layanan dapat berubah.

### 14.2 Dockerfile Worker (Garis Besar)

- Base image: `mcr.microsoft.com/playwright/python` dengan versi yang cocok dengan library Playwright.
- Pengguna non-root.
- Instalasi dependensi dengan lock file.
- Tidak ada secret di dalam image.
- Entrypoint: menjalankan worker RQ atau Celery.

### 14.3 Environment Variables

```
APP_ENV=production
DATABASE_URL=                 # secret
REDIS_URL=                    # secret
ENCRYPTION_MASTER_KEY=        # secret, versi kunci dikelola terpisah
ENCRYPTION_KEY_VERSION=1
TELEGRAM_BOT_TOKEN=           # secret
TELEGRAM_WEBHOOK_SECRET=      # secret
TELEGRAM_OWNER_IDS=           # daftar ID awal
SESSION_SECRET=               # secret
PUBLIC_BASE_URL=https://api.example.com
DASHBOARD_BASE_URL=https://dash.example.com
ARTIFACT_STORAGE_URL=         # secret
ARTIFACT_STORAGE_KEY=         # secret
ARTIFACT_SIGNED_URL_TTL=900
LOG_LEVEL=INFO
BROWSER_MAX_CONCURRENCY=3
JOB_DEFAULT_TIMEOUT_SECONDS=900
APPROVAL_TTL_SECONDS=1800
```

### 14.4 Health Check dan Startup

- API: `/healthz` untuk liveness, `/readyz` untuk readiness (cek database, Redis).
- Worker: heartbeat ke Redis setiap 30 detik. Tidak memiliki endpoint HTTP publik.
- Migrasi database dijalankan sebagai job terpisah sebelum deploy versi baru.

### 14.5 Persistensi dan Backup

- Semua data penting di PostgreSQL. Aktifkan backup otomatis dan uji pemulihan minimal sekali per kuartal.
- Screenshot dan artefak di object storage dengan kebijakan retensi.
- Tidak menyimpan state browser di filesystem lokal. Jika `browser_sessions` digunakan, datanya tetap di PostgreSQL dan terenkripsi.
- Konfigurasi infrastruktur disimpan di `render.yaml` dalam repositori.

### 14.6 Biaya dan Kapasitas

- Browser memerlukan RAM. Uji konsumsi memori dengan 1, 2, dan 3 sesi paralel sebelum memilih paket.
- Perhatikan batas jam layanan, kebijakan tidur pada paket tertentu, dan biaya layanan tambahan.
- Scheduler dan API sebaiknya tidak tidur (spin-down) agar webhook dan jadwal tetap responsif. Periksa perilaku paket yang dipilih.

---

## 15. Observability dan Audit

### 15.1 Log Aplikasi

Format JSON:

```json
{
  "ts": "2026-10-29T08:01:12.345+07:00",
  "level": "INFO",
  "service": "worker",
  "request_id": "...",
  "job_id": "...",
  "website_id": "site_01",
  "event": "job.step.completed",
  "step": "extract",
  "duration_ms": 4210
}
```

### 15.2 Audit Log: Peristiwa Wajib

| Event | Keterangan |
|---|---|
| `auth.login.success/failure` | Dengan IP dan user agent (dipotong) |
| `website.created/updated/disabled` | Tanpa nilai kredensial |
| `credential.updated` | Hanya mencatat bahwa diubah |
| `credential.used` | Dicatat oleh worker, tanpa nilai |
| `ai.config.updated` | Tanpa API key |
| `ai.request.sent` | Model, durasi, token perkiraan. Tanpa isi prompt sensitif |
| `ai.policy_violation` | Usulan ditolak beserta alasan |
| `job.created/started/completed/failed/cancelled` | Dengan job_id dan status |
| `approval.created/approved/rejected/expired/superseded` | Dengan pelaku |
| `telegram.unauthorized_attempt` | Telegram user ID dan perintah |
| `schedule.created/updated/triggered` | |
| `navigation.blocked` | URL yang diblokir |

### 15.3 Metrik

- `jobs_total{status, action, website}`
- `job_duration_seconds{action, website}` (histogram)
- `login_failures_total{website}`
- `ai_requests_total{model, outcome}`
- `ai_latency_seconds{model}`
- `approvals_pending`
- `queue_depth{priority}`
- `browser_sessions_active`

### 15.4 Alert

| Kondisi | Tingkat | Tindakan |
|---|---|---|
| Login gagal 3x beruntun pada satu website | Warning | Notifikasi owner |
| Error rate job > 10% selama 15 menit | Warning | Notifikasi owner |
| Antrean > 20 job selama 10 menit | Warning | Periksa worker |
| Worker tidak mengirim heartbeat 3 menit | Critical | Restart otomatis dan notifikasi |
| Database tidak dapat dijangkau | Critical | Notifikasi, mode degradasi |
| Gemini API key invalid | Warning | Nonaktifkan AI, beri tahu owner |

---

## 16. Penanganan Kesalahan Umum

| Skenario | Perilaku sistem |
|---|---|
| Restart API saat job sedang dibuat | Transaksi memastikan job tersimpan atau tidak sama sekali. Pengguna dapat mengulang dengan idempotency key |
| Restart worker di tengah ekstraksi | Heartbeat berhenti, job ditandai `WORKER_CRASH`, diulang jika read-only |
| Restart worker saat `executing` | Job ditandai untuk pemeriksaan. Worker membaca state website sebelum memutuskan retry atau `needs_manual_action` |
| Persetujuan diklik dua kali | Keputusan kedua ditolak dengan `APPROVAL_ALREADY_DECIDED` |
| Pesan Telegram terkirim tetapi gagal dicatat | Pesan dapat dikirim ulang. Notifikasi memiliki id unik untuk deduplikasi |
| Layanan Telegram down | Webhook gagal, Telegram mengirim ulang sesuai kebijakannya. Backend memproses idempotent |
| Website mengubah struktur halaman | Selector gagal, adapter `needs_attention`, job lanjutan berhenti |
| Website meminta verifikasi tambahan | `needs_manual_action`, notifikasi |
| Gemini mengembalikan teks di luar JSON | Retry sekali, lalu tolak dengan pesan ramah |
| Kunci enkripsi diganti tanpa migrasi | Dekripsi gagal dengan `KEY_VERSION_UNKNOWN`. Owner diberi tahu, sistem menolak eksekusi |
| Penghapusan website yang memiliki job aktif | Ditolak. Website harus dinonaktifkan dan job dibatalkan terlebih dahulu |

---

## 17. Pengujian

### 17.1 Strategi

| Jenis | Cakupan | Alat |
|---|---|---|
| Unit | Validasi parameter, kebijakan risiko, hash, enkripsi, filter log | pytest |
| Integrasi | API + PostgreSQL + Redis, alur job dan approval | pytest, testcontainers |
| Adapter | Parsing dan selector pada fixture HTML | pytest, Playwright dengan HTML statis |
| End-to-end | Alur Telegram sampai eksekusi pada website uji | Website staging milik sendiri |
| Keamanan | Pengujian kebocoran secret, bypass approval, SSRF, prompt injection | pytest khusus, tinjauan manual |
| Beban | 3 worker paralel, antrean 50 job | Locust atau script internal |
| Ketahanan | Restart worker dan API di tengah job | Skenario manual terkontrol |

### 17.2 Kasus Uji Wajib

**Keamanan:**
- Password dummy tidak muncul di log, respons API, pesan Telegram, atau artefak.
- Job tulis tidak dieksekusi tanpa approval valid.
- Approval dengan hash lama ditolak setelah parameter berubah.
- Token persetujuan yang sudah dipakai tidak dapat digunakan lagi.
- Pengguna Telegram tidak terdaftar tidak dapat membuat job.
- Viewer tidak dapat membuat atau menyetujui job.
- AI tidak dapat memilih website yang tidak terdaftar.
- AI tidak dapat mengusulkan action di luar `allowed_actions`.
- Navigasi ke domain di luar allowlist diblokir.

**Fungsional:**
- Login berhasil dan gagal untuk setiap adapter.
- Ekstraksi menghasilkan JSON sesuai skema.
- Screenshot disimpan dengan metadata dan checksum.
- Pratinjau form menampilkan perubahan dengan benar.
- Eksekusi form menyimpan dan memverifikasi hasil.
- Jadwal dibuat dengan zona waktu benar dan `next_run_at` dihitung dengan tepat.
- Jadwal terlewat dijalankan sekali sesuai misfire policy.
- Job duplikat dengan idempotency key yang sama tidak terbuat dua kali.

**Ketahanan:**
- Restart worker saat job read-only menghasilkan retry.
- Restart worker saat job tulis menghasilkan `needs_manual_action` atau pemeriksaan state, bukan eksekusi ganda.
- Lock website berakhir otomatis setelah worker mati.
- Gemini timeout ditangani dengan retry dan pesan yang tepat.

---

## 18. Rencana Implementasi (Roadmap)

### Fase 0: Persiapan (1 minggu)
- Repositori, CI, linting, pengaturan environment.
- Rancangan skema database dan migrasi awal.
- Pemilihan dan verifikasi 3 website uji milik sendiri (satu di antaranya staging).

### Fase 1: Fondasi (2–3 minggu)
- Autentikasi dashboard, RBAC, 2FA.
- Modul website dan kredensial terenkripsi.
- Antrean job, worker dasar, audit log.
- Satu adapter pertama dengan login dan ekstraksi read-only.

### Fase 2: Telegram dan AI (2 minggu)
- Bot dengan perintah dasar dan allowlist.
- Integrasi Gemini: daftar model, konfigurasi, validasi JSON, mode structured.
- Mode hybrid dengan validasi kebijakan.

### Fase 3: Persetujuan dan tulis (2–3 minggu)
- Pratinjau, approval, hash, token, expiry.
- Adapter kedua dengan pengisian form.
- Pengujian keamanan untuk alur persetujuan.

### Fase 4: Jadwal, artefak, dan skala (2 minggu)
- Scheduler dengan misfire dan idempotency.
- Screenshot dengan redaksi dan penyimpanan objek.
- Adapter ketiga sampai kelima.
- Pengujian beban dan penyesuaian paket Render.

### Fase 5: Stabilisasi (1–2 minggu)
- Observability lengkap, alert, dokumentasi operasional.
- Uji pemulihan backup.
- Adapter sampai 10 website.

### Roadmap Pascarilis (di luar v1.0)
- Pratinjau terjadwal dengan persetujuan asinkron.
- Multi-akun per website.
- Notifikasi via email.
- Aplikasi mobile.
- Penyimpanan sesi browser terenkripsi dengan masa berlaku.

---

## 19. Risiko dan Mitigasi

| Risiko | Dampak | Kemungkinan | Mitigasi |
|---|---|---|---|
| Website mengubah struktur halaman | Adapter rusak | Tinggi | Selector terkonfigurasi, test fixture, alert |
| Website memblokir atau membatasi akses otomatis | Job gagal | Sedang | Rate limit, backoff, pemberitahuan |
| Pelanggaran ketentuan layanan website | Akun terkena sanksi | Sedang | Tinjauan ketentuan per website, batasi frekuensi, hanya akun sendiri |
| Akun terkunci akibat login berulang | Akses terganggu | Sedang | Batas login, circuit breaker |
| Kebocoran kredensial | Sangat tinggi | Rendah | Enkripsi, filter log, akses terbatas, rotasi |
| Tindakan tulis salah akibat salah interpretasi AI | Data rusak | Sedang | Persetujuan wajib, pratinjau, verifikasi |
| Prompt injection dari konten website | Tindakan tidak diinginkan | Sedang | Validasi di mesin, pemisahan data |
| Biaya browser dan RAM lebih besar dari perkiraan | Anggaran | Sedang | Uji beban, batas konkurensi |
| Ketergantungan pada Gemini API dan perubahan model | Fitur AI terganggu | Sedang | Daftar model dinamis, fallback, mode perintah terstruktur |
| Render spin-down atau restart | Jadwal terlewat | Sedang | Misfire policy, paket yang tepat, health check |
| Telegram account atau bot token bocor | Akses tidak sah | Rendah | Allowlist, webhook secret, rotasi token |

---

## 20. Kriteria Penerimaan (Terperinci)

Sistem dianggap siap untuk penggunaan awal apabila seluruh butir berikut terpenuhi:

**Website dan akun**
- [ ] 3–10 website dapat didaftarkan dengan konfigurasi terpisah.
- [ ] Satu akun aktif per website dapat diuji login dan hasilnya tercatat.
- [ ] Website yang dinonaktifkan tidak menerima job baru.

**Data, form, dan artefak**
- [ ] Ekstraksi data menghasilkan JSON sesuai skema adapter.
- [ ] Screenshot tersimpan dengan metadata dan checksum, serta redaksi berjalan untuk selector sensitif.
- [ ] Pengisian form menghasilkan pratinjau, menunggu persetujuan, lalu memverifikasi hasil setelah simpan.

**Jadwal**
- [ ] Jadwal berjalan sesuai zona waktu.
- [ ] Jadwal tetap berjalan setelah restart layanan dengan misfire policy yang benar.
- [ ] Job duplikat tidak terbuat untuk satu slot jadwal.

**AI**
- [ ] Daftar model Gemini diambil secara dinamis dari API.
- [ ] Model yang tidak mendukung JSON terstruktur tidak dapat dipilih.
- [ ] Keluaran AI yang tidak sesuai skema atau whitelist ditolak dan dicatat.

**Keamanan**
- [ ] Kunci AI dan kredensial website tidak muncul di log atau respons API.
- [ ] Semua tindakan tulis memerlukan persetujuan yang valid dan terikat hash payload.
- [ ] Persetujuan kedaluwarsa atau sudah dipakai tidak dapat digunakan.
- [ ] Perintah Telegram hanya dapat digunakan oleh pengguna yang diizinkan dengan role yang sesuai.
- [ ] Webhook Telegram memvalidasi secret token.
- [ ] Navigasi di luar allowlist diblokir.
- [ ] CAPTCHA, MFA, dan verifikasi tambahan menghentikan job dan tidak dilewati.

**Keandalan**
- [ ] Login gagal, timeout, sesi kedaluwarsa, dan job duplikat ditangani sesuai kebijakan.
- [ ] Restart worker tidak menyebabkan eksekusi tulis ganda.
- [ ] Lock per website berakhir otomatis bila worker mati.

**Audit dan observabilitas**
- [ ] Audit log mencatat semua peristiwa wajib tanpa data sensitif.
- [ ] Audit log bersifat append-only.
- [ ] Metrik dan alert dasar berfungsi.

---

## 21. Pertimbangan Hukum dan Etika

Sistem ini mengotomasi akses ke website. Sebelum digunakan, pengguna perlu memastikan:

- Akun yang didaftarkan adalah milik sendiri atau berada dalam wewenang yang sah.
- Otomasi tidak melanggar ketentuan layanan masing-masing website. Beberapa website melarang otomasi browser secara eksplisit.
- Data yang diambil dipakai sesuai hukum perlindungan data yang berlaku, termasuk UU Pelindungan Data Pribadi (UU No. 27 Tahun 2022) jika data pribadi terlibat.
- Tidak ada upaya menghindari pembatasan teknis, CAPTCHA, atau mekanisme keamanan lain.

Dokumen ini bukan nasihat hukum. Untuk penggunaan di organisasi atau dengan data pribadi dalam jumlah besar, konsultasikan dengan penasihat hukum.

---

## 22. Glosarium

| Istilah | Arti |
|---|---|
| Adapter | Modul khusus satu website yang berisi aturan login, ekstraksi, dan form |
| Approval | Persetujuan eksplisit untuk tindakan tulis |
| Artefak | Hasil yang disimpan, misalnya screenshot atau JSON |
| Idempotency key | Kunci untuk mencegah job duplikat |
| Misfire | Kondisi ketika jadwal seharusnya berjalan tetapi terlewat |
| Preview hash | Sidik jari payload yang disetujui |
| Risk level | Klasifikasi tindakan: read, write_low, write_high |
| SSRF | Server-side request forgery, upaya membuat server membuka URL berbahaya |

---

## Lampiran A: Contoh Alur Lengkap Perubahan Data

1. Pengguna di Telegram: `/ask Isi formulir pembaruan di Site A dengan data dari draft 123.`
2. Backend menerima pesan, memeriksa Telegram user ID dan role `operator`.
3. AI Gateway mengirim daftar website (`site_01`: Site A, `allowed_actions`: `extract_data`, `take_screenshot`, `fill_form`, `submit_form`) dan instruksi pengguna.
4. Gemini mengembalikan:
   ```json
   {
     "intent": "execute_job",
     "website_id": "site_01",
     "action": "fill_form",
     "risk_level": "write_low",
     "requires_approval": true,
     "parameters": { "template": "update_profile", "draft_id": "123" },
     "clarification_question": null,
     "user_summary": "Mengisi formulir pembaruan profil di Site A dari draft 123."
   }
   ```
5. Backend memvalidasi: website aktif, action termasuk `allowed_actions`, `draft_id` ada, parameter sesuai skema. Risiko dihitung ulang sebagai `write_low`.
6. Job dibuat dengan status `queued`.
7. Worker membuka sesi, login, mengambil data draft, mengisi form tanpa menyimpan, lalu mengambil ringkasan perubahan.
8. Pratinjau disimpan, `approval_hash` dihitung, token dibuat, dan job berstatus `awaiting_approval`.
9. Bot mengirim pratinjau ke semua operator aktif dengan tombol Setujui dan Tolak.
10. Operator menekan Setujui. Backend menghitung ulang hash dan memastikan belum kedaluwarsa.
11. Job berstatus `executing`. Worker login ulang, memeriksa nilai lama terhadap pratinjau, menyimpan form, dan membaca ulang data.
12. Jika cocok, job `succeeded`, screenshot konfirmasi disimpan, dan bot mengirim hasil ringkas dengan tautan artefak.
13. Seluruh langkah tercatat di audit log dengan `request_id` yang sama.

---

**Akhir dokumen.**
