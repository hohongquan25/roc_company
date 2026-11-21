import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Json } from "./api";
import { EventsCtx, ToastHost, useData, useRoute } from "./ui";
import { renderPage } from "./routes";
import Office from "./pages/Office";
import "./styles.css";

const NAV: [string, string][] = [
  ["/", "Tổng quan"], ["/requests", "Yêu cầu nhạc"], ["/approvals", "Cần bạn xử lý"], ["/staff", "Nhân viên"],
  ["/jobs", "Bảng công việc"], ["/memory", "Bộ nhớ"], ["/reports", "Báo cáo & nhật ký"], ["/settings", "Cài đặt kết nối"],
];

const VIEW_KEY = "mini-view";
const readView = () => { try { return localStorage.getItem(VIEW_KEY) || "office"; } catch { return "office"; } };

function TableShell({ onOffice }: { onOffice: () => void }) {
  const [path] = useRoute();
  const { data: ov } = useData<Json>("/api/overview");
  const running = ov?.company?.company_running;
  return (
    <div className="layout">
      <nav className="side">
        <div className="brand">ROC<span>văn phòng nhạc AI</span></div>
        <div className={`company-state ${running ? "on" : "off"}`}>
          {running ? "● Đang hoạt động" : "○ Đang tạm dừng"}
          {ov && !ov.workers?.length && <div className="warn-s">Worker chưa chạy</div>}
        </div>
        <div className="view-switch" title="Chuyển kiểu giao diện">
          <button className="on">Bảng</button>
          <button onClick={onOffice}>Văn phòng 2D</button>
        </div>
        {NAV.map(([p, label]) => (
          <a key={p} href={`#${p}`} className={path === p || (p !== "/" && path.startsWith(p.replace(/s$/, ""))) ? "on" : ""}>
            {label}
            {p === "/approvals" && ov?.pending_approvals ? <span className="count">{ov.pending_approvals}</span> : null}
            {p === "/jobs" && ov?.needs_reconcile ? <span className="count red">{ov.needs_reconcile}</span> : null}
          </a>
        ))}
        <a href="#/requests?new=1" className="add">＋ Yêu cầu mới</a>
      </nav>
      <main>{renderPage(path) || <p>Không có trang này.</p>}</main>
    </div>
  );
}

function Shell() {
  // Văn phòng 2D là giao diện mặc định; "Chế độ bảng" trong văn phòng chuyển về giao diện bảng.
  const [view, setView] = useState(readView);
  const toOffice = () => {
    try { localStorage.setItem(VIEW_KEY, "office"); } catch { /* chỉ là tùy chọn hiển thị */ }
    window.location.hash = "/office";
    setView("office");
  };
  return view === "office" ? <Office /> : <TableShell onOffice={toOffice} />;
}

function App() {
  const [tick, setTick] = useState(0);
  const [events, setEvents] = useState<Json[]>([]);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      setEvents((x) => [e, ...x].slice(0, 300));
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setTick((t) => t + 1), 400);
    };
    return () => es.close();
  }, []);
  return (
    <EventsCtx.Provider value={{ tick, events }}>
      <ToastHost><Shell /></ToastHost>
    </EventsCtx.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
