"use client";

import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  CalendarClock,
  Command,
  Bot,
  FileClock,
  Globe2,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Workflow,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

type Role = "owner" | "operator" | "viewer";
type User = { id: string; email: string; role: Role };
type BootstrapStatus = { initialized: boolean; bootstrap_configured: boolean; needs_bootstrap: boolean };
type Website = {
  id: string;
  name: string;
  base_url: string;
  adapter_id: string;
  status: "active" | "disabled" | "needs_attention";
  allowed_actions: string[];
};
type Job = {
  id: string;
  website_id: string;
  action: string;
  risk_level: string;
  status: string;
  created_at: string;
  last_error_code?: string | null;
  last_error_message?: string | null;
};
type Approval = {
  id: string;
  job_id: string;
  website_name: string;
  action: string;
  preview_summary: Record<string, unknown>;
  approval_hash: string;
  expires_at: string;
};
type Section = "overview" | "websites" | "jobs" | "approvals" | "schedules" | "ai" | "audit" | "telegram";
type Schedule = { id: string; name: string; website_id: string; action: string; cron_expr: string; timezone: string; is_active: boolean; next_run_at: string };
type AISettings = { configured: boolean; api_key_masked: string | null; primary_model_id: string | null; fallback_model_id: string | null; temperature: number; max_output_tokens: number; timeout_seconds: number; mode: string; allow_ai_write_proposals: boolean; system_instruction: string; status: string };
type AIModel = { id: string; display_name: string; input_token_limit: number | null; output_token_limit: number | null };
type TelegramUser = { telegram_user_id: number; display_name: string; role: Role; is_active: boolean };

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

const navItems: { id: Section; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Ringkasan", icon: LayoutDashboard },
  { id: "websites", label: "Website", icon: Globe2 },
  { id: "jobs", label: "Riwayat job", icon: ListChecks },
  { id: "approvals", label: "Persetujuan", icon: ShieldCheck },
  { id: "schedules", label: "Jadwal", icon: CalendarClock },
  { id: "ai", label: "Pengaturan AI", icon: Bot },
  { id: "audit", label: "Audit log", icon: FileClock },
  { id: "telegram", label: "Pengguna Telegram", icon: Command },
];

const actionLabels: Record<string, string> = {
  extract_data: "Ekstraksi data",
  take_screenshot: "Screenshot",
  test_login: "Uji login",
  fill_form: "Isi formulir",
  submit_form: "Kirim formulir",
};

const statusLabels: Record<string, string> = {
  queued: "Dalam antrean",
  running: "Berjalan",
  awaiting_approval: "Menunggu persetujuan",
  executing: "Menjalankan perubahan",
  succeeded: "Selesai",
  failed: "Gagal",
  cancelled: "Dibatalkan",
  rejected: "Ditolak",
  expired: "Kedaluwarsa",
  needs_manual_action: "Perlu tindakan manual",
  active: "Aktif",
  disabled: "Nonaktif",
  needs_attention: "Perlu perhatian",
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (init?.method && init.method !== "GET") {
    const csrf = window.sessionStorage.getItem("pbot-csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "include" });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(payload.detail?.message ?? "Permintaan tidak dapat diproses.", response.status);
  return payload as T;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${value}`}>{statusLabels[value] ?? value.replaceAll("_", " ")}</span>;
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
    const [bootstrapStatus, setBootstrapStatus] = useState<BootstrapStatus | null>(null);
  const [section, setSection] = useState<Section>("overview");
  const [websites, setWebsites] = useState<Website[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [aiSettings, setAISettings] = useState<AISettings | null>(null);
  const [aiModels, setAIModels] = useState<AIModel[]>([]);
  const [telegramUsers, setTelegramUsers] = useState<TelegramUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<Record<string, unknown>[]>([]);
  const [aiDiscoverBusy, setAIDiscoverBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [totpRequired, setTotpRequired] = useState(false);
  const [search, setSearch] = useState("");
  const [websiteFormOpen, setWebsiteFormOpen] = useState(false);
  const [jobFormOpen, setJobFormOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const currentUser = await api<User>("/auth/me");
      const [siteData, jobData, approvalData] = await Promise.all([
        api<Website[]>("/websites"),
        api<Job[]>("/jobs?limit=30"),
        currentUser.role === "viewer" ? Promise.resolve([]) : api<Approval[]>("/approvals"),
      ]);
      setUser(currentUser);
      setWebsites(siteData);
      setJobs(jobData);
      setApprovals(approvalData);
      setSchedules(await api<Schedule[]>("/schedules"));
      setNotice("");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        const setup = await api<BootstrapStatus>("/auth/bootstrap/status").catch(() => null);
        setBootstrapStatus(setup);
        setUser(null);
        if (error.message !== "Silakan masuk terlebih dahulu." && !error.message.includes("Sesi")) {
          setNotice(error.message);
        }
      } else {
        setNotice(error instanceof Error ? error.message : "Data dashboard sementara tidak tersedia.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!user || user.role !== "owner") return;
    if (section === "ai") {
      void api<AISettings>("/ai/config").then(setAISettings).catch((error) => setNotice(error.message));
    }
    if (section === "telegram") {
      void api<TelegramUser[]>("/telegram/users").then(setTelegramUsers).catch((error) => setNotice(error.message));
    }
    if (section === "audit") {
      void api<Record<string, unknown>[]>("/audit-logs").then(setAuditLogs).catch((error) => setNotice(error.message));
    }
    if (section === "ai" && aiSettings?.configured) {
      void api<{ models: AIModel[] }>("/ai/models").then((result) => setAIModels(result.models)).catch((error) => setNotice(error.message));
    }
  }, [section, user, aiSettings?.configured]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function onLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ user?: User; csrf_token?: string; requires_totp?: boolean }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (result.requires_totp) {
        setTotpRequired(true);
        setNotice("");
        return;
      }
      if (!result.user || !result.csrf_token) throw new Error("Respons login tidak lengkap.");
      window.sessionStorage.setItem("pbot-csrf", result.csrf_token);
      setBootstrapStatus({ initialized: true, bootstrap_configured: false, needs_bootstrap: false });
      setUser(result.user);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Login gagal.");
    } finally {
      setLoginBusy(false);
    }
  }

  async function onVerifyTotp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ user: User; csrf_token: string }>("/auth/totp/verify", { method: "POST", body: JSON.stringify({ code: form.get("code") }) });
      window.sessionStorage.setItem("pbot-csrf", result.csrf_token);
      setTotpRequired(false);
      setUser(result.user);
      await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Kode autentikasi tidak valid."); }
    finally { setLoginBusy(false); }
  }

  async function decide(approval: Approval, decision: "approve" | "reject") {
    try {
      await api(`/approvals/${approval.id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ approval_hash: approval.approval_hash }),
      });
      setNotice(decision === "approve" ? "Perubahan disetujui dan masuk antrean eksekusi." : "Pratinjau ditolak.");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Keputusan tidak dapat diproses.");
    }
  }

  async function onLogout() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      window.sessionStorage.removeItem("pbot-csrf");
      setUser(null);
    }
  }

  async function submitWebsite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const username = String(form.get("username") || "");
      const password = String(form.get("password") || "");
      if (Boolean(username) !== Boolean(password)) throw new Error("Username dan password harus diisi bersama atau keduanya dikosongkan.");
      const adapterConfig = JSON.parse(String(form.get("adapter_config") || "{}"));
      const created = await api<Website>("/websites", { method: "POST", body: JSON.stringify({
        id: form.get("id"), name: form.get("name"), base_url: form.get("base_url"),
        login_url: form.get("login_url"), dashboard_url: form.get("dashboard_url"),
        adapter_id: form.get("adapter_id"), adapter_version: "1.0.0", adapter_config: adapterConfig,
        allowed_actions: form.getAll("actions"),
      }) });
      if (username && password) await api(`/websites/${created.id}/credentials`, { method: "PUT", body: JSON.stringify({ username, password }) });
      setNotice("Website berhasil didaftarkan.");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Website tidak dapat disimpan.");
    }
  }

  async function runLoginTest(site: Website) {
    try {
      await api(`/websites/${site.id}/test-login`, { method: "POST", body: "{}" });
      setNotice(`Job uji login untuk ${site.name} masuk antrean.`);
      await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Job tidak dapat dibuat."); }
  }

  async function submitJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const payload = JSON.parse(String(form.get("payload") || "{}"));
      await api("/jobs", {
        method: "POST",
        body: JSON.stringify({
          website_id: form.get("website_id"),
          action: form.get("action"),
          payload,
          idempotency_key: crypto.randomUUID(),
        }),
      });
      setNotice("Job masuk antrean worker.");
      setJobFormOpen(false);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Job tidak dapat dibuat.");
    }
  }

  async function cancelJob(job: Job) {
    try {
      await api(`/jobs/${job.id}/cancel`, { method: "POST", body: "{}" });
      setNotice(`Permintaan pembatalan job ${job.id.slice(0, 8)} dicatat.`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Job tidak dapat dibatalkan.");
    }
  }

  async function submitSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api("/schedules", { method: "POST", body: JSON.stringify({ name: form.get("name"), website_id: form.get("website_id"), action: form.get("action"), cron_expr: form.get("cron_expr"), timezone: form.get("timezone"), payload: JSON.parse(String(form.get("payload") || "{}")), is_active: true, misfire_policy: "run_once_latest" }) });
      setNotice("Jadwal berhasil dibuat.");
      setSchedules(await api<Schedule[]>("/schedules"));
      formElement.reset();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Jadwal tidak dapat dibuat."); }
  }

  async function submitAISettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const rawKey = String(form.get("api_key") || "");
    try {
      const saved = await api<AISettings>("/ai/config", { method: "PUT", body: JSON.stringify({
        ...(rawKey ? { api_key: rawKey } : {}), primary_model_id: form.get("primary_model_id"),
        fallback_model_id: form.get("fallback_model_id") || null, temperature: Number(form.get("temperature")),
        max_output_tokens: Number(form.get("max_output_tokens")), timeout_seconds: Number(form.get("timeout_seconds")),
        mode: form.get("mode"), allow_ai_write_proposals: form.get("allow_ai_write_proposals") === "on",
        system_instruction: form.get("system_instruction"),
      }) });
      setAISettings(saved);
      setAIModels((await api<{ models: AIModel[] }>("/ai/models")).models);
      setNotice("Pengaturan Gemini tersimpan. Key hanya tampil sebagai metadata tersamarkan.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Pengaturan AI gagal disimpan."); }
  }

  async function testAI() {
    try {
      await api("/ai/test", { method: "POST", body: "{}" });
      setNotice("Koneksi Gemini dan structured output berhasil.");
      setAISettings((value) => value ? { ...value, status: "valid" } : value);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Uji koneksi gagal."); }
  }

  async function discoverAIModels() {
    const form = document.forms.namedItem("ai-settings");
    const keyField = form?.elements.namedItem("api_key");
    const apiKey = keyField instanceof HTMLInputElement ? keyField.value : "";
    if (!apiKey) {
      setNotice("Masukkan API key Gemini untuk memuat daftar model.");
      return;
    }
    setAIDiscoverBusy(true);
    try {
      const result = await api<{ models: AIModel[] }>("/ai/models/discover", { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
      setAIModels(result.models);
      setNotice(`${result.models.length} model kompatibel ditemukan.`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Daftar model tidak dapat dimuat."); }
    finally { setAIDiscoverBusy(false); }
  }

  async function submitTelegramUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api("/telegram/users", { method: "POST", body: JSON.stringify({ telegram_user_id: Number(form.get("telegram_user_id")), display_name: form.get("display_name"), role: form.get("role") }) });
      setTelegramUsers(await api<TelegramUser[]>("/telegram/users"));
      formElement.reset();
      setNotice("Pengguna Telegram ditambahkan ke allowlist.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Pengguna Telegram tidak dapat ditambahkan."); }
  }

  async function toggleTelegramUser(entry: TelegramUser) {
    try {
      await api(`/telegram/users/${entry.telegram_user_id}`, { method: "PATCH", body: JSON.stringify({ is_active: !entry.is_active }) });
      setTelegramUsers(await api<TelegramUser[]>("/telegram/users"));
    } catch (error) { setNotice(error instanceof Error ? error.message : "Status pengguna gagal diubah."); }
  }

  async function toggleWebsite(site: Website) {
    try {
      await api(`/websites/${site.id}/status`, { method: "PATCH", body: JSON.stringify({ status: site.status === "active" ? "disabled" : "active" }) });
      await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Status website gagal diubah."); }
  }

  const activeSites = websites.filter((site) => site.status === "active").length;
  const runningJobs = jobs.filter((job) => ["queued", "running", "executing"].includes(job.status)).length;
  const succeededJobs = jobs.filter((job) => job.status === "succeeded").length;
  const filteredJobs = jobs.filter((job) => `${job.action} ${job.website_id} ${job.status}`.toLowerCase().includes(search.toLowerCase()));

  if (loading) return <main className="loading-screen"><span className="loader" />Memuat konsol</main>;

  if (!user) {
    return (
      <main className="login-screen">
        <section className="login-panel">
          <div className="brand-mark"><Workflow size={19} strokeWidth={2.2} /><span>PBOT</span></div>
          <p className="eyebrow">KONSOL OTOMASI</p>
          <h1>Kontrol tetap<br /><em>di tangan Anda.</em></h1>
          <p className="login-copy">Satu ruang kerja untuk memantau website, pekerjaan otomatis, dan setiap perubahan yang menunggu persetujuan.</p>
          {bootstrapStatus?.needs_bootstrap && <div className="bootstrap-note"><CircleAlert size={16} /><span>Owner awal belum tersedia. Isi <code>BOOTSTRAP_OWNER_EMAIL</code> dan <code>BOOTSTRAP_OWNER_PASSWORD</code> di environment lalu restart API. Tidak ada password default.</span></div>}
          {totpRequired ? <form onSubmit={onVerifyTotp} className="login-form"><label>Kode autentikasi<input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="000000" required /></label>{notice && <p className="form-error">{notice}</p>}<button className="button button-primary button-wide" disabled={loginBusy}>{loginBusy ? "Memverifikasi..." : "Verifikasi dan masuk"}<ArrowRight size={16} /></button></form> : <form onSubmit={onLogin} className="login-form"><label>Email<input name="email" type="email" autoComplete="username" placeholder="nama@perusahaan.id" required /></label><label>Password<input name="password" type="password" autoComplete="current-password" placeholder="Masukkan password" required /></label>{notice && <p className="form-error">{notice}</p>}<button className="button button-primary button-wide" disabled={loginBusy}>{loginBusy ? "Memverifikasi..." : "Masuk ke konsol"}<ArrowRight size={16} /></button></form>}
          <div className="login-foot"><ShieldCheck size={15} /> Kredensial website dienkripsi, tidak ditampilkan ulang.</div>
        </section>
        <aside className="login-aside">
          <div className="aside-grid" />
          <div className="aside-stamp"><Activity size={17} /> HUMAN-IN-THE-LOOP</div>
          <div className="aside-copy"><span>01 / SAFEGUARD</span><p>AI menyusun usulan.<br />Kebijakan server yang memutuskan.</p><div className="aside-rule" /><small>Setiap perubahan menunggu persetujuan Anda.</small></div>
          <div className="aside-version">PBOT PLATFORM <span>v1.0 / ID</span></div>
        </aside>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview"><span className="brand-icon"><Workflow size={18} /></span><span>pbot<span className="brand-period">.</span></span></a>
        <div className="workspace-switch"><span className="workspace-avatar">P</span><span><b>Workspace utama</b><small>Personal workspace</small></span><ChevronDown size={15} /></div>
        <p className="nav-caption">RUANG KERJA</p>
        <nav className="main-nav" aria-label="Navigasi utama">
          {navItems.filter((item) => item.id === "overview" || item.id === "websites" || item.id === "jobs" || item.id === "approvals" || user.role === "owner").map(({ id, label, icon: Icon }) => (
            <button key={id} onClick={() => setSection(id)} className={`nav-link ${section === id ? "selected" : ""}`}>
              <Icon size={17} strokeWidth={1.8} /><span>{label}</span>{id === "approvals" && approvals.length > 0 && <span className="nav-count">{approvals.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="system-status"><span className="pulse-dot" /><span><b>Sistem beroperasi</b><small>API tersambung</small></span><ArrowUpRight size={14} /></div>
          <div className="profile-row"><div className="profile-avatar">{user.email.slice(0, 1).toUpperCase()}</div><span className="profile-data"><b>{user.email}</b><small>{user.role}</small></span><button className="icon-button" title="Keluar" aria-label="Keluar" onClick={onLogout}><LogOut size={16} /></button></div>
        </div>
      </aside>

      <section className="main-column">
        <header className="topbar"><div className="breadcrumbs"><span>PBOT</span><span className="crumb-divider">/</span><b>{navItems.find((item) => item.id === section)?.label}</b></div><div className="topbar-actions"><span className="live-indicator"><span className="pulse-dot" /> LIVE</span><button className="icon-button" title="Segarkan data" aria-label="Segarkan data" onClick={() => void refresh()}><RefreshCw size={16} /></button><div className="topbar-date">{new Intl.DateTimeFormat("id-ID", { weekday: "short", day: "numeric", month: "short" }).format(new Date())}</div></div></header>

        <div className="content-area">
          {notice && <div className="notice"><CircleAlert size={16} /><span>{notice}</span><button className="icon-button" title="Tutup" aria-label="Tutup pesan" onClick={() => setNotice("")}><X size={15} /></button></div>}

          {section === "overview" && <>
            <div className="page-heading"><div><p className="eyebrow">KAMIS, 29 OKTOBER 2026 <span className="heading-dot" /> WORKSPACE OVERVIEW</p><h1>Selamat datang kembali.</h1><p className="page-subtitle">Pantau aktivitas otomasi dan tindak lanjuti yang membutuhkan perhatian.</p></div><button className="button button-dark" onClick={() => setSection("websites")}><Plus size={16} /> Tambah website</button></div>
            <div className="metric-grid">
              <Metric label="Website aktif" value={activeSites.toString().padStart(2, "0")} note={`${websites.length} terdaftar`} icon={<Globe2 size={17} />} trend="stabil" />
              <Metric label="Job berjalan" value={runningJobs.toString().padStart(2, "0")} note="di semua website" icon={<Activity size={17} />} trend="live" />
              <Metric label="Menunggu approval" value={approvals.length.toString().padStart(2, "0")} note="butuh keputusan Anda" icon={<ShieldCheck size={17} />} attention={approvals.length > 0} />
              <Metric label="Job berhasil" value={succeededJobs.toString().padStart(2, "0")} note="30 aktivitas terakhir" icon={<Check size={17} />} trend="terkini" />
            </div>
            <div className="overview-grid">
              <section className="surface activity-surface"><div className="section-header"><div><p className="eyebrow">OPERASIONAL</p><h2>Aktivitas terbaru</h2></div><button className="text-button" onClick={() => setSection("jobs")}>Semua job <ArrowRight size={14} /></button></div>
                {jobs.length === 0 ? <EmptyState title="Belum ada aktivitas" detail="Job yang dibuat akan muncul di sini." /> : <div className="activity-list">{jobs.slice(0, 6).map((job) => <JobRow key={job.id} job={job} />)}</div>}
              </section>
              <div className="overview-right"><section className="surface approval-surface"><div className="section-header"><div><p className="eyebrow">TINDAKAN ANDA</p><h2>Persetujuan</h2></div><span className="approval-count">{approvals.length.toString().padStart(2, "0")}</span></div>
                  {approvals.length === 0 ? <div className="approval-empty"><span className="empty-check"><Check size={19} /></span><div><b>Semua terkendali</b><small>Tidak ada perubahan menunggu persetujuan.</small></div></div> : approvals.slice(0, 2).map((approval) => <ApprovalCard key={approval.id} approval={approval} onDecide={decide} compact />)}
                  {approvals.length > 0 && <button className="text-button approval-more" onClick={() => setSection("approvals")}>Lihat semua <ArrowRight size={14} /></button>}
                </section>
                <section className="surface systems-surface"><div className="section-header"><div><p className="eyebrow">KETERSEDIAAN</p><h2>Status sistem</h2></div><span className="system-ok"><span className="pulse-dot" />NORMAL</span></div><div className="service-row"><span className="service-mark api-mark">A</span><span><b>API & Database</b><small>Respons normal</small></span><span className="service-state">Online</span></div><div className="service-row"><span className="service-mark worker-mark">W</span><span><b>Automation worker</b><small>Antrean siap</small></span><span className="service-state">Online</span></div><div className="system-foot"><Clock3 size={13} /> Diperbarui otomatis setiap 5 detik</div></section>
              </div>
            </div>
            <section className="surface website-strip"><div className="section-header"><div><p className="eyebrow">KONEKSI TERDAFTAR</p><h2>Website Anda</h2></div><button className="text-button" onClick={() => setSection("websites")}>Kelola website <ArrowRight size={14} /></button></div>{websites.length === 0 ? <EmptyState title="Hubungkan website pertama" detail="Tambahkan website milik Anda untuk memulai." /> : <div className="website-grid">{websites.slice(0, 4).map((site) => <WebsiteTile key={site.id} site={site} />)}</div>}</section>
          </>}

          {section === "websites" && <>
            <div className="page-heading"><div><p className="eyebrow">KONFIGURASI</p><h1>Website</h1><p className="page-subtitle">Adapter dan status koneksi untuk setiap website terdaftar.</p></div>{user.role === "owner" && <button className="button button-dark" onClick={() => setWebsiteFormOpen((value) => !value)}><Plus size={16} /> Tambah website</button>}</div>
            {websiteFormOpen && <form className="surface config-form" onSubmit={submitWebsite}>
              <div className="form-title"><div><p className="eyebrow">KONFIGURASI BARU</p><h2>Daftarkan website</h2></div><button type="button" className="icon-button" aria-label="Tutup" onClick={() => setWebsiteFormOpen(false)}><X size={16} /></button></div>
              <div className="form-grid"><label>ID unik<input name="id" placeholder="site_01" pattern="[A-Za-z0-9_-]{1,32}" required /></label><label>Nama<input name="name" placeholder="Portal utama" required /></label><label>Base URL<input name="base_url" type="url" placeholder="https://portal.milik-anda.id" required /></label><label>URL login<input name="login_url" type="url" placeholder="https://portal.milik-anda.id/login" required /></label><label>URL dashboard<input name="dashboard_url" type="url" placeholder="https://portal.milik-anda.id/dashboard" required /></label><label>Adapter ID<input name="adapter_id" defaultValue="configured_selectors" required /></label><label>Username akun<input name="username" autoComplete="off" /></label><label>Password akun<input name="password" type="password" autoComplete="new-password" /></label></div>
              <label className="form-block">Selector & adapter config (JSON)<textarea name="adapter_config" rows={8} defaultValue={JSON.stringify({ login: { username_selector: "input[name='email']", password_selector: "input[name='password']", submit_selector: "button[type='submit']", success_indicator: "[data-testid='user-menu']", failure_indicator: ".alert-error", manual_action_indicators: [] }, extract: { fields: {} }, pages: {}, sensitive_selectors: ["input[type='password']"], forms: {} }, null, 2)} /></label>
              <fieldset className="action-fieldset"><legend>Aksi yang diizinkan</legend>{Object.entries(actionLabels).map(([action, label]) => <label key={action} className="checkbox-row"><input type="checkbox" name="actions" value={action} defaultChecked={action === "extract_data" || action === "take_screenshot" || action === "test_login"} />{label}{action.startsWith("fill") || action.startsWith("submit") ? <span className="inline-warning">memerlukan persetujuan</span> : null}</label>)}</fieldset>
              <div className="form-actions"><button type="button" className="button button-quiet" onClick={() => setWebsiteFormOpen(false)}>Batal</button><button className="button button-dark" type="submit">Simpan website</button></div>
            </form>}
            <section className="surface full-table"><div className="table-toolbar"><div className="search-box"><Search size={16} /><input placeholder="Cari website..." value={search} onChange={(event) => setSearch(event.target.value)} /></div><button className="icon-button" title="Filter"><SlidersHorizontal size={16} /></button></div>{websites.length === 0 ? <EmptyState title="Belum ada website" detail="Website yang Anda daftarkan akan tercatat di sini." /> : <div className="table-wrap"><table><thead><tr><th>NAMA WEBSITE</th><th>ADAPTER</th><th>AKSI DIIZINKAN</th><th>STATUS</th><th>UJI / STATUS</th></tr></thead><tbody>{websites.filter((site) => `${site.name} ${site.id}`.toLowerCase().includes(search.toLowerCase())).map((site) => <tr key={site.id}><td><div className="site-cell"><span className="site-avatar">{site.name.slice(0, 1).toUpperCase()}</span><span><b>{site.name}</b><small>{site.base_url}</small></span></div></td><td><code>{site.adapter_id}</code></td><td>{site.allowed_actions.map((action) => <span key={action} className="action-chip">{actionLabels[action] ?? action}</span>)}</td><td><Badge value={site.status} /></td><td className="table-actions">{user.role !== "viewer" && <button className="button button-quiet button-small" onClick={() => void runLoginTest(site)}>Uji login</button>}{user.role === "owner" && <button className="button button-quiet button-small" onClick={() => void toggleWebsite(site)}>{site.status === "active" ? "Nonaktifkan" : "Aktifkan"}</button>}</td></tr>)}</tbody></table></div>}</section>
          </>}

          {section === "jobs" && <>
            <div className="page-heading"><div><p className="eyebrow">JEJAK EKSEKUSI</p><h1>Riwayat job</h1><p className="page-subtitle">Setiap permintaan, hasil, dan kegagalan tercatat.</p></div>{user.role !== "viewer" ? <button className="button button-dark" onClick={() => setJobFormOpen((value) => !value)}><Plus size={16} /> Jalankan aksi baca</button> : <span className="read-only-label"><ShieldCheck size={15} /> Tercatat untuk audit</span>}</div>
            {jobFormOpen && <form className="surface config-form" onSubmit={submitJob}><div className="form-title"><div><p className="eyebrow">JOB MANUAL</p><h2>Aksi baca</h2></div><button type="button" className="icon-button" aria-label="Tutup" onClick={() => setJobFormOpen(false)}><X size={16} /></button></div><div className="form-grid"><label>Website<select name="website_id" required>{websites.filter((site) => site.status === "active").map((site) => <option key={site.id} value={site.id}>{site.name} · {site.id}</option>)}</select></label><label>Aksi<select name="action"><option value="extract_data">Ekstraksi data</option><option value="take_screenshot">Screenshot</option><option value="test_login">Uji login</option></select></label></div><label className="form-block">Parameter JSON<textarea name="payload" rows={3} defaultValue="{}" /></label><div className="form-actions"><button type="button" className="button button-quiet" onClick={() => setJobFormOpen(false)}>Batal</button><button className="button button-dark" type="submit" disabled={!websites.some((site) => site.status === "active")}>Masukkan antrean</button></div></form>}
            <section className="surface full-table"><div className="table-toolbar"><div className="search-box"><Search size={16} /><input placeholder="Cari aksi atau website..." value={search} onChange={(event) => setSearch(event.target.value)} /></div><span className="result-count">{filteredJobs.length} aktivitas</span></div>{filteredJobs.length === 0 ? <EmptyState title="Tidak ada job yang cocok" detail="Ubah pencarian atau jalankan aksi baca." /> : <div className="table-wrap"><table><thead><tr><th>AKSI</th><th>WEBSITE</th><th>RISIKO</th><th>STATUS</th><th>WAKTU</th><th></th></tr></thead><tbody>{filteredJobs.map((job) => <tr key={job.id}><td><b>{actionLabels[job.action] ?? job.action}</b><small className="job-id">{job.id}</small></td><td><code>{job.website_id}</code></td><td><span className={`risk-label risk-${job.risk_level}`}>{job.risk_level === "read" ? <ArrowDownRight size={13} /> : <ArrowUpRight size={13} />}{job.risk_level}</span></td><td><Badge value={job.status} /></td><td>{formatTime(job.created_at)}</td><td className="table-actions">{job.last_error_code && <span title={job.last_error_message ?? ""}><CircleAlert size={15} className="error-icon" /></span>}{user.role !== "viewer" && ["queued", "running", "executing", "awaiting_approval"].includes(job.status) && <button className="button button-quiet button-small" onClick={() => void cancelJob(job)}>Batalkan</button>}</td></tr>)}</tbody></table></div>}</section>
          </>}

          {section === "approvals" && <><div className="page-heading"><div><p className="eyebrow">HUMAN-IN-THE-LOOP</p><h1>Persetujuan</h1><p className="page-subtitle">Tinjau perubahan sebelum worker mengeksekusinya.</p></div><div className="pending-pill"><span className="pending-dot" />{approvals.length} menunggu</div></div>{approvals.length === 0 ? <section className="surface approval-blank"><span className="empty-check"><Check size={22} /></span><h2>Tidak ada perubahan tertunda</h2><p>Pratinjau perubahan akan muncul di sini sebelum dieksekusi.</p></section> : <div className="approval-page-list">{approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} onDecide={decide} />)}</div>}</>}

          {section === "schedules" && <>
            <div className="page-heading"><div><p className="eyebrow">OTOMASI BERJADWAL</p><h1>Jadwal</h1><p className="page-subtitle">Eksekusi berulang hanya untuk tindakan baca.</p></div><span className="read-only-label"><ShieldCheck size={15} /> Write diblokir</span></div>
            {user.role === "owner" && <form className="surface config-form" onSubmit={submitSchedule}><div className="form-title"><div><p className="eyebrow">JADWAL BARU</p><h2>Buat jadwal</h2></div><CalendarClock size={17} /></div><div className="form-grid"><label>Nama jadwal<input name="name" placeholder="Ekstraksi harian" required /></label><label>Website<select name="website_id" required>{websites.filter((site) => site.status === "active").map((site) => <option key={site.id} value={site.id}>{site.name} · {site.id}</option>)}</select></label><label>Aksi baca<select name="action"><option value="extract_data">Ekstraksi data</option><option value="take_screenshot">Screenshot</option><option value="test_login">Uji login</option></select></label><label>Ekspresi cron<input name="cron_expr" defaultValue="0 8 * * *" required /></label><label>Zona waktu IANA<input name="timezone" defaultValue="Asia/Jakarta" required /></label><label>Parameter (JSON)<input name="payload" defaultValue="{}" /></label></div><div className="form-actions"><button className="button button-dark" type="submit"><Plus size={15} /> Simpan jadwal</button></div></form>}
            <section className="surface full-table"><div className="table-toolbar"><b className="toolbar-title">Jadwal terdaftar</b><span className="result-count">{schedules.length} jadwal</span></div>{schedules.length === 0 ? <EmptyState title="Belum ada jadwal" detail="Buat jadwal untuk aksi baca yang perlu dijalankan berulang." /> : <div className="table-wrap"><table><thead><tr><th>NAMA</th><th>WEBSITE</th><th>AKSI</th><th>CRON / ZONA WAKTU</th><th>EKSEKUSI BERIKUTNYA</th><th>STATUS</th><th></th></tr></thead><tbody>{schedules.map((schedule) => <tr key={schedule.id}><td><b>{schedule.name}</b></td><td><code>{schedule.website_id}</code></td><td>{actionLabels[schedule.action] ?? schedule.action}</td><td><code>{schedule.cron_expr}</code><small>{schedule.timezone}</small></td><td>{formatTime(schedule.next_run_at)}</td><td><Badge value={schedule.is_active ? "active" : "disabled"} /></td><td>{user.role === "owner" && schedule.is_active && <button className="button button-quiet button-small" onClick={async () => { try { await api(`/schedules/${schedule.id}`, { method: "DELETE" }); setSchedules(await api<Schedule[]>("/schedules")); } catch (error) { setNotice(error instanceof Error ? error.message : "Jadwal gagal dinonaktifkan."); } }}>Nonaktifkan</button>}</td></tr>)}</tbody></table></div>}</section>
          </>}

          {section === "ai" && <><div className="page-heading"><div><p className="eyebrow">GATEWAY MODEL</p><h1>Pengaturan AI</h1><p className="page-subtitle">Gemini mengusulkan tindakan; backend tetap menegakkan kebijakan.</p></div><Badge value={aiSettings?.status ?? "unverified"} /></div>{!aiSettings ? <section className="surface"><EmptyState title="Memuat konfigurasi" detail="Menghubungkan ke AI Gateway." /></section> : <form id="ai-settings" name="ai-settings" className="surface config-form" onSubmit={submitAISettings}><div className="form-title"><div><p className="eyebrow">GOOGLE GEMINI</p><h2>Koneksi dan kebijakan</h2></div><Bot size={18} /></div><label className="form-block">API key{aiSettings.api_key_masked && <small className="field-note">Key tersimpan: {aiSettings.api_key_masked}. Nilai asli tidak dapat dibaca kembali.</small>}<input name="api_key" type="password" autoComplete="new-password" placeholder={aiSettings.configured ? "Kosongkan untuk tetap memakai key tersimpan" : "AIza..."} /></label><div className="form-actions form-actions-left"><button type="button" className="button button-quiet" disabled={aiDiscoverBusy} onClick={() => void discoverAIModels()}><RefreshCw size={14} />{aiDiscoverBusy ? "Memuat model..." : "Muat model dari Gemini"}</button><span className="field-note">Daftar model tidak disimpan permanen.</span></div><div className="form-grid"><label>Model utama<select name="primary_model_id" required defaultValue={aiSettings.primary_model_id ?? ""} disabled={aiModels.length === 0}><option value="" disabled>{aiModels.length ? "Pilih model" : "Muat model terlebih dahulu"}</option>{aiModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label><label>Model cadangan<select name="fallback_model_id" defaultValue={aiSettings.fallback_model_id ?? ""} disabled={aiModels.length === 0}><option value="">Tidak digunakan</option>{aiModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label><label>Temperature<input name="temperature" type="number" min="0" max="1" step="0.05" defaultValue={aiSettings.temperature} /></label><label>Token maksimum<input name="max_output_tokens" type="number" min="128" max="8192" step="128" defaultValue={aiSettings.max_output_tokens} /></label><label>Timeout (detik)<input name="timeout_seconds" type="number" min="5" max="120" defaultValue={aiSettings.timeout_seconds} /></label><label>Mode<select name="mode" defaultValue={aiSettings.mode}><option value="structured_only">Perintah terstruktur</option><option value="hybrid">Hybrid</option></select></label></div><label className="form-block">Instruksi sistem<textarea name="system_instruction" rows={4} maxLength={4000} defaultValue={aiSettings.system_instruction} /></label><label className="toggle-row"><input name="allow_ai_write_proposals" type="checkbox" defaultChecked={aiSettings.allow_ai_write_proposals} /><span><b>Izinkan usulan tindakan tulis dari AI</b><small>Approval tetap wajib untuk semua tindakan tulis.</small></span></label><div className="form-actions"><button type="button" className="button button-quiet" disabled={!aiSettings.configured} onClick={() => void testAI()}><Activity size={14} /> Uji koneksi</button><button className="button button-dark" type="submit" disabled={!aiModels.length}>Simpan pengaturan</button></div></form>}</>}

          {section === "audit" && <><div className="page-heading"><div><p className="eyebrow">APPEND-ONLY</p><h1>Audit log</h1><p className="page-subtitle">Jejak tindakan pengguna, AI, job, dan konfigurasi.</p></div><span className="read-only-label"><ShieldCheck size={15} /> Owner only</span></div><section className="surface full-table"><div className="table-toolbar"><b className="toolbar-title">Peristiwa terbaru</b><span className="result-count">{auditLogs.length} peristiwa</span></div>{auditLogs.length === 0 ? <EmptyState title="Belum ada catatan audit" detail="Aktivitas sistem akan tercatat di sini." /> : <div className="table-wrap"><table><thead><tr><th>WAKTU</th><th>PELAKU</th><th>PERISTIWA</th><th>OBJEK</th><th>HASIL</th></tr></thead><tbody>{auditLogs.map((item, index) => <tr key={String(item.id ?? index)}><td>{formatTime(String(item.occurred_at))}</td><td><code>{String(item.actor_type)} · {String(item.actor_id).slice(0, 12)}</code></td><td><b>{String(item.action)}</b></td><td>{String(item.target_type)} · {String(item.target_id).slice(0, 14)}</td><td><Badge value={String(item.result)} /></td></tr>)}</tbody></table></div>}</section></>}

          {section === "telegram" && <><div className="page-heading"><div><p className="eyebrow">ALLOWLIST</p><h1>Pengguna Telegram</h1><p className="page-subtitle">Akses bot dibatasi ke ID Telegram dan peran yang terdaftar.</p></div><span className="read-only-label"><ShieldCheck size={15} /> Tidak menerima secret</span></div><form className="surface config-form" onSubmit={submitTelegramUser}><div className="form-title"><div><p className="eyebrow">PENGGUNA BARU</p><h2>Tambahkan ID Telegram</h2></div><Command size={17} /></div><div className="form-grid"><label>Telegram user ID<input name="telegram_user_id" type="number" min="1" required /></label><label>Nama tampilan<input name="display_name" maxLength={100} required /></label><label>Peran<select name="role"><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="owner">Owner</option></select></label></div><div className="form-actions"><button className="button button-dark" type="submit"><Plus size={14} /> Tambahkan</button></div></form><section className="surface full-table"><div className="table-toolbar"><b className="toolbar-title">Daftar akses</b><span className="result-count">{telegramUsers.length} pengguna</span></div>{telegramUsers.length === 0 ? <EmptyState title="Allowlist kosong" detail="Tambahkan user ID Telegram untuk mengaktifkan akses bot." /> : <div className="table-wrap"><table><thead><tr><th>NAMA</th><th>TELEGRAM USER ID</th><th>PERAN</th><th>STATUS</th><th></th></tr></thead><tbody>{telegramUsers.map((entry) => <tr key={entry.telegram_user_id}><td><b>{entry.display_name}</b></td><td><code>{entry.telegram_user_id}</code></td><td>{entry.role}</td><td><Badge value={entry.is_active ? "active" : "disabled"} /></td><td><button className="button button-quiet button-small" onClick={() => void toggleTelegramUser(entry)}>{entry.is_active ? "Nonaktifkan" : "Aktifkan"}</button></td></tr>)}</tbody></table></div>}</section></>}

          <footer className="page-footer"><span><Command size={13} /> PBOT PLATFORM <b>v1.0.0</b></span><span>Seluruh waktu dalam WIB <span className="footer-separator">·</span> Zona Asia/Jakarta</span></footer>
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value, note, icon, trend, attention }: { label: string; value: string; note: string; icon: React.ReactNode; trend?: string; attention?: boolean }) {
  return <article className={`metric-card ${attention ? "metric-attention" : ""}`}><div className="metric-top"><span className="metric-icon">{icon}</span>{trend && <span className={`metric-trend ${trend === "live" ? "trend-live" : ""}`}>{trend === "live" && <span className="pulse-dot" />}{trend === "stabil" ? "STABIL" : trend === "terkini" ? "30 TERAKHIR" : "LIVE"}</span>}</div><div className="metric-value">{value}</div><div className="metric-label">{label}</div><div className="metric-note">{note}</div></article>;
}

function JobRow({ job }: { job: Job }) {
  return <div className="activity-row"><span className={`activity-icon activity-${job.status}`}>{job.status === "succeeded" ? <Check size={15} /> : job.status === "failed" ? <X size={15} /> : <Activity size={15} />}</span><span className="activity-main"><b>{actionLabels[job.action] ?? job.action}<span className="activity-on"> pada </span><span className="activity-site">{job.website_id}</span></b><small>{formatTime(job.created_at)}</small></span><Badge value={job.status} /></div>;
}

function ApprovalCard({ approval, onDecide, compact = false }: { approval: Approval; onDecide: (approval: Approval, decision: "approve" | "reject") => void; compact?: boolean }) {
  const summary = Object.entries(approval.preview_summary).slice(0, 3);
  return <article className={`approval-card ${compact ? "approval-card-compact" : ""}`}><div className="approval-card-head"><span className="approval-action-icon"><ShieldCheck size={16} /></span><div><b>{actionLabels[approval.action] ?? approval.action}</b><small>{approval.website_name} · Job {approval.job_id.slice(0, 8)}</small></div><span className="approval-expiry"><Clock3 size={13} />{formatTime(approval.expires_at)}</span></div><div className="preview-summary">{summary.length ? summary.map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><b>{typeof value === "string" ? value : JSON.stringify(value)}</b></div>) : <p>Perubahan siap ditinjau sebelum disimpan.</p>}</div><div className="approval-card-actions"><button className="button button-quiet" onClick={() => onDecide(approval, "reject")}><X size={15} /> Tolak</button><button className="button button-approve" onClick={() => onDecide(approval, "approve")}><Check size={15} /> Setujui perubahan</button></div></article>;
}

function WebsiteTile({ site }: { site: Website }) {
  return <div className="website-tile"><span className="site-avatar">{site.name.slice(0, 1).toUpperCase()}</span><span className="tile-copy"><b>{site.name}</b><small>{site.adapter_id}</small></span><Badge value={site.status} /></div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><span className="empty-icon"><Workflow size={19} /></span><b>{title}</b><p>{detail}</p></div>;
}