// Văn phòng 2D kiểu pixel-art: một văn phòng làm nhạc AI (Suno) + video nhạc; mọi việc đi từ yêu cầu của bạn.
// Toàn bộ thao tác làm ngay trong văn phòng: bấm người/đồ vật, thanh bên có tab, cửa sổ chức năng.
// Nhân viên chỉ di chuyển theo trạng thái thật (xem office/sim.ts); không có tiến độ giả.
import { MouseEvent as RME, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmtTime, Json } from "../api";
import { CLI_NAME, CLI_SHORT, NavCtx, RouteCtx, cliLogin, parsePath, useData, useLive, useRoute, useToast } from "../ui";
import { renderPage, titleOf } from "../routes";
import { MAX_PLAQUES, World } from "../office/map";
import { CLI_COLOR, lookFor, sprites } from "../office/px";
import { Renderer } from "../office/renderer";
import { Agent, OWNER, Sim, wantOf } from "../office/sim";

const short = (s: string | null | undefined, n: number) => (!s ? "" : s.length > n ? s.slice(0, n - 1) + "…" : s);
const mmss = (sec: number) => `${Math.floor(Math.max(0, sec) / 60)}:${String(Math.floor(Math.max(0, sec) % 60)).padStart(2, "0")}`;
const avatar = (id: string, role: string) => sprites.avatar(lookFor(id, role));
const WEEKDAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];
const STATUS_COLOR: Record<string, string> = {
  running: "#38bdf8", queued: "#94a3b8", awaiting_approval: "#f59e0b", awaiting_quota: "#a78bfa", awaiting_connection: "#a78bfa",
  needs_reconcile: "#ef4444", failed: "#ef4444", done: "#22c55e", cancelled: "#64748b",
};
const OPEN = ["queued", "running", "awaiting_approval", "awaiting_connection", "awaiting_quota", "needs_reconcile"];

function Chip({ c, children }: { c: string; children: ReactNode }) {
  return <span className="mo-chip" style={{ ["--c" as string]: c }}>{children}</span>;
}

// --- cửa sổ chức năng (chạy các trang ngay trong văn phòng) ------------------------------------
function Modal({ stack, setStack }: { stack: string[]; setStack: (s: string[]) => void }) {
  const path = stack[stack.length - 1];
  const push = useCallback((p: string) => setStack([...stack, p.replace(/^#/, "")]), [stack, setStack]);
  const route = parsePath(path);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setStack([]); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [setStack]);
  const onClickCapture = (e: RME) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href");
    if (href && href.startsWith("#/")) { e.preventDefault(); push(href.slice(1)); }
  };
  return (
    <div className="mo-modal" onClick={() => setStack([])}>
      <div className="mo-modal-card" onClick={(e) => e.stopPropagation()} onClickCapture={onClickCapture}>
        <header className="mo-modal-head">
          {stack.length > 1 && <button className="mo-btn ghost" onClick={() => setStack(stack.slice(0, -1))}>← Quay lại</button>}
          <div className="mo-crumb">🏢 Văn phòng{stack.map((p, i) => <span key={i}><i>›</i>{titleOf(parsePath(p)[0])}</span>)}</div>
          <button className="mo-icon-btn" onClick={() => setStack([])} title="Đóng (Esc)">✕</button>
        </header>
        <div className="mo-modal-body mo-page">
          <RouteCtx.Provider value={route}>
            <NavCtx.Provider value={push}>{renderPage(route[0]) || <p>Không có chức năng này.</p>}</NavCtx.Provider>
          </RouteCtx.Provider>
        </div>
      </div>
    </div>
  );
}

// --- thanh trên ---------------------------------------------------------------------------------
function Clock({ running }: { running: boolean }) {
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  return (
    <div className="mo-clock">
      <div className="mo-clock-time">{String(now.getHours()).padStart(2, "0")}:{String(now.getMinutes()).padStart(2, "0")}</div>
      <div className="mo-clock-meta">
        <div>{WEEKDAYS[now.getDay()]} · {now.toLocaleDateString("vi-VN")}</div>
        <div className="mo-phase">{running ? "💼 Công ty đang hoạt động" : "⏸ Công ty tạm dừng"}</div>
      </div>
    </div>
  );
}

function ConnBadge({ data, health, onClick }: { data: Json; health: Json | null; onClick: () => void }) {
  const blocked = (cli: string) => data.providers.some((p: Json) => p.cli === cli);
  const items: [string, boolean | "?" | null, boolean][] = [
    ["Claude", cliLogin(health, "claude"), blocked("claude")],
    ["Antigravity", cliLogin(health, "antigravity"), blocked("antigravity")],
    ["Worker", !!data.workers_alive, false],
  ];
  const okCount = items.filter(([, v, b]) => v === true && !b).length;
  const cls = !health ? "off" : okCount === items.length ? "ok" : okCount ? "partial" : "off";
  return (
    <button className={`mo-agent-badge ${cls}`} onClick={onClick} title="Trạng thái kết nối thật — bấm để xem chi tiết">
      🔌 {items.map(([n, v, b]) => <span key={n} className={v === null ? "" : b || v === "?" ? "warn" : v ? "ok-text" : "bad-text"}>{n}{v === null ? "…" : b ? " ⏳" : v === "?" ? " ?" : v ? " ✓" : " ✗"} </span>)}
    </button>
  );
}

// --- thẻ thông tin nhân viên --------------------------------------------------------------------
function InfoCard({ a, data, onClose, open }: { a: Agent; data: Json; onClose: () => void; open: (p: string) => void }) {
  const toast = useToast();
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  if (a.isBoss) {
    return (
      <div className="mo-info">
        <button className="mo-close" onClick={onClose}>✕</button>
        <div className="mo-info-head"><img src={avatar("boss", "boss")} alt="" /><div><div className="mo-info-name">★ Bạn</div><div className="mo-info-role">Giám đốc — người duyệt và quyết định</div></div></div>
        <div className="mo-info-status">{data.pending_approvals ? `📥 ${data.pending_approvals} yêu cầu đang chờ bạn` : "Khay hồ sơ trống"}</div>
        <button className="mo-btn primary wide" onClick={() => open("/approvals")}>Mở khay việc của bạn</button>
        <button className="mo-btn ghost wide" onClick={() => open("/requests?new=1")}>＋ Gửi yêu cầu nhạc mới</button>
      </div>
    );
  }
  const e = a.emp;
  const want = wantOf(e, data.machines);
  const j = e.jobs || {};
  const lists: [string, string][] = [["queued", "Xếp hàng"], ["awaiting_approval", "Chờ duyệt"], ["awaiting_quota", "Chờ lượt CLI"], ["awaiting_connection", "Chờ kết nối"], ["needs_reconcile", "Cần đối chiếu"]];
  return (
    <div className="mo-info">
      <button className="mo-close" onClick={onClose}>✕</button>
      <div className="mo-info-head">
        <img src={avatar(a.id, a.role)} alt="" />
        <div>
          <div className="mo-info-name">{e.name}</div>
          <div className="mo-info-role">{e.role_vi} · <span style={{ color: e.cli === "claude" ? "#f2a679" : "#7fb0f5" }}>{CLI_NAME[e.cli] || e.cli}</span>{e.model ? ` · ${short(e.model, 18)}` : ""}</div>
        </div>
      </div>
      <div className={`mo-info-status tone-${want.tone}`}>{want.icon ? `${want.icon} ` : ""}{want.status}</div>
      {e.job && (
        <>
          <div className="mo-info-row"><span>Việc</span><b>{e.job.kind_vi} #{e.job.id}</b></div>
          {e.job.request_title && <div className="mo-info-row" onClick={() => open(`/request/${e.job.request_id}`)}><span>Yêu cầu</span><b>{short(e.job.request_title, 34)}</b></div>}
          {e.status === "working" && e.job.since && <div className="mo-info-row"><span>Đã chạy</span><b>{mmss(Date.now() / 1000 - e.job.since)}</b></div>}
          {e.job.model && <div className="mo-info-row"><span>Model thực tế</span><b>{e.job.model}</b></div>}
          {e.job.last_tool?.name && <div className="mo-info-row"><span>Công cụ vừa dùng</span><b>🔧 {e.job.last_tool.name}</b></div>}
        </>
      )}
      {lists.map(([k, label]) => (j[k] || []).length ? (
        <div key={k}>
          <div className="mo-info-sub">{label} ({j[k].length})</div>
          <ul className="mo-info-list">{j[k].slice(0, 4).map((x: Json) => <li key={x.id} onClick={() => open(`/jobs?id=${x.id}`)}>{x.kind_vi}{x.request_title ? ` — ${short(x.request_title, 30)}` : ""}<em>#{x.id}</em></li>)}</ul>
        </div>
      ) : null)}
      {(e.failed || []).length > 0 && (
        <div>
          <div className="mo-info-sub">Thất bại 24 giờ qua</div>
          <ul className="mo-info-list">{e.failed.map((x: Json) => <li key={x.id} onClick={() => open(`/jobs?id=${x.id}`)}>{x.kind_vi}<em>#{x.id}</em><div className="mo-reason">{short(x.reason, 90)}</div></li>)}</ul>
        </div>
      )}
      <div className="mo-row">
        <button className="mo-btn primary" onClick={() => open(`/employee/${a.id}`)}>Chi tiết & thao tác</button>
        {e.job && <button className="mo-btn ghost" onClick={() => open(`/jobs?id=${e.job.id}`)}>Xem việc</button>}
        {e.job && e.status === "working" && (
          <button className="mo-btn danger" onClick={async () => { if (confirm("Hủy việc đang chạy?")) { await api.post(`/api/jobs/${e.job.id}/cancel`); toast("Đã yêu cầu hủy"); } }}>Hủy</button>
        )}
      </div>
    </div>
  );
}

// --- các tab thanh bên --------------------------------------------------------------------------
function JobsTab({ data, open, select }: { data: Json; open: (p: string) => void; select: (id: string) => void }) {
  const [filter, setFilter] = useState("open");
  const { data: jobs } = useData<Json[]>("/api/jobs?limit=150");
  const all = jobs || [];
  const groups: [string, string, (j: Json) => boolean][] = [
    ["open", "Đang mở", (j) => OPEN.includes(j.status)],
    ["running", "Đang chạy", (j) => j.status === "running"],
    ["queued", "Xếp hàng", (j) => j.status === "queued"],
    ["approval", "Chờ duyệt", (j) => j.status === "awaiting_approval"],
    ["wait", "Chờ lượt/kết nối", (j) => j.status === "awaiting_quota" || j.status === "awaiting_connection"],
    ["reconcile", "Cần đối chiếu", (j) => j.status === "needs_reconcile"],
    ["failed", "Lỗi", (j) => j.status === "failed"],
    ["done", "Xong", (j) => j.status === "done"],
  ];
  const f = groups.find((g) => g[0] === filter)!;
  const list = all.filter(f[2]);
  const empOf = (j: Json) => data.staff.find((e: Json) => e.role === OWNER[j.kind]) || null;
  return (
    <>
      <div className="mo-filters">
        {groups.map(([k, label, fn]) => {
          const n = all.filter(fn).length;
          return (k === "open" || n > 0 || k === filter) &&
            <button key={k} className={`mo-filter ${filter === k ? "active" : ""}`} onClick={() => setFilter(k)}>{label} <b>{n}</b></button>;
        })}
      </div>
      <div className="mo-list">
        {!jobs && <div className="mo-empty">Đang tải…</div>}
        {jobs && !list.length && <div className="mo-empty">{filter !== "open" ? "Không có việc ở mục này."
          : "Không có việc nào đang mở. Gửi yêu cầu nhạc mới (quầy lễ tân hoặc bảng trắng) rồi bấm ▶ Bắt đầu."}</div>}
        {list.map((j) => {
          const e = empOf(j);
          return (
            <div key={j.id} className="mo-task" onClick={() => open(`/jobs?id=${j.id}`)}>
              <div className="prio" style={{ background: STATUS_COLOR[j.status] || "#64748b" }} />
              <div className="mo-task-main">
                <div className="mo-task-top"><div className="mo-task-title">{j.kind_vi}</div><span className="mo-when">#{j.id} · {fmtTime(j.updated_at)}</span></div>
                <div className="mo-task-meta">
                  <Chip c={STATUS_COLOR[j.status] || "#64748b"}>{j.status_vi}</Chip>
                  {!j.request_id && <Chip c="#64748b">Ban điều hành</Chip>}
                  {j.attempts > 0 && <Chip c="#a78bfa">lần {j.attempts}/{j.max_attempts}</Chip>}
                </div>
                {j.request_title && <div className="mo-reason">🎵 {short(j.request_title, 60)}</div>}
                {(j.wait_reason || (j.status === "failed" && j.error)) && <div className="mo-reason">{short(j.wait_reason || j.error, 120)}</div>}
                {e && (
                  <div className="mo-assignee" onClick={(ev) => { ev.stopPropagation(); select(e.id); }}>
                    <img src={avatar(e.id, e.role)} alt="" />{e.name} · {e.role_vi}
                    <span className="pct">{CLI_SHORT[e.cli] || e.cli}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function ApprovalsTab({ open }: { open: (p: string) => void }) {
  const { data } = useData<Json[]>("/api/inbox");
  if (!data) return <div className="mo-empty">Đang tải…</div>;
  if (!data.length) return <div className="mo-empty">📥 Khay trống — không có yêu cầu nào chờ bạn.</div>;
  return (
    <div className="mo-list">
      {data.map((r) => (
        <div key={r.id} className="mo-task" onClick={() => open(`/request/${r.id}`)}>
          <div className="prio" style={{ background: r.stage === "needs_attention" ? "#ef4444" : "#f59e0b" }} />
          <div className="mo-task-main">
            <div className="mo-task-top"><div className="mo-task-title">✋ {r.stage_vi}</div><span className="mo-when">{fmtTime(r.updated_at)}</span></div>
            <div className="mo-reason">🎵 {short(r.title, 70)}</div>
            <div className="mo-reason">{short(r.gate, 140)}</div>
            <button className="mo-result-btn" onClick={(e) => { e.stopPropagation(); open(`/request/${r.id}`); }}>Mở yêu cầu</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function StaffRow({ e, machines, selected, select }: { e: Json; machines: Json | null; selected: boolean; select: (id: string) => void }) {
  const w = wantOf(e, machines);
  return (
    <div className={`mo-staff ${selected ? "selected" : ""}`} onClick={() => select(e.id)}>
      <img className="mo-avatar" src={avatar(e.id, e.role)} alt="" />
      <div className="mo-staff-main">
        <div className="mo-staff-name">{e.name} <span className="mo-muted">· {e.role_vi}</span><Chip c={CLI_COLOR[e.cli] || "#4d8ee8"}>{CLI_SHORT[e.cli] || e.cli}</Chip></div>
        <div className={`mo-staff-status tone-${w.tone}`}>{w.icon ? `${w.icon} ` : ""}{w.status}</div>
      </div>
    </div>
  );
}

function StaffTab({ data, selected, select, open }: { data: Json; selected: string | null; select: (id: string) => void; open: (p: string) => void }) {
  const toast = useToast();
  return (
    <div className="mo-list">
      <div className="mo-staff director" onClick={() => select("boss")}>
        <img className="mo-avatar" src={avatar("boss", "boss")} alt="" />
        <div className="mo-staff-main"><div className="mo-staff-name">★ Bạn — giám đốc</div><div className="mo-staff-status">{data.pending_approvals ? `📥 ${data.pending_approvals} yêu cầu chờ bạn` : "Khay hồ sơ trống"}</div></div>
      </div>
      <div className="mo-card">
        <div className="mo-team-head">
          <div className="mo-team-name">Nhân viên cố định</div>
          <button className="mo-btn ghost" onClick={() => open("/staff")}>⚙ Đổi tên & cấu hình</button>
        </div>
        <div className="mo-muted">Cùng một văn phòng — mỗi người một việc tại một thời điểm.</div>
        <div className="mo-list">{data.staff.map((e: Json) => <StaffRow key={e.id} e={e} machines={data.machines} selected={selected === e.id} select={select} />)}</div>
      </div>
      {data.teams.map((t: Json) => (
        <div key={t.id} className="mo-card">
          <div className="mo-team-head">
            <div className="mo-team-name">🏢 Văn phòng</div>
            {t.paused && <Chip c="#f59e0b">tạm dừng</Chip>}
            {t.open_jobs > 0 && <Chip c="#64748b">{t.open_jobs} việc mở</Chip>}
            {t.waiting_for_you > 0 && <Chip c="#f59e0b">{t.waiting_for_you} chờ bạn</Chip>}
          </div>
          <div className="mo-row">
            <button className="mo-btn ghost" onClick={async () => { await api.patch("/api/workspace", { paused: !t.paused }); toast(t.paused ? "Văn phòng tiếp tục nhận việc" : "Đã tạm dừng văn phòng"); }}>{t.paused ? "▶ Chạy tiếp" : "⏸ Tạm dừng"}</button>
            <button className="mo-btn ghost" onClick={() => open("/requests")}>🎵 Yêu cầu</button>
          </div>
        </div>
      ))}
    </div>
  );
}

const EV_ICON: Record<string, string> = { job: "📋", cli: "🤖", approval: "📝", video: "🎬", company: "🏢", team: "👥", provider: "⏳", worker: "⚙️", artifact: "📄", asset: "🗂️", voice: "🎙️", scheduler: "🗓️", employee: "👤" };
function LogTab({ live }: { live: Json[] }) {
  const { data } = useData<Json[]>("/api/events?limit=150");
  const merged = useMemo(() => {
    const m = new Map<number, Json>();
    for (const e of [...(data || []), ...live]) m.set(e.id, e);
    return [...m.values()].sort((a, b) => b.id - a.id).slice(0, 200);
  }, [data, live]);
  if (!merged.length) return <div className="mo-empty">Chưa có sự kiện nào.</div>;
  return (
    <div className="mo-log">
      {merged.map((e) => (
        <div key={e.id} className={`mo-log-item lv-${e.level}`}>
          <span className="mo-log-time">{new Date(e.ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</span>
          <span>{EV_ICON[String(e.type).split(".")[0]] || "•"}</span>
          <span>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

function SettingsTab({ data, health, open, showNames, setShowNames }: { data: Json; health: Json | null; open: (p: string) => void; showNames: boolean; setShowNames: (v: boolean) => void }) {
  const c = data.company;
  const flag = (k: string, label: string) => (
    <label className="mo-toggle"><input type="checkbox" checked={!!c[k]} onChange={(e) => api.post("/api/company/flags", { [k]: e.target.checked })} /> {label}</label>
  );
  const row = (label: string, v: boolean | "?" | null | undefined, ok: string, bad: string) => (
    <div className="mo-agent-row"><span>{label}</span><b className={v == null ? "" : v === "?" ? "warn" : v ? "ok-text" : "bad-text"}>{v == null ? "đang kiểm tra…" : v === "?" ? "chưa xác minh — bấm Chạy thử thật" : v ? ok : bad}</b></div>
  );
  const links: [string, string][] = [["/overview", "📊 Tổng quan"], ["/requests", "🎵 Yêu cầu nhạc"], ["/requests?new=1", "＋ Yêu cầu mới"], ["/staff", "👥 Nhân viên"],
    ["/approvals", "📥 Cần bạn xử lý"], ["/memory", "📚 Bộ nhớ"], ["/reports", "📈 Báo cáo"], ["/jobs", "📋 Bảng công việc"]];
  return (
    <>
      <div className="mo-card">
        <div className="mo-form-title">🔌 Kết nối (kiểm tra thật)</div>
        {row("Claude Code", cliLogin(health, "claude"), "đã đăng nhập", "chưa đăng nhập")}
        {row("Antigravity CLI", cliLogin(health, "antigravity"), "đã đăng nhập", health?.cli?.antigravity?.installed ? "chưa sẵn sàng" : "chưa cài")}
        {row("yt-dlp (tải bài mẫu)", health ? !!health.ytdlp?.installed : null, "sẵn sàng", "chưa cài")}
        {row("FFmpeg", health ? !!health.ffmpeg : null, "sẵn sàng", "không có")}
        {row("Worker", !!data.workers_alive, "đang chạy", "chưa chạy")}
        {data.providers.map((p: Json) => <div key={p.cli} className="mo-agent-row"><span>{CLI_NAME[p.cli] || p.cli}</span><b className="warn">hết lượt tới {fmtTime(p.blocked_until)}</b></div>)}
        <button className="mo-btn ghost wide" onClick={() => open("/settings")}>Mở cài đặt kết nối & hướng dẫn đăng nhập</button>
      </div>
      <div className="mo-card">
        <div className="mo-form-title">🛑 Công tắc công ty</div>
        {flag("allow_cli_fallback", "Cho phép CLI dự phòng khi hết lượt")}
        <label className="mo-toggle"><input type="checkbox" checked={showNames} onChange={(e) => setShowNames(e.target.checked)} /> Hiện tên nhân viên</label>
      </div>
      <div className="mo-card">
        <div className="mo-form-title">🧭 Mọi chức năng</div>
        <div className="mo-links">{links.map(([p, l]) => <button key={p} className="mo-btn ghost" onClick={() => open(p)}>{l}</button>)}</div>
      </div>
      <div className="mo-card mo-help">
        <div className="mo-form-title">📖 Văn phòng phản ánh trạng thái thật</div>
        <ol>
          <li>Ngồi bàn, <b>gõ phím, màn hình sáng</b> và đồng hồ ⌨ trên đầu: Claude Code / Antigravity CLI của người đó <b>đang chạy thật</b>.</li>
          <li>Đứng ở <b>máy dựng</b>: máy đang chuẩn hóa âm thanh & dựng video nhạc thật.</li>
          <li>Khay hồ sơ 📥 trên bàn giám đốc: yêu cầu đang chờ bạn (duyệt prompt, tạo nhạc trên Suno, duyệt video).</li>
          <li>Mang tài liệu 📄 sang bàn người khác: một bước <b>vừa hoàn thành</b> (sự kiện thật).</li>
          <li>⏳ hết lượt CLI · 🔌 chờ kết nối · ❓ cần đối chiếu · ⚠️ thất bại · 📋 việc xếp hàng.</li>
          <li>Uống nước, ngồi sofa, chơi game ở góc nghỉ, đi dạo: <b>đang rảnh</b>.</li>
        </ol>
        <p className="mo-muted">Không có thanh tiến độ giả hay câu thoại bịa. Bấm vào người hoặc đồ vật để thao tác.</p>
      </div>
      <button className="mo-btn ghost" onClick={() => { try { localStorage.setItem("mini-view", "table"); } catch { /* chỉ là tùy chọn hiển thị */ } window.location.hash = "/"; window.location.reload(); }}>Chuyển sang chế độ bảng</button>
    </>
  );
}

// --- văn phòng ---------------------------------------------------------------------------------
type Tab = "jobs" | "approvals" | "staff" | "log" | "settings";

export default function Office() {
  const { data } = useData<Json>("/api/office");
  const { data: health } = useData<Json>("/api/health");
  const { data: meta } = useData<Json>("/api/meta");
  const { events } = useLive();
  const [hashPath, q] = useRoute();
  const [stack, setStack] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("jobs");
  const [selected, setSelected] = useState<string | null>(null);
  const [showNames, setShowNames] = useState(true);
  const [cursor, setCursor] = useState("default");
  const toast = useToast();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const eng = useRef<{ sim: Sim; r: Renderer; key: string } | null>(null);
  const live = useRef<{ data: Json | null; health: Json | null }>({ data: null, health: null });
  live.current = { data, health };

  const open = useCallback((p: string) => setStack([p]), []);

  // vào bằng đường dẫn chức năng (vd: liên kết #/approvals) → mở ngay trong văn phòng
  useEffect(() => {
    if (hashPath !== "/" && hashPath !== "/office") {
      setStack([`${hashPath}${q.toString() ? `?${q}` : ""}`]);
      window.history.replaceState(null, "", "#/office");
    }
  }, []); // eslint-disable-line

  // dựng / dựng lại bản đồ khi văn phòng (biển tên trên tường) thay đổi
  const plaqueIds: number[] = data ? data.teams.map((t: Json) => t.id).slice(0, MAX_PLAQUES) : [];
  const worldKey = data ? plaqueIds.join(",") : null;
  useEffect(() => {
    if (!data || !canvasRef.current) return;
    if (!eng.current) {
      const sim = new Sim(new World(plaqueIds));
      sim.sync(data);
      eng.current = { sim, r: new Renderer(canvasRef.current, sim), key: worldKey! };
    } else if (eng.current.key !== worldKey) {
      const w = new World(plaqueIds);
      eng.current.sim.setWorld(w);
      eng.current.r.setWorld(w);
      eng.current.key = worldKey!;
    }
  }, [worldKey, !!data]); // eslint-disable-line

  useEffect(() => { if (data && eng.current) eng.current.sim.sync(data); }, [data]);
  useEffect(() => { if (data && eng.current) eng.current.sim.onEvents(events, data, meta?.jobs || {}); }, [events, data, meta]);
  useEffect(() => { if (eng.current) { eng.current.r.selected = selected; eng.current.r.showNames = showNames; } }, [selected, showNames]);

  // vòng lặp vẽ
  useEffect(() => {
    let raf = 0, last = performance.now();
    const loop = (t: number) => {
      const dt = Math.min(0.1, (t - last) / 1000);
      last = t;
      const e = eng.current, d = live.current.data;
      if (e && d) { e.sim.update(dt); e.r.render({ data: d, health: live.current.health }, t / 1000); }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    // khung văn phòng đổi cỡ cả khi cửa sổ không đổi (vd thanh trên xuống dòng) → đo lại canvas
    const onResize = () => eng.current?.r.resize();
    const stage = canvasRef.current?.parentElement;
    const ro = stage ? new ResizeObserver(onResize) : null;
    if (stage) ro!.observe(stage);
    window.addEventListener("resize", onResize);
    return () => { cancelAnimationFrame(raf); ro?.disconnect(); window.removeEventListener("resize", onResize); };
  }, []);

  // chuột: rê để xem, bấm để chọn/mở, lăn để cuộn tầng, Ctrl+lăn để phóng to
  const drag = useRef<{ y: number; moved: boolean } | null>(null);
  const onMove = (ev: RME<HTMLCanvasElement>) => {
    const e = eng.current, d = live.current.data;
    if (!e || !d) return;
    if (drag.current && ev.buttons === 1) {
      const dy = drag.current.y - ev.clientY;
      if (Math.abs(dy) > 3) { drag.current.moved = true; e.r.scrollBy(dy); drag.current.y = ev.clientY; }
      return;
    }
    const p = e.r.toWorld(ev.clientX, ev.clientY);
    const a = e.r.agentAt(p.x, p.y);
    e.r.hoverAgent = a;
    e.r.hoverHot = a ? null : e.r.hotAt(p.x, p.y);
    const desk = !a && !e.r.hoverHot ? e.r.deskOwnerAt(p.x, p.y, { data: d, health: null }) : null;
    setCursor(a || e.r.hoverHot || desk ? "pointer" : "grab");
  };
  const onClick = (ev: RME<HTMLCanvasElement>) => {
    const e = eng.current, d = live.current.data;
    if (!e || !d || drag.current?.moved) return;
    const p = e.r.toWorld(ev.clientX, ev.clientY);
    const a = e.r.agentAt(p.x, p.y);
    if (a) { setSelected(a.id); return; }
    const h = e.r.hotAt(p.x, p.y);
    if (h) { open(h.path); return; }
    const owner = e.r.deskOwnerAt(p.x, p.y, { data: d, health: null });
    setSelected(owner);
  };
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const onWheel = (ev: WheelEvent) => {
      const e = eng.current;
      if (!e) return;
      ev.preventDefault();
      if (ev.ctrlKey) e.r.setZoom(e.r.zoom * (ev.deltaY < 0 ? 1.1 : 0.9));
      else e.r.scrollBy(ev.deltaY);
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }, [!!data]); // eslint-disable-line

  const select = (id: string | null) => {
    setSelected(id);
    const a = id ? eng.current?.sim.agents.get(id) : null;
    if (a) eng.current!.r.scrollTo(a.y);
  };

  if (!data) return <div className="mo mo-loading">Đang mở văn phòng…</div>;
  const c = data.company;
  const counts = data.job_counts || {};
  const selAgent = selected ? eng.current?.sim.agents.get(selected) : null;
  const tabs: [Tab, string, number][] = [
    ["jobs", "📋 Việc", (counts.running || 0) + (counts.queued || 0)], ["approvals", "📥 Chờ bạn", data.pending_approvals],
    ["staff", "👥 Nhân sự", 0], ["log", "📜 Nhật ký", 0], ["settings", "⚙️", 0],
  ];

  return (
    <div className="mo">
      <header className="mo-topbar">
        <div className="mo-brand"><span className="mo-brand-logo">🏢</span><div><div className="mo-brand-name">ROC</div><div className="mo-brand-sub">công ty nội dung AI</div></div></div>
        <Clock running={!!c.company_running} />
        <div className="mo-speed" role="group" aria-label="Điều khiển công ty">
          <button className={c.company_running ? "active" : ""} disabled={c.company_running} title="Nhân viên bắt đầu / tiếp tục làm việc"
            onClick={async () => { await api.post("/api/company/start"); toast("Công ty bắt đầu làm việc"); }}>▶ Bắt đầu</button>
          <button className={!c.company_running ? "active idle" : ""} disabled={!c.company_running} title="Dừng nhận việc mới; việc đang chạy làm nốt"
            onClick={() => api.post("/api/company/pause")}>⏸ Tạm dừng</button>
          <button className="stop" title="Hủy ngay mọi việc đang chạy"
            onClick={async () => { if (confirm("Hủy ngay mọi việc đang chạy?")) { await api.post("/api/company/emergency_stop"); toast("Đã dừng khẩn cấp"); } }}>■ Dừng khẩn cấp</button>
        </div>
        <ConnBadge data={data} health={health} onClick={() => setTab("settings")} />
        <div className="mo-stats">
          <div className="mo-stat" onClick={() => setTab("jobs")}><b>{counts.running || 0}</b><span>Đang chạy</span></div>
          <div className="mo-stat" onClick={() => setTab("jobs")}><b>{(counts.queued || 0) + (counts.awaiting_quota || 0) + (counts.awaiting_connection || 0)}</b><span>Đang chờ</span></div>
          <div className={`mo-stat ${data.pending_approvals ? "hot" : ""}`} onClick={() => setTab("approvals")}><b>{data.pending_approvals}</b><span>Chờ bạn</span></div>
          <div className="mo-stat" onClick={() => open("/requests")} title="Yêu cầu nhạc đang làm"><b>{data.requests_active || 0}</b><span>🎵 Yêu cầu</span></div>
          {data.needs_reconcile > 0 && <div className="mo-stat bad" onClick={() => open("/jobs?status=needs_reconcile")}><b>{data.needs_reconcile}</b><span>Cần đối chiếu</span></div>}
          <div className="mo-stat ok"><b>{data.done_today || 0}</b><span>Xong hôm nay</span></div>
        </div>
      </header>

      <div className="mo-layout">
        <section className="mo-stage">
          <canvas ref={canvasRef} style={{ cursor }} onMouseMove={onMove} onClick={onClick}
            onMouseDown={(e) => { drag.current = { y: e.clientY, moved: false }; }}
            onMouseUp={() => { setTimeout(() => { drag.current = null; }, 0); }}
            onMouseLeave={() => { drag.current = null; if (eng.current) { eng.current.r.hoverAgent = null; eng.current.r.hoverHot = null; } }} />
          {selAgent && <InfoCard a={selAgent} data={data} onClose={() => setSelected(null)} open={open} />}
          <div className="mo-zoom">
            <button onClick={() => eng.current?.r.setZoom(eng.current.r.zoom / 1.15)} title="Thu nhỏ">－</button>
            <button onClick={() => eng.current?.r.setZoom(1)} title="Vừa khung">⤢</button>
            <button onClick={() => eng.current?.r.setZoom(eng.current.r.zoom * 1.15)} title="Phóng to">＋</button>
          </div>
          <div className="mo-hint">🖱️ Bấm người hoặc đồ vật để thao tác · Ctrl+lăn: phóng to, lăn: cuộn khi đã phóng to</div>
        </section>

        <aside className="mo-sidebar">
          <nav className="mo-tabs">
            {tabs.map(([k, label, n]) => (
              <button key={k} className={`mo-tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)} title={k === "settings" ? "Kết nối & cài đặt" : undefined}>
                {label}{n > 0 && <span className="mo-count">{n}</span>}
              </button>
            ))}
          </nav>
          <div className="mo-panel">
            {tab === "jobs" && <JobsTab data={data} open={open} select={select} />}
            {tab === "approvals" && <ApprovalsTab open={open} />}
            {tab === "staff" && <StaffTab data={data} selected={selected} select={select} open={open} />}
            {tab === "log" && <LogTab live={events} />}
            {tab === "settings" && <SettingsTab data={data} health={health} open={open} showNames={showNames} setShowNames={setShowNames} />}
          </div>
        </aside>
      </div>
      {stack.length > 0 && <Modal stack={stack} setStack={setStack} />}
    </div>
  );
}
