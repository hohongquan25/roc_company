// Đồ họa pixel cho văn phòng: sàn, tường, đồ trang trí tường và nội thất (toạ độ thế giới, 1 ô = 16px).
// Phần lớn chuyển từ my_company; các món riêng của ROC (tủ máy chủ, máy dựng…) ở cuối.
// Mọi thứ "đang chạy" (màn hình sáng, đèn máy) chỉ bật theo dữ liệu thật truyền vào qua RC.
import { Decor, DeskSeat, Furn, Rug, World } from "./map";
import { G, R, TILE, hash2, makeCanvas, shade } from "./px";

export type RC = {
  t: number;                                   // giây (chỉ dùng cho hoạt ảnh môi trường)
  minute: number;                              // phút trong ngày theo giờ máy
  occupied: Set<string>;                       // id chỗ ngồi đang có người ngồi
  deskState: (d: DeskSeat) => "off" | "idle" | "on";
  deskRole: (d: DeskSeat) => string | null;
  machineOn: (team: number | undefined, key: string) => boolean;
  lamps: string[];                             // màu 5 đèn tủ máy: Claude, Antigravity, FFmpeg, yt-dlp, Worker
  pending: number;                             // số hồ sơ chờ duyệt
  counts: Record<string, number>;              // số việc mở theo trạng thái
  teamVideos: (team: number | undefined) => number;
  occupant: (spotId: string) => boolean;
};

const FLOOR: Record<string, Record<string, string>> = {
  wood: { base: "#d8bd91", line: "#c4a473", grain: "#e3caa1" },
  darkwood: { base: "#8c6446", line: "#74513a", grain: "#9b7254" },
  carpetBlue: { base: "#7f95ae", noise: "#7388a1" },
  carpetGreen: { base: "#93b59c", noise: "#86a88f" },
  studio: { base: "#8d97b8", noise: "#808aab" },
  tile: { a: "#eef2f5", b: "#dfe6eb" },
  server: { a: "#c5ccd6", b: "#b7bfcb" },
  mat: { base: "#5b4636", edge: "#47362a" },
  door: { base: "#6e5c4d" },
  emptyFloor: { base: "#cbbfa8", line: "#bcae95", grain: "#d3c8b3" },
};
const WALL = { cap: "#3d3850", capEdge: "#4f4866", face: "#efe6d8", rail: "#d6c4a8", wains: "#e4d6c1", base: "#8a6f55" };

// ---------- nền tĩnh (vẽ một lần) ----------
export function buildBackground(w: World) {
  const W = w.w * TILE, H = w.h * TILE;
  const c = makeCanvas(W, H);
  const g = c.getContext("2d")!;
  for (let ty = 0; ty < w.h; ty++) for (let tx = 0; tx < w.w; tx++) floorTile(g, w.floor[ty][tx], tx, ty);
  for (const r of w.rugs) rug(g, r);
  for (const by of w.bands) {
    const y = by * TILE;
    R(g, 0, y, W, 6, WALL.cap);
    R(g, 0, y + 6, W, 42, WALL.face);
    R(g, 0, y + 6, W, 2, "rgba(0,0,0,0.07)");
    R(g, 0, y + 34, W, 1, WALL.rail);
    R(g, 0, y + 35, W, 10, WALL.wains);
    for (let x = 4; x < W; x += 12) R(g, x, y + 36, 1, 8, "rgba(0,0,0,0.04)");
    R(g, 0, y + 45, W, 3, WALL.base);
  }
  for (const d of w.doors) {
    const dx = d.tx * TILE, y = d.y0 * TILE;
    if (d.open) {
      R(g, dx, y, 2 * TILE, 48, FLOOR.door.base);
      R(g, dx, y, 2 * TILE, 6, "#2b2738");
      for (let k = 0; k < 6; k++) R(g, dx + 2, y + 10 + k * 6, 28, 1, "rgba(0,0,0,0.12)");
    } else {
      R(g, dx + 2, y + 8, 28, 40, "#8a6a4a");
      R(g, dx + 15, y + 8, 2, 40, "#5e452f");
      R(g, dx + 11, y + 28, 2, 3, "#e5c07b"); R(g, dx + 19, y + 28, 2, 3, "#e5c07b");
    }
    R(g, dx - 3, y + 4, 3, 44, "#2b2738");
    R(g, dx + 2 * TILE, y + 4, 3, 44, "#2b2738");
  }
  for (const v of w.vwalls) {  // vách ngăn (hiện không dùng: một văn phòng chung)
    R(g, v.tx * TILE, v.ty * TILE, TILE, v.h * TILE, WALL.cap);
    R(g, v.tx * TILE + 2, v.ty * TILE, 2, v.h * TILE, WALL.capEdge);
  }
  R(g, 0, 0, TILE, H, WALL.cap); R(g, TILE - 2, 6, 2, H - 6, WALL.capEdge);
  R(g, W - TILE, 0, TILE, H, WALL.cap); R(g, W - TILE, 6, 2, H - 6, WALL.capEdge);
  R(g, 0, H - TILE, W, TILE, WALL.cap); R(g, TILE, H - TILE, W - 2 * TILE, 2, WALL.capEdge);
  return c;
}

function floorTile(g: G, type: string, tx: number, ty: number) {
  const x = tx * TILE, y = ty * TILE, s = FLOOR[type];
  if (!s) return;
  if (type === "wood" || type === "darkwood" || type === "emptyFloor") {
    R(g, x, y, 16, 16, s.base);
    for (let i = 0; i < 2; i++) {
      const py = y + i * 8, row = ty * 2 + i;
      R(g, x, py + 7, 16, 1, s.line);
      const off = (row * 13) % 32, jx = x + ((32 - ((x + off) % 32)) % 32);
      if (jx < x + 16) R(g, jx, py, 1, 7, s.line);
      const h = hash2(tx, row, 7);
      if (h < 0.6) R(g, x + (Math.floor(h * 20) % 13), py + 2 + (Math.floor(h * 7) % 4), 3, 1, s.grain);
    }
  } else if (s.noise) {
    R(g, x, y, 16, 16, s.base);
    for (let i = 0; i < 7; i++) R(g, x + Math.floor(hash2(tx, ty, i) * 16), y + Math.floor(hash2(ty, tx, i + 9) * 16), 1, 1, s.noise);
  } else if (s.a) {
    for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) R(g, x + i * 8, y + j * 8, 8, 8, (tx * 2 + i + ty * 2 + j) % 2 ? s.a : s.b);
  } else if (type === "mat") {
    R(g, x, y, 16, 16, s.edge); R(g, x + 1, y + 2, 14, 12, s.base);
    for (let i = 0; i < 6; i++) R(g, x + 2 + i * 2, y + 4, 1, 8, "#665040");
  } else R(g, x, y, 16, 16, s.base);
}

function rug(g: G, r: Rug) {
  const x = r.tx * TILE + 4, y = r.ty * TILE + 4, w = r.w * TILE - 8, h = r.h * TILE - 8;
  R(g, x, y, w, h, r.border); R(g, x + 2, y + 2, w - 4, h - 4, r.color);
  R(g, x + 4, y + 4, w - 8, 1, r.border); R(g, x + 4, y + h - 5, w - 8, 1, r.border);
  R(g, x + 4, y + 4, 1, h - 8, r.border); R(g, x + w - 5, y + 4, 1, h - 8, r.border);
}

// ---------- đồ trên tường (vẽ mỗi khung hình) ----------
function sky(minute: number) {
  if (minute < 6 * 60 || minute >= 19 * 60) return { top: "#1c2547", bottom: "#2c3a66", night: true };
  if (minute < 8 * 60) return { top: "#f7c8a0", bottom: "#fde3c2", night: false };
  if (minute < 16 * 60 + 30) return { top: "#8fc9f2", bottom: "#c7e6fb", night: false };
  if (minute < 18 * 60) return { top: "#f29e6d", bottom: "#fbd0a2", night: false };
  return { top: "#6b5b95", bottom: "#c88b8b", night: false };
}

export function drawDecor(g: G, w: World, rc: RC) {
  for (const d of w.decor) decor(g, d, rc);
}

function decor(g: G, d: Decor, rc: RC) {
  const x = d.tx * TILE, w = d.w * TILE, Y = d.y0 * TILE;
  switch (d.type) {
    case "window": {
      const s = sky(rc.minute);
      R(g, x + 1, Y + 11, w - 2, 31, "#8b7355");
      R(g, x + 3, Y + 13, w - 6, 13, s.top); R(g, x + 3, Y + 26, w - 6, 14, s.bottom);
      if (s.night) { R(g, x + 7, Y + 16, 1, 1, "#fff"); R(g, x + 20, Y + 20, 1, 1, "#fff"); R(g, x + 12, Y + 30, 1, 1, "#dbeafe"); }
      else {
        const cx = x + 3 + Math.floor((rc.t * 2 + d.tx * 9) % (w - 6));
        R(g, Math.min(cx, x + w - 10), Y + 18, 6, 2, "rgba(255,255,255,0.85)");
        R(g, Math.min(cx + 1, x + w - 9), Y + 17, 3, 1, "rgba(255,255,255,0.85)");
      }
      R(g, x + w / 2 - 1, Y + 13, 2, 27, "#8b7355"); R(g, x + 3, Y + 26, w - 6, 1, "#8b7355");
      R(g, x + 4, Y + 14, 1, 10, "rgba(255,255,255,0.35)");
      R(g, x, Y + 41, w, 3, "#d9c8ad"); R(g, x, Y + 44, w, 1, "#b8a283");
      break;
    }
    case "certificate":
      R(g, x + 3, Y + 15, 10, 13, "#b08d57"); R(g, x + 4, Y + 16, 8, 11, "#fdf6e3");
      R(g, x + 5, Y + 18, 6, 1, "#cbb88f"); R(g, x + 5, Y + 20, 6, 1, "#cbb88f"); R(g, x + 7, Y + 23, 2, 2, "#dc2626");
      break;
    case "logo":
    case "plaque":
      R(g, x + 3, Y + 13, w - 6, 18, "#263247"); R(g, x + 3, Y + 13, w - 6, 1, "#3b4a66");
      {  // logo ROC 4 màu (cả biển công ty lẫn biển văn phòng)
        R(g, x + 6, Y + 17, 4, 4, "#f59e0b"); R(g, x + 10, Y + 17, 4, 4, "#3b82f6");
        R(g, x + 6, Y + 21, 4, 4, "#22c55e"); R(g, x + 10, Y + 21, 4, 4, "#ec4899");
      }
      break;
    case "board": taskBoard(g, x, w, Y, rc); break;
    case "ideaBoard": {
      R(g, x + 1, Y + 11, w - 2, 30, "#a0754a"); R(g, x + 2, Y + 12, w - 4, 28, "#c89a62");
      const n = Math.min(rc.teamVideos(d.team), 8);
      const notes = ["#fde047", "#93c5fd", "#fca5a5", "#86efac"];
      for (let k = 0; k < n; k++) R(g, x + 5 + (k % 4) * 10, Y + 15 + Math.floor(k / 4) * 11, 8, 8, notes[k % 4]);
      R(g, x + 4, Y + 40, w - 8, 2, "#7a5232");
      break;
    }
    case "clock": wallClock(g, x + 8, Y + 22, rc.minute); break;
    case "calendar":
      R(g, x + 2, Y + 13, 12, 15, "#f8fafc"); R(g, x + 2, Y + 13, 12, 4, "#ef4444");
      for (let k = 0; k < 6; k++) R(g, x + 4 + (k % 3) * 3, Y + 19 + Math.floor(k / 3) * 4, 2, 2, k === 4 ? "#ef4444" : "#94a3b8");
      break;
    case "poster":
      R(g, x + 3, Y + 14, 10, 15, "#fde68a"); R(g, x + 4, Y + 15, 8, 13, "#93c5fd");
      R(g, x + 5, Y + 22, 3, 6, "#16a34a"); R(g, x + 7, Y + 19, 4, 9, "#15803d"); R(g, x + 8, Y + 19, 1, 1, "#f8fafc"); R(g, x + 9, Y + 16, 2, 2, "#fde047");
      break;
    case "onAir": {
      const on = rc.machineOn(d.team, "render"); // đèn ON AIR sáng khi máy dựng đang chạy
      R(g, x + 3, Y + 15, w - 6, 12, "#1f2937");
      R(g, x + 5, Y + 17, w - 10, 8, on ? "#ef4444" : "#4b5563");
      R(g, x + 8, Y + 20, 2, 2, "#f8fafc"); R(g, x + 12, Y + 20, 2, 2, "#f8fafc"); R(g, x + 16, Y + 20, 2, 2, "#f8fafc");
      break;
    }
    case "coffeeSign":
      R(g, x + 2, Y + 15, 12, 10, "#7c2d12"); R(g, x + 5, Y + 18, 5, 4, "#f8fafc"); R(g, x + 10, Y + 19, 1, 2, "#f8fafc");
      R(g, x + 6, Y + 16, 1, 1, "#fde68a"); R(g, x + 8, Y + 16, 1, 1, "#fde68a");
      break;
    case "cabinets":
      R(g, x, Y + 10, w, 15, "#a1887f"); R(g, x, Y + 24, w, 2, "#8d6e63");
      for (let i = 0; i < d.w * 2; i++) { R(g, x + i * 8, Y + 10, 1, 15, "#8d6e63"); R(g, x + i * 8 + (i % 2 ? 1 : 6), Y + 17, 1, 3, "#e5d3c8"); }
      R(g, x, Y + 26, w, 1, "rgba(255,247,214,0.9)");
      break;
  }
}

function taskBoard(g: G, x: number, w: number, Y: number, rc: RC) {
  R(g, x + 1, Y + 11, w - 2, 30, "#94a3b8"); R(g, x + 2, Y + 12, w - 4, 28, "#f8fafc");
  const cols: [string, string, string][] = [
    ["queued", "#cbd5e1", "#fde047"], ["running", "#7dd3fc", "#7dd3fc"], ["awaiting_approval", "#fcd34d", "#fdba74"],
    ["awaiting_quota", "#c4b5fd", "#c4b5fd"], ["needs_reconcile", "#fca5a5", "#fca5a5"],
  ];
  const colW = Math.floor((w - 6) / cols.length);
  cols.forEach(([status, head, note], i) => {
    const cx = x + 3 + i * colW;
    R(g, cx, Y + 13, colW - 1, 2, head);
    const n = Math.min((rc.counts[status] || 0) + (status === "awaiting_quota" ? rc.counts.awaiting_connection || 0 : 0), 8);
    for (let k = 0; k < n; k++) {
      const nx = cx + (k % 2) * 4, ny = Y + 17 + Math.floor(k / 2) * 5;
      R(g, nx, ny, 3, 4, note); R(g, nx, ny, 3, 1, shade(note, -0.12));
    }
    if (i) R(g, cx - 1, Y + 13, 1, 26, "#e2e8f0");
  });
  R(g, x + 4, Y + 40, w - 8, 2, "#94a3b8");
}

function pixelLine(g: G, x0: number, y0: number, x1: number, y1: number, c: string) {
  const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
  for (let i = 0; i <= n; i++) { const t = n ? i / n : 0; R(g, Math.round(x0 + (x1 - x0) * t), Math.round(y0 + (y1 - y0) * t), 1, 1, c); }
}

function wallClock(g: G, cx: number, cy: number, minute: number) {
  const rows = [[-2, 2], [-4, 4], [-5, 5], [-5, 5], [-6, 6], [-6, 6], [-6, 6], [-6, 6], [-5, 5], [-5, 5], [-4, 4], [-2, 2]];
  rows.forEach(([a, b], i) => R(g, cx + a, cy - 6 + i, b - a, 1, "#334155"));
  rows.slice(1, -1).forEach(([a, b], i) => R(g, cx + a + 1, cy - 5 + i, b - a - 2, 1, "#f8fafc"));
  const m = minute % 60, h = (minute / 60) % 12;
  const am = (m / 60) * Math.PI * 2 - Math.PI / 2, ah = (h / 12) * Math.PI * 2 - Math.PI / 2;
  pixelLine(g, cx, cy, cx + Math.round(Math.cos(ah) * 2.5), cy + Math.round(Math.sin(ah) * 2.5), "#0f172a");
  pixelLine(g, cx, cy, cx + Math.round(Math.cos(am) * 4), cy + Math.round(Math.sin(am) * 4), "#ef4444");
}

// ---------- nội thất ----------
const SCREEN: Record<string, { bg: string; lines: string[] }> = {
  lead: { bg: "#0f172a", lines: ["#f87171", "#60a5fa", "#fbbf24", "#e2e8f0"] },
  research: { bg: "#ecfeff", lines: ["#14b8a6", "#0ea5e9", "#0f766e", "#94a3b8"] },
  writer: { bg: "#f5f3ff", lines: ["#8b5cf6", "#94a3b8", "#64748b", "#a78bfa"] },
  production: { bg: "#fff7ed", lines: ["#f97316", "#fb923c", "#3b82f6", "#94a3b8"] },
  qa: { bg: "#eff6ff", lines: ["#3b82f6", "#ef4444", "#1d4ed8", "#94a3b8"] },
  exec_assistant: { bg: "#0f172a", lines: ["#4ade80", "#60a5fa", "#f472b6", "#e2e8f0"] },
};

function screen(g: G, x: number, y: number, w: number, h: number, state: string, role: string, t: number) {
  if (state === "off") { R(g, x, y, w, h, "#1b2433"); R(g, x + 1, y + 1, 2, 1, "#2f3d55"); return; }
  if (state === "idle") { R(g, x, y, w, h, "#22304a"); R(g, x + (Math.floor(t * 3) % (w - 2)), y + (Math.floor(t * 2) % (h - 1)), 2, 1, "#7dd3fc"); return; }
  const th = SCREEN[role] || SCREEN.lead;
  R(g, x, y, w, h, th.bg);
  const scroll = Math.floor(t * 4);
  for (let i = 0; i < h - 1; i += 2) {
    const k = i / 2 + scroll, len = 2 + Math.floor(hash2(k, role.length) * (w - 4));
    R(g, x + 1 + (k % 3), y + 1 + i, Math.min(len, w - 2 - (k % 3)), 1, th.lines[k % th.lines.length]);
  }
}
function monitorBack(g: G, cx: number, top: number) {
  R(g, cx - 7, top, 14, 10, "#2d3340"); R(g, cx - 7, top, 14, 1, "#4a5263"); R(g, cx - 1, top + 10, 2, 2, "#2d3340"); R(g, cx - 3, top + 12, 6, 1, "#1f242e");
}
function mug(g: G, x: number, y: number) { R(g, x, y, 3, 3, "#f8fafc"); R(g, x, y, 3, 1, "#7c4a2d"); R(g, x + 3, y + 1, 1, 1, "#e2e8f0"); }
function deskDecor(g: G, kind: number, x: number, y: number) {
  if (kind === 0) { R(g, x + 1, y + 3, 4, 3, "#b45309"); R(g, x, y, 6, 3, "#22c55e"); R(g, x + 2, y - 1, 2, 1, "#4ade80"); }
  else if (kind === 1) { R(g, x, y + 1, 6, 4, "#f8fafc"); R(g, x + 1, y + 2, 4, 1, "#cbd5e1"); R(g, x + 1, y, 6, 4, "#ffffff"); }
  else if (kind === 2) { R(g, x, y, 5, 5, "#8b5a2b"); R(g, x + 1, y + 1, 3, 3, "#93c5fd"); }
  else { R(g, x + 2, y + 1, 1, 5, "#475569"); R(g, x, y, 5, 2, "#facc15"); }
}
function lamp(g: G, x: number, y: number, color: string) { R(g, x, y, 2, 2, color); }

type Draw = (g: G, f: Furn, rc: RC) => void;
export const FURNITURE: Record<string, Draw> = {
  deskCluster(g, f, rc) {
    const { x, y } = f;
    R(g, x, y + 2, 64, 28, "#dcb88e"); R(g, x, y + 2, 64, 1, "#ead0ad"); R(g, x, y + 15, 64, 2, "#c49a6c"); R(g, x + 31, y + 2, 2, 28, "#c49a6c");
    R(g, x, y + 30, 64, 4, "#a87b4f"); R(g, x, y + 33, 64, 1, "#7d5a38"); R(g, x + 1, y + 34, 2, 2, "#7d5a38"); R(g, x + 61, y + 34, 2, 2, "#7d5a38");
    for (const d of f.seats!) {
      const sx = d.seat.x, st = rc.deskState(d), role = rc.deskRole(d);
      if (d.side === "top") {
        if (role) deskDecor(g, d.decor, sx + 8, y + 4);
        monitorBack(g, sx, y - 3);
        if (st === "on") R(g, sx - 8, y - 2, 1, 8, "rgba(125,211,252,0.35)");
      } else {
        R(g, sx - 7, y + 5, 14, 11, "#2d3340");
        screen(g, sx - 6, y + 6, 12, 8, st, role || "lead", rc.t);
        R(g, sx - 1, y + 16, 2, 2, "#2d3340"); R(g, sx - 3, y + 18, 6, 1, "#1f242e");
        R(g, sx - 5, y + 22, 10, 3, "#e2e8f0"); R(g, sx - 4, y + 23, 8, 1, "#cbd5e1"); R(g, sx + 7, y + 23, 2, 3, "#e2e8f0");
        if (role) deskDecor(g, d.decor, sx - 14, y + 21);
      }
    }
  },
  deskSolo(g, f, rc) {
    const { x, y } = f, d = f.seats![0], sx = d.seat.x;
    R(g, x, y + 2, 32, 16, "#dcb88e"); R(g, x, y + 2, 32, 1, "#ead0ad");
    R(g, x, y + 18, 32, 4, "#a87b4f"); R(g, x, y + 21, 32, 1, "#7d5a38"); R(g, x + 1, y + 22, 2, 2, "#7d5a38"); R(g, x + 29, y + 22, 2, 2, "#7d5a38");
    monitorBack(g, sx, y - 3);
    if (rc.deskState(d) === "on") R(g, sx - 8, y - 2, 1, 8, "rgba(125,211,252,0.35)");
    deskDecor(g, 1, sx + 8, y + 5);
    mug(g, sx - 13, y + 8);
  },
  chairDown(g, f) {
    const sx = f.seat!.x, y = f.y, c = f.color!;
    R(g, sx - 6, y - 4, 12, 9, c); R(g, sx - 6, y - 4, 12, 1, shade(c, 0.25)); R(g, sx - 5, y + 5, 10, 4, shade(c, -0.15));
  },
  chairUp(g, f, rc) {
    const sx = f.seat!.x, ay = f.seat!.y, c = f.color!;
    if (!rc.occupied.has(f.seat!.id)) R(g, sx - 5, ay - 9, 10, 4, shade(c, -0.15));
    R(g, sx - 6, ay - 5, 12, 8, c); R(g, sx - 6, ay - 5, 12, 1, shade(c, 0.25));
    R(g, sx - 1, ay + 3, 2, 2, "#1f2430"); R(g, sx - 5, ay + 5, 10, 1, "#1f2430");
  },
  directorChair(g, f) {
    const sx = f.seat!.x, y = f.y;
    R(g, sx - 7, y - 10, 14, 15, "#4a2c2a"); R(g, sx - 7, y - 10, 14, 1, "#6b3f3b");
    R(g, sx - 4, y - 6, 1, 1, "#2e1a19"); R(g, sx + 3, y - 6, 1, 1, "#2e1a19");
    R(g, sx - 8, y + 2, 2, 6, "#3a2120"); R(g, sx + 6, y + 2, 2, 6, "#3a2120"); R(g, sx - 6, y + 5, 12, 4, "#5a3533");
  },
  directorDesk(g, f, rc) {
    const { x, y } = f, sx = x + 24;
    R(g, x - 2, y + 2, 52, 28, "#7a4e35"); R(g, x - 2, y + 2, 52, 1, "#9a6847");
    R(g, x - 2, y + 30, 52, 6, "#5b3a27"); R(g, x + 2, y + 31, 44, 4, "#6b452f");
    R(g, x + 18, y + 31, 12, 3, "#d4af37"); R(g, x + 19, y + 32, 10, 1, "#f5e08a");
    monitorBack(g, sx, y - 4);
    // khay hồ sơ: số tờ = số hồ sơ thật đang chờ duyệt
    R(g, x + 1, y + 12, 12, 9, "#a16207"); R(g, x + 2, y + 13, 10, 7, "#ca8a04");
    const n = Math.min(rc.pending, 8);
    for (let k = 0; k < n; k++) { R(g, x + 3, y + 17 - k * 2, 8, 2, "#ffffff"); R(g, x + 3, y + 18 - k * 2, 8, 1, "#cbd5e1"); }
    if (n) R(g, x + 11, y + 4 - n * 2 + 12, 3, 3, "#ef4444");
    R(g, x + 32, y + 12, 7, 10, "#a16207"); R(g, x + 33, y + 14, 5, 7, "#f8fafc"); R(g, x + 34, y + 11, 3, 2, "#64748b");
    R(g, x + 34, y + 16, 3, 1, "#22c55e"); R(g, x + 34, y + 18, 3, 1, "#94a3b8");
    R(g, x + 42, y + 4, 1, 9, "#14532d"); R(g, x + 39, y + 2, 7, 3, "#15803d"); R(g, x + 40, y + 13, 5, 1, "#14532d");
    R(g, x + 17, y + 20, 6, 4, "#1f2937"); R(g, x + 18, y + 21, 4, 1, "#475569");
  },
  glassH(g, f) {
    const { x, y, pw } = f;
    R(g, x, y - 8, pw, 22, "rgba(186, 225, 245, 0.35)");
    for (let i = 0; i < pw; i += 22) R(g, x + i + 4, y - 4, 6, 1, "rgba(255,255,255,0.55)");
    R(g, x, y - 9, pw, 2, "#9ca3af"); R(g, x, y + 12, pw, 3, "#6b7280");
    for (let i = 0; i <= pw; i += 32) R(g, Math.min(x + i, x + pw - 2), y - 9, 2, 24, "#9ca3af");
  },
  glassV(g, f) { R(g, f.x + 6, f.y - 9, 4, 16, "rgba(186, 225, 245, 0.45)"); R(g, f.x + 6, f.y - 9, 1, 16, "#9ca3af"); R(g, f.x + 9, f.y - 9, 1, 16, "#9ca3af"); },
  doorFrame(g, f) { R(g, f.x + 5, f.y - 10, 6, 2, "#9ca3af"); R(g, f.x + 4, f.y + 2, 8, 28, "rgba(0, 0, 0, 0.05)"); },
  bookshelf(g, f) {
    const { x, y, pw } = f;
    R(g, x + 1, y - 20, pw - 2, 34, "#7a5232"); R(g, x + 2, y - 19, pw - 4, 31, "#5c3d24");
    const pal = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#14b8a6", "#f8fafc"];
    [-18, -8, 2].forEach((sy, row) => {
      let bx = x + 3;
      while (bx < x + pw - 4) {
        const bw = 2 + Math.floor(hash2(bx, row, f.tx) * 2), bh = 6 + Math.floor(hash2(row, bx, f.ty) * 3);
        R(g, bx, y + sy + (8 - bh), bw, bh, pal[Math.floor(hash2(bx, sy, 5) * pal.length)]);
        bx += bw + (hash2(bx, row, 9) < 0.2 ? 2 : 0);
      }
      R(g, x + 2, y + sy + 8, pw - 4, 2, "#7a5232");
    });
    R(g, x + 1, y + 12, pw - 2, 3, "#4a3020");
  },
  trophyCabinet(g, f) {
    const { x, y } = f;
    R(g, x + 1, y - 2, 30, 16, "#6b4a33"); R(g, x, y - 4, 32, 3, "#8a5f40"); R(g, x + 15, y, 1, 12, "#4f3523");
    R(g, x + 12, y + 5, 2, 2, "#d4af37"); R(g, x + 18, y + 5, 2, 2, "#d4af37");
    R(g, x + 6, y - 12, 5, 5, "#e5b93c"); R(g, x + 7, y - 7, 3, 2, "#c99a2e"); R(g, x + 5, y - 5, 7, 2, "#8a6d1f");
    R(g, x + 4, y - 11, 1, 2, "#e5b93c"); R(g, x + 11, y - 11, 1, 2, "#e5b93c");
    R(g, x + 20, y - 13, 6, 6, "#ef4444"); R(g, x + 22, y - 11, 1, 3, "#fff"); R(g, x + 23, y - 10, 1, 1, "#fff");
    R(g, x + 22, y - 7, 2, 2, "#64748b"); R(g, x + 20, y - 5, 6, 1, "#475569");
  },
  plantBig(g, f) {
    const { x, y } = f;
    R(g, x + 4, y + 6, 8, 9, "#c2683f"); R(g, x + 3, y + 5, 10, 2, "#a3532f"); R(g, x + 5, y + 8, 1, 5, "#d98058");
    R(g, x + 5, y - 13, 6, 5, "#2f8f46"); R(g, x + 2, y - 9, 12, 7, "#3fae5a"); R(g, x + 1, y - 4, 14, 7, "#2f8f46"); R(g, x + 3, y + 1, 10, 4, "#3fae5a");
    R(g, x + 4, y - 7, 2, 2, "#6fd08a"); R(g, x + 10, y - 3, 2, 2, "#6fd08a"); R(g, x + 6, y - 12, 2, 2, "#6fd08a");
    R(g, x + 2, y - 2, 1, 3, "#257a3a"); R(g, x + 13, y - 6, 1, 3, "#257a3a");
  },
  plantSmall(g, f) {
    const { x, y } = f;
    R(g, x + 5, y + 8, 6, 6, "#94a3b8"); R(g, x + 4, y + 7, 8, 2, "#cbd5e1");
    R(g, x + 3, y + 1, 10, 7, "#3fae5a"); R(g, x + 5, y - 2, 6, 4, "#2f8f46"); R(g, x + 6, y + 2, 2, 2, "#6fd08a");
  },
  meetingTable(g, f) {
    const { x, y } = f;
    R(g, x, y + 2, 64, 27, "#8f6f55"); R(g, x + 1, y + 3, 62, 25, "#b08968"); R(g, x + 1, y + 3, 62, 1, "#c7a17f"); R(g, x, y + 29, 64, 5, "#6f5440");
    R(g, x + 28, y + 12, 8, 6, "#1f2937"); R(g, x + 30, y + 14, 4, 2, "#22c55e");
    R(g, x + 5, y + 6, 7, 5, "#f8fafc"); R(g, x + 50, y + 18, 7, 5, "#f8fafc");
    R(g, x + 18, y + 18, 9, 6, "#475569"); R(g, x + 19, y + 19, 7, 4, "#93c5fd");
    R(g, x + 44, y + 6, 2, 5, "#bae6fd"); R(g, x + 14, y + 5, 2, 5, "#bae6fd");
  },
  whiteboardStand(g, f) {
    const { x, y } = f;
    R(g, x + 3, y + 10, 1, 20, "#64748b"); R(g, x + 12, y + 10, 1, 20, "#64748b");
    R(g, x + 1, y - 8, 14, 20, "#94a3b8"); R(g, x + 2, y - 7, 12, 18, "#f8fafc");
    R(g, x + 4, y + 4, 2, 5, "#3b82f6"); R(g, x + 7, y + 1, 2, 8, "#22c55e"); R(g, x + 10, y - 2, 2, 11, "#f97316"); R(g, x + 3, y - 5, 8, 1, "#ef4444");
  },
  printer(g, f, rc) {
    const { x, y } = f;
    R(g, x + 1, y + 2, 14, 12, "#94a3b8"); R(g, x + 1, y + 7, 14, 1, "#64748b");
    R(g, x, y - 6, 16, 9, "#e2e8f0"); R(g, x, y - 6, 16, 1, "#f8fafc"); R(g, x + 3, y - 9, 10, 3, "#f8fafc"); R(g, x + 3, y - 1, 10, 2, "#475569");
    R(g, x + 12, y - 4, 2, 1, Math.floor(rc.t * 2) % 2 ? "#22c55e" : "#16a34a");
  },
  fileCabinet(g, f) {
    const { x, y } = f;
    R(g, x + 1, y - 8, 30, 22, "#64748b"); R(g, x + 1, y - 8, 30, 1, "#94a3b8");
    for (let i = 0; i < 3; i++) {
      R(g, x + 2, y - 6 + i * 7, 13, 6, "#748398"); R(g, x + 17, y - 6 + i * 7, 13, 6, "#748398");
      R(g, x + 7, y - 4 + i * 7, 3, 1, "#cbd5e1"); R(g, x + 22, y - 4 + i * 7, 3, 1, "#cbd5e1");
    }
  },
  trashBin(g, f) { R(g, f.x + 5, f.y + 6, 6, 8, "#475569"); R(g, f.x + 4, f.y + 5, 8, 2, "#64748b"); R(g, f.x + 6, f.y + 4, 3, 1, "#f8fafc"); },
  waterCooler(g, f) {
    const { x, y } = f;
    R(g, x + 3, y + 1, 10, 13, "#e2e8f0"); R(g, x + 3, y + 1, 10, 1, "#f8fafc"); R(g, x + 6, y + 4, 4, 2, "#3b82f6"); R(g, x + 6, y + 6, 4, 1, "#ef4444");
    R(g, x + 4, y - 13, 8, 14, "rgba(96,165,250,0.85)"); R(g, x + 5, y - 15, 6, 2, "#2563eb"); R(g, x + 5, y - 11, 1, 9, "rgba(255,255,255,0.6)");
  },
  counter(g, f, rc) {
    const { x, y, pw } = f;
    R(g, x, y + 2, pw, 13, "#8d6e63");
    for (let i = 1; i < f.w; i++) R(g, x + i * 16, y + 3, 1, 12, "#6d4c41");
    for (let i = 0; i < f.w; i++) R(g, x + i * 16 + 12, y + 7, 1, 3, "#d7ccc8");
    R(g, x, y - 2, pw, 5, "#cfd8dc"); R(g, x, y - 2, pw, 1, "#eceff1");
    R(g, x + 19, y - 14, 10, 13, "#37474f"); R(g, x + 19, y - 15, 10, 2, "#263238"); R(g, x + 21, y - 6, 6, 3, "#111827"); R(g, x + 27, y - 12, 1, 1, "#ef4444"); R(g, x + 21, y - 11, 4, 2, "#90a4ae");
    R(g, x + 35, y - 12, 10, 11, "#455a64"); R(g, x + 37, y - 7, 6, 5, "#7c4a2d"); R(g, x + 37, y - 7, 6, 1, "#a1887f");
    R(g, x + 50, y - 1, 12, 3, "#90a4ae"); R(g, x + 55, y - 7, 2, 6, "#b0bec5"); R(g, x + 55, y - 7, 4, 1, "#b0bec5");
    R(g, x + 65, y - 11, 14, 10, "#eceff1"); R(g, x + 66, y - 10, 9, 7, "#263238"); R(g, x + 76, y - 9, 2, 1, "#22c55e");
    for (const [id, sx] of [["coffee1", 26 * 16 + 8], ["coffee2", 27 * 16 + 8]] as [string, number][]) {
      if (!rc.occupant(id)) continue;
      for (let i = 0; i < 3; i++) {
        const k = (rc.t * 3 + i * 0.33) % 1;
        R(g, sx - 2 + Math.round(Math.sin((k + i) * 6) * 1.5), y - 16 - Math.floor(k * 10), 1, 2, "rgba(255,255,255,0.75)");
      }
    }
  },
  fridge(g, f) {
    const { x, y } = f;
    R(g, x + 1, y - 18, 14, 32, "#eceff1"); R(g, x + 1, y - 18, 14, 1, "#ffffff"); R(g, x + 1, y - 6, 14, 1, "#b0bec5"); R(g, x + 15, y - 17, 1, 31, "#cfd8dc");
    R(g, x + 12, y - 15, 1, 6, "#90a4ae"); R(g, x + 12, y - 3, 1, 8, "#90a4ae");
    R(g, x + 4, y - 14, 2, 2, "#ef4444"); R(g, x + 7, y - 12, 2, 2, "#fbbf24"); R(g, x + 4, y - 2, 3, 4, "#f8fafc");
  },
  vending(g, f) {
    const { x, y } = f;
    R(g, x + 1, y - 18, 14, 32, "#dc2626"); R(g, x + 1, y - 18, 14, 2, "#ef4444"); R(g, x + 3, y - 14, 8, 18, "#1e293b");
    const cs = ["#fbbf24", "#22c55e", "#3b82f6", "#f472b6"];
    for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) R(g, x + 4 + c * 2 + (c ? c - 1 : 0), y - 12 + r * 4, 1, 2, cs[(r + c) % 4]);
    R(g, x + 12, y - 10, 2, 3, "#facc15"); R(g, x + 3, y + 7, 8, 3, "#111827");
  },
  pantryTable(g, f) {
    const { x, y } = f;
    R(g, x + 4, y + 1, 24, 12, "#f5f5f4"); R(g, x + 2, y + 3, 28, 8, "#f5f5f4"); R(g, x + 4, y + 1, 24, 1, "#ffffff");
    R(g, x + 2, y + 11, 28, 2, "#d6d3d1"); R(g, x + 4, y + 13, 24, 1, "#a8a29e"); R(g, x + 14, y + 14, 4, 2, "#78716c");
    R(g, x + 15, y + 2, 2, 4, "#60a5fa"); R(g, x + 14, y, 4, 2, "#f472b6");
  },
  stool(g, f) {
    const { x, y } = f;
    R(g, x + 7, y + 11, 2, 4, "#57534e"); R(g, x + 5, y + 15, 6, 1, "#44403c"); R(g, x + 4, y + 7, 8, 1, "#f59e0b"); R(g, x + 3, y + 8, 10, 3, "#d97706"); R(g, x + 4, y + 11, 8, 1, "#92400e");
  },
  sofaDown(g, f) {
    const { x, y } = f;
    R(g, x, y - 8, 48, 12, "#c2414f"); R(g, x, y - 8, 48, 2, "#d95b68"); R(g, x, y - 2, 5, 14, "#a83442"); R(g, x + 43, y - 2, 5, 14, "#a83442");
    R(g, x + 5, y + 3, 38, 8, "#df5f6e"); R(g, x + 17, y + 3, 1, 8, "#c2414f"); R(g, x + 30, y + 3, 1, 8, "#c2414f");
    R(g, x + 2, y + 11, 44, 3, "#8f2b37"); R(g, x + 3, y + 14, 2, 2, "#4a2c2a"); R(g, x + 43, y + 14, 2, 2, "#4a2c2a");
  },
  sofaUp(g, f, rc) {
    const { x, y } = f;
    for (let i = 0; i < 3; i++) if (!rc.occupied.has("sofaB" + i)) R(g, x + 5 + i * 13, y - 3, 12, 7, "#df5f6e");
    R(g, x, y - 4, 5, 16, "#a83442"); R(g, x + 43, y - 4, 5, 16, "#a83442");
    R(g, x + 2, y + 3, 44, 11, "#c2414f"); R(g, x + 2, y + 3, 44, 2, "#d95b68"); R(g, x + 3, y + 14, 2, 2, "#4a2c2a"); R(g, x + 43, y + 14, 2, 2, "#4a2c2a");
  },
  coffeeTable(g, f) {
    const { x, y } = f;
    R(g, x + 4, y + 3, 40, 8, "#8d6e63"); R(g, x + 4, y + 3, 40, 1, "#a1887f"); R(g, x + 4, y + 11, 40, 2, "#6d4c41");
    R(g, x + 6, y + 13, 2, 2, "#4e342e"); R(g, x + 40, y + 13, 2, 2, "#4e342e");
    R(g, x + 9, y + 5, 7, 4, "#60a5fa"); R(g, x + 11, y + 4, 7, 4, "#f472b6"); mug(g, x + 30, y + 5); R(g, x + 22, y + 4, 4, 4, "#3fae5a");
  },
  arcade(g, f, rc) {
    const { x, y } = f;
    R(g, x + 1, y - 18, 14, 32, "#4c1d95"); R(g, x + 1, y - 18, 14, 3, "#6d28d9");
    const busy = rc.occupant("arcade"), k = Math.floor(rc.t * 4);
    R(g, x + 3, y - 14, 10, 8, busy ? ["#22d3ee", "#a3e635", "#f472b6"][k % 3] : "#1e1b4b");
    if (busy) R(g, x + 4 + (k % 7), y - 11, 2, 2, "#fde047");
    R(g, x + 1, y - 5, 14, 3, "#1e1b4b"); R(g, x + 5, y - 7, 1, 2, "#ef4444"); R(g, x + 9, y - 6, 2, 1, "#22c55e");
    R(g, x + 2, y - 1, 12, 12, "#3b0f7a"); R(g, x + 4, y + 2, 8, 1, "#f472b6");
  },

  // ----- đồ riêng của ROC -----
  racks(g, f, rc) {
    // 5 tủ máy: Claude, Antigravity, FFmpeg, yt-dlp, Worker — đèn theo kiểm tra thật
    for (let i = 0; i < 5; i++) {
      const x = f.x + i * 16, y = f.y;
      R(g, x + 1, y - 20, 14, 34, "#1c222d"); R(g, x + 1, y - 20, 14, 1, "#3b4252"); R(g, x + 14, y - 19, 1, 33, "#11151c");
      for (let r = 0; r < 5; r++) {
        R(g, x + 3, y - 17 + r * 6, 10, 4, "#2c3340");
        R(g, x + 4, y - 16 + r * 6, 5, 1, "#3e4757");
        const blink = rc.lamps[i] === "#22c55e" && (Math.floor(rc.t * 3 + r * 1.7 + i) % 4 === 0);
        lamp(g, x + 10, y - 16 + r * 6, r === 0 ? rc.lamps[i] : blink ? "#86efac" : "#3d4656");
      }
      R(g, x + 2, y + 12, 12, 2, "#11151c");
    }
  },
  reception(g, f) {
    const { x, y, pw } = f;
    R(g, x, y - 4, pw, 18, "#7a4e35"); R(g, x, y - 4, pw, 2, "#9a6847"); R(g, x, y + 10, pw, 4, "#5b3a27");
    R(g, x + 4, y - 1, pw - 8, 8, "#8a5a3e");
    R(g, x + pw / 2 - 9, y - 1, 18, 9, "#f5a524"); R(g, x + pw / 2 - 1, y + 1, 2, 5, "#1a1205"); R(g, x + pw / 2 - 3, y + 3, 6, 1, "#1a1205");
    R(g, x + 6, y - 10, 9, 6, "#f8fafc"); R(g, x + 7, y - 8, 7, 1, "#cbd5e1");
    R(g, x + pw - 14, y - 9, 8, 6, "#3fae5a"); R(g, x + pw - 12, y - 4, 4, 3, "#c2683f");
  },
  renderRig(g, f, rc) {
    const { x, y } = f, on = rc.machineOn(f.team, "render"), k = Math.floor(rc.t * 4);
    R(g, x + 2, y - 18, 44, 32, "#334155"); R(g, x + 2, y - 18, 44, 2, "#475569");
    R(g, x + 5, y - 15, 24, 15, "#0f172a");
    if (on) {
      R(g, x + 6, y - 14, 22, 9, "#1e3a8a");
      for (let i = 0; i < 4; i++) R(g, x + 7 + i * 5, y - 13 + ((i + k) % 3) * 2, 4, 2, ["#f97316", "#38bdf8", "#a3e635", "#f472b6"][i]);
      R(g, x + 6, y - 4, Math.floor(((rc.t * 6) % 22)), 2, "#f8fafc");
    }
    R(g, x + 32, y - 15, 11, 26, "#1e293b");
    for (let r = 0; r < 4; r++) lamp(g, x + 35, y - 12 + r * 6, on && (k + r) % 2 ? "#f97316" : "#3d4656");
    R(g, x + 5, y + 2, 24, 4, "#1f2937"); R(g, x + 7, y + 3, 20, 1, "#475569");
    R(g, x + 2, y + 12, 44, 2, "#1e293b");
  },
};
