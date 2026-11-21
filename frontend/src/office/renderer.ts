// Vẽ thế giới pixel vào canvas phụ, phóng to sắc nét lên canvas chính, rồi vẽ lớp phủ
// (tên, đồng hồ thời gian chạy thật, biểu tượng trạng thái, bong bóng, chú thích khi rê chuột).
import { Json } from "../api";
import { FURNITURE, RC, buildBackground, drawDecor } from "./furniture";
import { DeskSeat, Furn, Hot, World } from "./map";
import { cliLogin } from "../ui";
import { CLI_COLOR, G, R, ROLE_COLOR, TILE, clamp, makeCanvas, roundRect, sprites, wrapText } from "./px";
import { Agent, Sim } from "./sim";

const FONT = '"Be Vietnam Pro", "Segoe UI", system-ui, "Segoe UI Emoji", sans-serif';
const mmss = (sec: number) => {
  const s = Math.max(0, Math.floor(sec));
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export type Live = { data: Json; health: Json | null };

export class Renderer {
  ctx: G;
  world!: HTMLCanvasElement;
  wg!: G;
  bg!: HTMLCanvasElement;
  w!: World;
  dpr = 1; cssW = 0; cssH = 0; scale = 2; ox = 0; oy = 0;
  camY = 0; zoom = 1;
  hoverAgent: Agent | null = null;
  hoverHot: Hot | null = null;
  selected: string | null = null;
  showNames = true;

  constructor(public canvas: HTMLCanvasElement, public sim: Sim) {
    this.ctx = canvas.getContext("2d")!;
    this.setWorld(sim.world);
  }

  setWorld(w: World) {
    this.w = w;
    this.world = makeCanvas(w.w * TILE, w.h * TILE);
    this.wg = this.world.getContext("2d")!;
    this.bg = buildBackground(w);
    this.resize();
  }

  get WW() { return this.w.w * TILE; }
  get WH() { return this.w.h * TILE; }

  resize() {
    const p = this.canvas.parentElement!;
    const w = p.clientWidth, h = p.clientHeight;
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    this.cssW = w; this.cssH = h;
    // cả văn phòng (kể cả 2 phòng ban) nằm gọn trong khung; Ctrl+lăn để phóng to rồi cuộn
    const fit = Math.min(w / this.WW, h / (this.WH + 40));  // chừa chỗ cho dòng gợi ý & nút phóng to
    const s = fit * this.zoom;
    this.scale = s >= 2 && s - Math.floor(s) < 0.25 ? Math.floor(s) : s;
    this.ox = Math.floor((w - this.WW * this.scale) / 2);
    this.clampCam();
  }

  clampCam() {
    const viewH = this.cssH / this.scale;
    if (this.WH <= viewH) { this.camY = 0; this.oy = Math.floor((this.cssH - this.WH * this.scale) / 2); return; }
    this.oy = 0;
    this.camY = clamp(this.camY, 0, this.WH - viewH);
  }
  scrollBy(dy: number) { this.camY += dy / this.scale; this.clampCam(); }
  scrollTo(worldY: number) { this.camY = worldY - this.cssH / this.scale / 2; this.clampCam(); }
  setZoom(z: number) { this.zoom = clamp(z, 0.6, 2.5); this.resize(); }

  toWorld(cx: number, cy: number) {
    const r = this.canvas.getBoundingClientRect();
    return { x: (cx - r.left - this.ox) / this.scale, y: (cy - r.top - this.oy) / this.scale + this.camY };
  }
  toScreen(x: number, y: number) { return { x: this.ox + x * this.scale, y: this.oy + (y - this.camY) * this.scale }; }

  agentAt(wx: number, wy: number) {
    const list = [...this.sim.agents.values()].sort((a, b) => b.y - a.y);
    for (const a of list) {
      const top = a.y - (a.pose === "sit" ? 21 : 25);
      if (wx >= a.x - 7 && wx <= a.x + 7 && wy >= top && wy <= a.y + 1) return a;
    }
    return null;
  }
  deskRect(d: DeskSeat) {
    return d.side === "top" ? { x: d.seat.x - 16, y: d.furn.y - 6, w: 32, h: 22 } : { x: d.seat.x - 16, y: d.furn.y + 16, w: 32, h: 20 };
  }
  /** Bàn làm việc → mã nhân viên ngồi bàn đó (nếu có). */
  deskOwnerAt(wx: number, wy: number, live: Live): string | null {
    for (const d of this.w.desks) {
      if (!d.role) continue;
      const r = this.deskRect(d);
      if (wx < r.x || wx > r.x + r.w || wy < r.y || wy > r.y + r.h) continue;
      const emp = live.data.staff.find((e: Json) => e.role === d.role);
      if (emp) return emp.id;
    }
    return null;
  }
  hotAt(wx: number, wy: number) {
    for (const h of this.w.hots) if (wx >= h.x && wx <= h.x + h.w && wy >= h.y && wy <= h.y + h.h) return h;
    return null;
  }

  // ---------- khung hình ----------
  render(live: Live, t: number) {
    const rc = this.rc(live, t);
    this.drawWorld(rc, live);
    const g = this.ctx;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.cssW, this.cssH);
    g.imageSmoothingEnabled = false;
    const viewH = Math.min(this.WH - this.camY, this.cssH / this.scale);
    g.drawImage(this.world, 0, this.camY, this.WW, viewH, this.ox, this.oy, this.WW * this.scale, viewH * this.scale);
    this.drawOverlay(g, live);
  }

  private rc(live: Live, t: number): RC {
    const d = live.data, h = live.health;
    const occupied = new Set<string>();
    for (const a of this.sim.agents.values()) if (a.seat && !a.path) occupied.add(a.seat.id);
    const agentFor = (desk: DeskSeat) => {
      for (const a of this.sim.agents.values()) if (a.team === desk.team && a.role === desk.role && !a.isBoss) return a;
      return null;
    };
    const blocked = (cli: string) => d.providers.some((p: Json) => p.cli === cli);
    const GRAY = "#7f8794", GREEN = "#22c55e", RED = "#ef4444", AMBER = "#f59e0b";
    const ok = (v: unknown) => (!h || v == null || v === "?" ? GRAY : v ? GREEN : RED);
    const lamps = [
      blocked("claude") ? AMBER : ok(h?.cli?.claude?.logged_in), blocked("antigravity") ? AMBER : ok(h ? cliLogin(h, "antigravity") : null),
      ok(h?.ffmpeg?.installed), ok(h?.ytdlp?.installed), d.workers_alive ? GREEN : RED,
    ];
    const lt = new Date();
    return {
      t, minute: lt.getHours() * 60 + lt.getMinutes(), occupied, lamps,
      deskState: (desk) => {
        if (!desk.role) return "off";
        const a = agentFor(desk);
        if (!a) return "off";
        return a.typing && a.seat === desk.seat && !a.path ? "on" : "idle";
      },
      deskRole: (desk) => (desk.role && agentFor(desk) ? desk.role : null),
      machineOn: (_team, key) => !!d.machines?.[key],
      pending: d.pending_approvals || 0,
      counts: d.job_counts || {},
      teamVideos: () => d.requests_active || 0,
      occupant: (id) => occupied.has(id),
    };
  }

  private drawWorld(rc: RC, live: Live) {
    const g = this.wg;
    g.drawImage(this.bg, 0, 0);
    drawDecor(g, this.w, rc);
    const items: { y: number; f?: Furn; a?: Agent }[] = this.w.furniture.map((f) => ({ y: f.sortY, f }));
    for (const a of this.sim.agents.values()) items.push({ y: a.y, a });
    items.sort((p, q) => p.y - q.y || (p.a ? 1 : 0) - (q.a ? 1 : 0));
    for (const it of items) {
      if (it.f) FURNITURE[it.f.type]?.(g, it.f, rc);
      else if (it.a) this.drawAgent(g, it.a, rc.t);
    }
  }

  private drawAgent(g: G, a: Agent, t: number) {
    const x = Math.round(a.x), y = Math.round(a.y);
    if (a.pose !== "sit") { R(g, x - 5, y - 2, 10, 2, "rgba(0,0,0,0.16)"); R(g, x - 3, y - 3, 6, 1, "rgba(0,0,0,0.10)"); }
    const frame = a.pose === "walk" ? Math.floor(a.anim) % 4 : 0;
    const tf = a.typing && a.pose === "sit" ? Math.floor(t * 4 + a.x) % 2 : 0;
    g.drawImage(sprites.get(a.look, a.dir, a.pose, frame, a.holding, tf), x - 9, y - 25);
  }

  // ---------- lớp phủ ----------
  private headTop(a: Agent) { return a.y - (a.pose === "sit" ? 21 : 25); }

  private drawOverlay(g: G, live: Live) {
    const d = live.data;
    const agents = [...this.sim.agents.values()].sort((a, b) => a.y - b.y);
    this.drawCaptions(g, live);
    const sel = this.selected ? this.sim.agents.get(this.selected) : null;
    if (sel?.path) this.drawPath(g, sel);
    for (const a of [sel, this.hoverAgent]) if (a) this.drawRing(g, a, a === sel ? "#facc15" : "rgba(255,255,255,0.85)");
    for (const a of agents) if (this.showNames || a === this.hoverAgent || a === sel) this.drawName(g, a);
    const now = Date.now() / 1000;
    for (const a of agents) {
      if (a.bubble) { this.drawBubble(g, a, a.bubble.text, clamp(a.bubble.ttl / 0.3, 0, 1)); continue; }
      const since = Sim.since(a);
      if (since && a.typing) this.drawChip(g, a, `⌨ ${mmss(now - since)}`, ROLE_COLOR[a.role] || "#64748b");
      else if (a.icon && !a.path) this.drawIcon(g, a, a.icon);
    }
    if (this.hoverHot) this.drawHot(g, this.hoverHot);
    if (!d.company.company_running) {
      this.drawPill(g, this.cssW / 2, 22, "⏸  Công ty đang tạm dừng — bấm ▶ Bắt đầu để nhân viên làm việc", "rgba(15,23,42,0.82)", "#fde68a", 13);
    }
  }

  private drawCaptions(g: G, live: Live) {
    const d = live.data;
    // tên công ty trên biển logo
    const logo = this.w.decor.find((x) => x.type === "logo");
    if (logo) this.textAt(g, logo.tx * TILE + 16, 22, "ROC", logo.w * TILE - 22, "#f8fafc", 800);
    for (const dec of this.w.decor) {
      if (dec.type !== "plaque" && dec.type !== "logo") continue;
      if (dec.label) this.textAt(g, dec.tx * TILE + 17, dec.y0 * TILE + 22, dec.label, dec.w * TILE - 22, "#f8fafc", 800);
      const team = d.teams.find((x: Json) => x.id === dec.team);
      if (!team) continue;
      const p = this.toScreen(dec.tx * TILE + 2, dec.y0 * TILE + 34);
      const tags = [team.mode_vi, team.paused ? "⏸ tạm dừng" : null, team.open_jobs ? `📋 ${team.open_jobs}` : null,
        team.pending_approvals ? `📝 ${team.pending_approvals}` : null].filter(Boolean).join(" · ");
      this.smallPill(g, p.x, p.y, tags, team.paused ? "#fde68a" : "#e2e8f0");
    }
    // văn phòng đang khởi tạo: chỗ biển tên trống
    for (const s of this.w.slots) {
      const c = this.toScreen((s.tx + s.w / 2) * TILE, s.y0 * TILE + 26);
      this.drawPill(g, c.x, c.y, "Đang mở văn phòng…", "rgba(15,23,42,0.65)", "#f8fafc", 12);
    }
    // khay hồ sơ trên bàn giám đốc
    if (d.pending_approvals) {
      const p = this.toScreen(4 * TILE + 8, 9 * TILE + 10);
      this.smallPill(g, p.x - 40, p.y, `📥 ${d.pending_approvals} hồ sơ chờ duyệt`, "#fde68a", "rgba(120,53,15,0.85)");
    }
  }

  private textAt(g: G, wx: number, wy: number, text: string, maxW: number, color: string, weight: number) {
    const p = this.toScreen(wx, wy), mw = maxW * this.scale;
    let size = Math.max(7, 5.2 * this.scale);
    g.font = `${weight} ${size}px ${FONT}`;
    while (g.measureText(text).width > mw && size > 6) { size -= 0.5; g.font = `${weight} ${size}px ${FONT}`; }
    g.fillStyle = color; g.textAlign = "left"; g.textBaseline = "middle";
    g.fillText(text, p.x, p.y, mw);
  }

  private smallPill(g: G, x: number, y: number, text: string, fg: string, bg = "rgba(15,23,42,0.7)") {
    if (!text) return;
    const size = clamp(3.3 * this.scale, 10, 12);
    g.font = `600 ${size}px ${FONT}`;
    const w = g.measureText(text).width + 12, h = size + 6;
    g.fillStyle = bg; roundRect(g, x, y, w, h, h / 2); g.fill();
    g.fillStyle = fg; g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(text, x + 6, y + h / 2 + 0.5);
  }

  private drawRing(g: G, a: Agent, color: string) {
    const p = this.toScreen(a.x, a.y);
    g.save(); g.strokeStyle = color; g.lineWidth = 2;
    g.beginPath(); g.ellipse(p.x, p.y - this.scale, 8 * this.scale, 3 * this.scale, 0, 0, Math.PI * 2); g.stroke(); g.restore();
  }

  private drawName(g: G, a: Agent) {
    const p = this.toScreen(a.x, a.y + 2);
    const size = clamp(3.6 * this.scale, 10, 13);
    g.font = `600 ${size}px ${FONT}`;
    const label = a.isBoss ? "★ Bạn" : a.name;
    const w = g.measureText(label).width + 14, h = size + 6;
    const x = clamp(p.x - w / 2, 2, this.cssW - w - 2);
    g.fillStyle = "rgba(15, 23, 42, 0.68)"; roundRect(g, x, p.y, w, h, h / 2); g.fill();
    g.fillStyle = a.isBoss ? "#facc15" : CLI_COLOR[a.emp?.cli] || "#e8834d";
    g.beginPath(); g.arc(x + 6, p.y + h / 2, 2.5, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#f8fafc"; g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(label, x + 10, p.y + h / 2 + 0.5);
  }

  private drawChip(g: G, a: Agent, text: string, color: string) {
    const p = this.toScreen(a.x, this.headTop(a) - 2);
    const size = clamp(3.2 * this.scale, 10, 12);
    g.font = `700 ${size}px ${FONT}`;
    const w = g.measureText(text).width + 12, h = size + 6;
    const x = clamp(p.x - w / 2, 2, this.cssW - w - 2), y = p.y - h - 2;
    g.fillStyle = "rgba(15, 23, 42, 0.82)"; roundRect(g, x, y, w, h, 5); g.fill();
    g.fillStyle = color; g.fillRect(x, y + h - 2, w, 2);
    g.fillStyle = "#f8fafc"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, x + w / 2, y + h / 2);
  }

  private drawIcon(g: G, a: Agent, icon: string) {
    const p = this.toScreen(a.x, this.headTop(a) - 2);
    g.font = `${clamp(5 * this.scale, 13, 18)}px ${FONT}`;
    g.textAlign = "center"; g.textBaseline = "bottom"; g.fillText(icon, p.x, p.y);
  }

  private drawBubble(g: G, a: Agent, text: string, alpha: number) {
    const p = this.toScreen(a.x, this.headTop(a) - 1);
    g.save(); g.globalAlpha = alpha;
    const size = clamp(3.6 * this.scale, 11, 13);
    g.font = `500 ${size}px ${FONT}`;
    const lines = wrapText(g, text, 200, 2), lh = size + 4;
    const w = Math.max(...lines.map((l) => g.measureText(l).width)) + 16, h = lines.length * lh + 10;
    const x = clamp(p.x - w / 2, 2, this.cssW - w - 2), y = p.y - h - 6;
    const tx = clamp(p.x, x + 8, x + w - 8);
    g.fillStyle = "#ffffff"; g.strokeStyle = "rgba(15, 23, 42, 0.35)"; g.lineWidth = 1;
    roundRect(g, x, y, w, h, 8); g.fill(); g.stroke();
    g.beginPath(); g.moveTo(tx - 5, y + h - 1); g.lineTo(tx, y + h + 6); g.lineTo(tx + 5, y + h - 1); g.closePath(); g.fill();
    g.fillStyle = "#0f172a"; g.textAlign = "left"; g.textBaseline = "top";
    lines.forEach((l, i) => g.fillText(l, x + 8, y + 6 + i * lh));
    g.restore();
  }

  private drawPath(g: G, a: Agent) {
    g.save(); g.strokeStyle = ROLE_COLOR[a.role] || "#facc15"; g.globalAlpha = 0.7; g.lineWidth = 2; g.setLineDash([4, 4]);
    g.beginPath();
    const s = this.toScreen(a.x, a.y - 2); g.moveTo(s.x, s.y);
    for (const pt of a.path || []) { const q = this.toScreen(pt.x, pt.y - 2); g.lineTo(q.x, q.y); }
    g.stroke(); g.restore();
  }

  private drawHot(g: G, h: Hot) {
    const p = this.toScreen(h.x, h.y);
    g.save(); g.strokeStyle = "#facc15"; g.lineWidth = 2; g.setLineDash([5, 3]);
    roundRect(g, p.x - 2, p.y - 2, h.w * this.scale + 4, h.h * this.scale + 4, 6); g.stroke(); g.restore();
    const size = 12;
    g.font = `600 ${size}px ${FONT}`;
    const text = h.title, w = g.measureText(text).width + 16, hh = size + 10;
    const x = clamp(p.x + (h.w * this.scale) / 2 - w / 2, 4, this.cssW - w - 4);
    let y = p.y - hh - 8;
    if (y < 4) y = p.y + h.h * this.scale + 8;
    g.fillStyle = "rgba(15,23,42,0.92)"; roundRect(g, x, y, w, hh, 8); g.fill();
    g.fillStyle = "#f8fafc"; g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(text, x + 8, y + hh / 2 + 0.5);
  }

  drawPill(g: G, cx: number, cy: number, text: string, bg: string, fg: string, size: number) {
    g.font = `600 ${size}px ${FONT}`;
    const w = g.measureText(text).width + 24, h = size + 14;
    g.fillStyle = bg; roundRect(g, cx - w / 2, cy - h / 2, w, h, h / 2); g.fill();
    g.fillStyle = fg; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, cx, cy + 1);
  }
}
