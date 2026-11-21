import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, Json } from "./api";

// --- điều hướng ------------------------------------------------------------------
// Mặc định theo hash của trình duyệt. Trong cửa sổ nổi của văn phòng 2D, RouteCtx/NavCtx
// cung cấp "đường dẫn riêng" để các trang chạy bên trong văn phòng mà không rời khỏi nó.
export const parsePath = (h: string): [string, URLSearchParams] => {
  const [path, q] = (h.replace(/^#/, "") || "/").split("?");
  return [path || "/", new URLSearchParams(q || "")];
};
export const RouteCtx = createContext<[string, URLSearchParams] | null>(null);
export const NavCtx = createContext<((path: string) => void) | null>(null);

export function useRoute(): [string, URLSearchParams] {
  const inner = useContext(RouteCtx);
  const [r, setR] = useState(() => parsePath(window.location.hash));
  useEffect(() => {
    const f = () => setR(parsePath(window.location.hash));
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return inner || r;
}
export const go = (path: string) => (window.location.hash = path);
export function useNav(): (path: string) => void {
  return useContext(NavCtx) || go;
}

// --- sự kiện thời gian thực (SSE) ------------------------------------------------
export const EventsCtx = createContext<{ tick: number; events: Json[] }>({ tick: 0, events: [] });
export const useLive = () => useContext(EventsCtx);

// Tải dữ liệu và tự làm mới khi có sự kiện thật từ backend
export function useData<T = Json>(url: string | null, deps: unknown[] = []) {
  const { tick } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!url) return;
    api.get(url).then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message || e)));
  }, [url]);
  useEffect(load, [load, tick, ...deps]);
  return { data, error, reload: load, setData };
}

// --- thông báo -------------------------------------------------------------------
const ToastCtx = createContext<(m: string, kind?: "ok" | "err") => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastHost({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; m: string; kind: string }[]>([]);
  const push = useCallback((m: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, m, kind }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">{items.map((i) => <div key={i.id} className={`toast ${i.kind}`}>{i.m}</div>)}</div>
    </ToastCtx.Provider>
  );
}

// Nút gọi API có trạng thái bận + báo lỗi
export function Act({ onClick, children, kind = "", confirm, title, disabled }: {
  onClick: () => Promise<unknown>; children: ReactNode; kind?: string; confirm?: string; title?: string; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <button className={`btn ${kind}`} disabled={busy || disabled} title={title} onClick={async () => {
      if (confirm && !window.confirm(confirm)) return;
      setBusy(true);
      try { await onClick(); } catch (e: any) { toast(e.message || String(e), "err"); } finally { setBusy(false); }
    }}>{busy ? "…" : children}</button>
  );
}

export const Card = ({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={`card ${className}`}>
    {(title || actions) && <header className="card-h"><h3>{title}</h3><div className="row">{actions}</div></header>}
    {children}
  </section>
);

const STATUS_CLASS: Record<string, string> = {
  running: "blue", queued: "gray", done: "green", failed: "red", cancelled: "gray",
  awaiting_approval: "amber", awaiting_connection: "amber", awaiting_quota: "amber", needs_reconcile: "red",
  pending: "amber", approved: "green", rejected: "red", invalidated: "gray",
};
export const CLI_NAME: Record<string, string> = { claude: "Claude Code", antigravity: "Antigravity CLI" };
export const CLI_SHORT: Record<string, string> = { claude: "Claude", antigravity: "Antigravity" };
/** Đăng nhập CLI: true/false; "?" khi CLI không có lệnh kiểm tra và chưa có lần chạy thật nào; null khi đang tải. */
export function cliLogin(health: Json | null, cli: string): boolean | "?" | null {
  if (!health) return null;
  const c = health.cli?.[cli];
  if (!c) return false;
  return c.logged_in ?? (c.installed ? "?" : false);
}

export const Badge = ({ s, label }: { s: string; label?: string }) => <span className={`badge ${STATUS_CLASS[s] || "gray"}`}>{label || s}</span>;

/** Một văn phòng duy nhất: tự chọn văn phòng làm việc, không hiện gì trên giao diện. */
export function TeamPicker({ value, onChange }: { value: number | null; onChange: (id: number) => void }) {
  const { data } = useData<Json>("/api/workspace");
  useEffect(() => { if (!value && data) onChange(data.id); }, [data, value, onChange]);
  return null;
}

export function useTeamParam(): [number | null, (id: number) => void] {
  const [, q] = useRoute();
  const [id, setId] = useState<number | null>(q.get("team") ? Number(q.get("team")) : null);
  return [id, setId];
}

export const Json_ = ({ v }: { v: unknown }) => <pre className="json">{JSON.stringify(v, null, 2)}</pre>;

export function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="toggle" title={hint}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  );
}

// --- Markdown tối giản cho báo cáo của nhân viên (dựng phần tử React, không chèn HTML thô) --------
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_|https?:\/\/[^\s)]+)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${i++}`;
    if (t.startsWith("**")) out.push(<b key={k}>{t.slice(2, -2)}</b>);
    else if (t.startsWith("`")) out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else if (t.startsWith("_")) out.push(<i key={k}>{t.slice(1, -1)}</i>);
    else out.push(<a key={k} href={t} target="_blank" rel="noreferrer noopener">{t}</a>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Md({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0, n = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      i++;
      blocks.push(<pre key={n++} className="md-pre">{buf.join("\n")}</pre>);
      continue;
    }
    if (/^\s*-\s+/.test(l)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        const sub = /^\s{2,}-/.test(lines[i]);
        items.push(<li key={i} className={sub ? "sub" : ""}>{inline(lines[i].replace(/^\s*-\s+/, ""), `l${i}`)}</li>);
        i++;
      }
      blocks.push(<ul key={n++} className="md-ul">{items}</ul>);
      continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) { blocks.push(<h4 key={n++}>{inline(h[2], `h${i}`)}</h4>); i++; continue; }
    if (!l.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("```") && !/^\s*-\s+/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={n++}>{para.map((x, k) => <span key={k}>{k > 0 && <br />}{inline(x, `p${i}-${k}`)}</span>)}</p>);
  }
  return <div className="md">{blocks}</div>;
}

/** Nút chép nhanh vào clipboard (dán vào Suno / YouTube). */
export function CopyBtn({ text, label = "Chép" }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false);
  const toast = useToast();
  return (
    <button className="btn small" onClick={async () => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); document.body.removeChild(ta);
      }
      setOk(true); toast("Đã chép"); setTimeout(() => setOk(false), 1500);
    }}>{ok ? "✓ Đã chép" : `📋 ${label}`}</button>
  );
}
