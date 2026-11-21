// Nhân viên trong văn phòng di chuyển theo TRẠNG THÁI THẬT lấy từ /api/office và sự kiện thật:
//  - CLI đang chạy thật      → ngồi bàn, màn hình sáng, gõ phím, đồng hồ thời gian chạy thật trên đầu
//  - máy dựng chạy           → đứng vận hành máy dùng chung (dựng video nhạc)
//  - yêu cầu chờ bạn         → hồ sơ nằm trên khay bàn giám đốc
//  - hết lượt / chờ kết nối  → ra sảnh nghỉ, kèm biểu tượng lý do
//  - một bước vừa xong       → mang tài liệu sang bàn người làm bước tiếp theo (sự kiện job.done thật)
//  - rảnh                    → uống nước, ngồi sofa, đi dạo trong pantry/sảnh nghỉ
// Không có tiến độ giả: không thanh %, không câu thoại bịa về công việc.
import { Json } from "../api";
import { MACHINE_ROLE, Spot, World, findPath } from "./map";
import { Dir, Holding, Look, Pose, TILE, dist, hash2, hashStr, lookFor, standPos, tileOf } from "./px";

const SPEED = 54; // px thế giới mỗi giây
const STEP_PX = 5;

// bước vừa xong → vai trò nhận bàn giao; "boss" = mang lên phòng giám đốc
const NEXT: Record<string, string> = { lead_plan: "research", research: "writer", write: "qa", check: "boss", produce: "qa", verify: "boss", summary: "boss" };
export const OWNER: Record<string, string> = {
  lead_plan: "lead", research: "research", write: "writer", check: "qa", produce: "production", verify: "qa",
  summary: "exec_assistant", report: "exec_assistant",
};
const MACHINE_ICON: Record<string, string> = { render: "🎬" };

export type Want = {
  key: string; spots: string[] | null; spotId?: string; typing: boolean; holding: Holding;
  icon: string | null; tone: "work" | "machine" | "wait" | "alert" | "blocked" | "idle"; status: string;
};

export type Agent = {
  id: string; role: string; team: number | null; name: string; roleVi: string; look: Look;
  x: number; y: number; dir: Dir; pose: Pose; anim: number; path: { x: number; y: number }[] | null;
  holding: Holding; seat: Spot | null; exit: { tx: number; ty: number } | null; dest: Spot | null;
  want: Want; typing: boolean; icon: string | null; bubble: { text: string; ttl: number } | null;
  errand: { spot: Spot; text: string; stage: "go" | "wait"; until: number } | null;
  idleUntil: number; idleSeed: number; emp: Json; since: number | null; isBoss?: boolean;
};

const short = (s: string | null | undefined, n: number) => (!s ? "" : s.length > n ? s.slice(0, n - 1) + "…" : s);

/** machines: máy dùng chung đang chạy việc thật (từ /api/office). */
export function wantOf(e: Json, machines: Json | null): Want {
  const j = e.jobs || {};
  const desk = ["desk"];
  const w = (key: string, spots: string[] | null, tone: Want["tone"], status: string, icon: string | null = null, typing = false, holding: Holding = null): Want =>
    ({ key, spots, tone, status, icon, typing, holding });
  if (e.status === "working" && e.job) {
    return w("work", desk, "work", `Đang làm: ${e.job.kind_vi}${e.job.request_title ? ` — ${short(e.job.request_title, 40)}` : ""}`, null, true);
  }
  if (e.status === "waiting_slot") return w("slot", desk, "wait", "Chờ lượt chạy CLI (đã đủ số CLI chạy cùng lúc)", "⌛");
  for (const m of Object.keys(MACHINE_ROLE)) {
    if (MACHINE_ROLE[m] === e.role && machines?.[m]) {
      const ch = machines[m].request_title ? ` · ${short(machines[m].request_title, 30)}` : "";
      return { ...w(`op-${m}`, null, "machine", `Vận hành máy: ${machines[m].kind_vi}${ch}`, MACHINE_ICON[m]), spotId: `op-${m}` };
    }
  }
  if (j.needs_reconcile?.length) return w("reconcile", desk, "alert", `Cần bạn đối chiếu: ${j.needs_reconcile[0].kind_vi}`, "❓");
  if (j.awaiting_approval?.length) return w("approval", ["approval"], "wait", `Chờ bạn duyệt: ${j.awaiting_approval[0].kind_vi}`, "📝", false, "paper");
  if (j.awaiting_quota?.length) return w("quota", ["sofa", "pantrySeat"], "blocked", `Hết lượt CLI — chờ tới lượt mới`, "⏳");
  if (j.awaiting_connection?.length) return w("conn", ["sofa", "pantrySeat"], "blocked", `Chờ kết nối: ${short(j.awaiting_connection[0].reason, 60) || "cần thiết lập"}`, "🔌");
  if (e.failed?.length) return w("failed", desk, "alert", `Việc thất bại gần đây: ${e.failed[0].kind_vi}`, "⚠️");
  if (j.queued?.length) return w("queued", desk, "wait", `${j.queued.length} việc đang xếp hàng`, "📋");
  return w("idle", null, "idle", "Đang rảnh");
}

const IDLE_KINDS = ["coffee", "water", "snack", "pantrySeat", "sofa", "book", "window", "arcade", "wander", "wander"];

export class Sim {
  agents = new Map<string, Agent>();
  world: World;
  private lastEvent = 0;
  constructor(world: World) { this.world = world; }

  setWorld(world: World) {
    this.world = world;
    for (const a of this.agents.values()) { a.seat = null; a.dest = null; a.path = null; a.exit = null; a.errand = null; this.placeAtGoal(a); }
  }

  // ----- đồng bộ từ dữ liệu thật -----
  sync(data: Json) {
    const seen = new Set<string>();
    const add = (e: Json) => {
      seen.add(e.id);
      const want = wantOf(e, data.machines);
      let a = this.agents.get(e.id);
      if (!a) {
        a = { id: e.id, role: e.role, team: null, name: e.name, roleVi: e.role_vi, look: lookFor(e.id, e.role),
          x: 0, y: 0, dir: "down", pose: "stand", anim: 0, path: null, holding: null, seat: null, exit: null, dest: null,
          want, typing: false, icon: null, bubble: null, errand: null, idleUntil: 0, idleSeed: hashStr(e.id), emp: e, since: null };
        this.agents.set(e.id, a);
        this.placeAtGoal(a);
        return;
      }
      a.emp = e; a.name = e.name; a.roleVi = e.role_vi;
      if (a.want.key !== want.key || a.want.spotId !== want.spotId) {
        a.want = want;
        if (want.typing) a.errand = null; // có việc thật → về bàn ngay
        this.retarget(a);
      } else a.want = want;
    };
    // nhân sự cố định của công ty (kể cả trợ lý điều hành) — làm việc chung trong văn phòng
    data.staff.forEach((e: Json) => add(e));
    for (const id of [...this.agents.keys()]) {
      if (!seen.has(id) && id !== "boss") { this.world.releaseAll(id); this.agents.delete(id); }
    }
    if (!this.agents.has("boss")) {
      const s = this.world.get("boss");
      const boss: Agent = { id: "boss", role: "boss", team: null, name: "Bạn", roleVi: "Giám đốc", look: lookFor("boss", "boss"),
        x: s.x, y: s.y, dir: "down", pose: "sit", anim: 0, path: null, holding: null, seat: s, exit: s.access, dest: s,
        want: { key: "boss", spots: null, typing: false, holding: null, icon: null, tone: "idle", status: "Bạn — người duyệt và quyết định" },
        typing: false, icon: null, bubble: null, errand: null, idleUntil: 0, idleSeed: 1, emp: {}, since: null, isBoss: true };
      this.world.reserve(s, "boss");
      this.agents.set("boss", boss);
    }
  }

  /** Sự kiện hoàn thành thật → người làm mang tài liệu sang bàn người làm bước tiếp theo. */
  onEvents(events: Json[], data: Json, jobVi: Record<string, string>) {
    const fresh = events.filter((e) => e.id > this.lastEvent);
    if (events.length) this.lastEvent = Math.max(this.lastEvent, events[0].id);
    if (!this.primed) { this.primed = true; return; } // bỏ qua các sự kiện cũ lúc mới mở
    for (const ev of fresh.reverse()) {
      if (ev.type !== "job.done") continue;
      let kind: string | undefined;
      try { kind = ev.data ? JSON.parse(ev.data).kind : undefined; } catch { kind = undefined; }
      if (!kind || !OWNER[kind]) continue;
      const emp = data.staff.find((e: Json) => e.role === OWNER[kind!]);
      const a = emp && this.agents.get(emp.id);
      if (!a) continue;
      if (a.want.typing || !NEXT[kind]) { a.bubble = { text: `✅ Xong: ${jobVi[kind] || kind}`, ttl: 3 }; continue; }
      const to = NEXT[kind];
      let spot: Spot | null = null;
      if (to === "boss") spot = this.world.claim(["approval"], a.id, this.world.get("boss"));
      else {
        const d = this.world.deskOf(null, to);
        if (d) {
          const t = this.world.besideDesk(d), p = standPos(t.tx, t.ty);
          spot = { id: `errand-${a.id}`, kind: "errand", x: p.x, y: p.y, dir: "up", pose: "stand", access: t, occupant: a.id };
        }
      }
      if (!spot) continue;
      a.errand = { spot, text: `📄 ${to === "boss" ? "Trình giám đốc" : "Bàn giao"}: ${jobVi[kind] || kind}`, stage: "go", until: 0 };
      a.holding = "paper";
      this.goTo(a, spot);
    }
  }
  private primed = false;

  // ----- chọn chỗ & đường đi -----
  private goalSpot(a: Agent): Spot | null {
    const w = this.world, want = a.want;
    if (want.spotId) { const s = w.get(want.spotId); return s && w.reserve(s, a.id) ? s : null; }
    if (want.spots?.[0] === "desk") {
      const d = w.deskOf(a.team, a.role);
      return d ? d.seat : null;
    }
    if (want.spots) return w.claim(want.spots, a.id, want.spots[0] === "approval" ? w.get("boss") : undefined, a.idleSeed + Math.floor(Date.now() / 60000));
    return null;
  }

  private placeAtGoal(a: Agent) {
    this.world.releaseAll(a.id);
    let s = this.goalSpot(a);
    if (!s) s = this.idleSpot(a);
    if (s) { this.world.reserve(s, a.id); a.x = s.x; a.y = s.y; this.settle(a, s); }
    else { const t = this.world.randomTile(["lounge", "pantry"], a.idleSeed); const p = standPos(t.tx, t.ty); a.x = p.x; a.y = p.y; a.pose = "stand"; }
    a.typing = a.want.typing && a.seat?.kind === "desk";
    a.icon = a.want.icon;
    a.holding = a.want.holding;
    a.idleUntil = Date.now() / 1000 + 20 + hash2(a.idleSeed, 3) * 30;
  }

  private idleSpot(a: Agent): Spot | null {
    const k = IDLE_KINDS[Math.floor(hash2(a.idleSeed, Math.floor(Date.now() / 1000)) * IDLE_KINDS.length)];
    if (k === "wander") {
      const t = this.world.randomTile(["lounge", "pantry"], a.idleSeed + Math.floor(Date.now() / 1000));
      const p = standPos(t.tx, t.ty);
      return { id: `wander-${a.id}`, kind: "wander", x: p.x, y: p.y, dir: "down", pose: "stand", access: t, occupant: a.id };
    }
    return this.world.claim([k], a.id, undefined, a.idleSeed + Math.floor(Date.now() / 1000));
  }

  private retarget(a: Agent) {
    this.world.releaseAll(a.id, a.errand?.spot);
    if (a.errand) return;
    const s = this.goalSpot(a) || this.idleSpot(a);
    a.icon = a.want.icon;
    a.holding = a.want.holding;
    a.typing = false;
    a.idleUntil = Date.now() / 1000 + 20 + hash2(a.idleSeed, Math.floor(Date.now() / 1000)) * 30;
    if (s) this.goTo(a, s);
  }

  private goTo(a: Agent, s: Spot) {
    const w = this.world;
    if (!a.path && dist(a.x, a.y, s.x, s.y) < 1) { this.settle(a, s); return; }
    const from = a.exit || { tx: tileOf(a.x), ty: tileOf(a.y) };
    const start = w.isWalkable(from.tx, from.ty) ? from : w.nearestWalkable(from.tx, from.ty);
    const tiles = findPath(w, start, s.access);
    a.seat = null; a.exit = null; a.typing = false;
    a.dest = s;
    if (!tiles) { a.x = s.x; a.y = s.y; this.settle(a, s); return; } // không có đường (hiếm): đặt thẳng vào chỗ
    const pts: { x: number; y: number }[] = [];
    pts.push(standPos(start.tx, start.ty));
    for (const t of tiles) pts.push(standPos(t.tx, t.ty));
    pts.push({ x: s.x, y: s.y });
    a.path = pts;
    a.pose = "walk";
  }

  private settle(a: Agent, s: Spot) {
    a.x = s.x; a.y = s.y; a.dir = s.dir; a.dest = s;
    a.pose = s.pose === "sit" ? "sit" : "stand";
    a.seat = s; a.exit = s.access; a.path = null;
  }

  // ----- mỗi khung hình -----
  update(dt: number) {
    const now = Date.now() / 1000;
    for (const a of this.agents.values()) {
      if (a.bubble) { a.bubble.ttl -= dt; if (a.bubble.ttl <= 0) a.bubble = null; }
      if (a.isBoss) continue;
      if (a.path) {
        let budget = SPEED * dt;
        while (a.path && a.path.length && budget > 0) {
          const p = a.path[0], dx = p.x - a.x, dy = p.y - a.y, d = Math.hypot(dx, dy);
          if (d > 0.01) {
            if (Math.abs(dx) > Math.abs(dy) + 0.01) a.dir = dx > 0 ? "right" : "left";
            else if (Math.abs(dy) > 0.01) a.dir = dy > 0 ? "down" : "up";
          }
          const step = Math.min(d, budget);
          if (d <= budget) { a.x = p.x; a.y = p.y; a.path.shift(); } else { a.x += (dx / d) * budget; a.y += (dy / d) * budget; }
          budget -= step;
          a.anim += step / STEP_PX;
        }
        if (a.path && !a.path.length) {
          a.path = null;
          if (a.dest) this.settle(a, a.dest);
          if (a.errand && a.errand.stage === "go") {
            a.errand.stage = "wait"; a.errand.until = now + 2.5;
            a.bubble = { text: a.errand.text, ttl: 2.5 };
          } else {
            a.typing = a.want.typing && a.seat?.kind === "desk";
          }
        }
        continue;
      }
      if (a.errand && a.errand.stage === "wait" && now > a.errand.until) {
        a.errand = null;
        this.retarget(a);
        continue;
      }
      if (!a.errand && a.want.key === "idle" && now > a.idleUntil) {
        this.world.releaseAll(a.id);
        const fromCoffee = a.seat?.kind === "coffee";
        const s = this.idleSpot(a);
        a.idleUntil = now + 18 + hash2(a.idleSeed, Math.floor(now)) * 30;
        a.holding = fromCoffee ? "cup" : null;
        if (s) this.goTo(a, s);
      }
    }
  }

  /** Thời điểm bắt đầu việc đang chạy (giây), lấy từ dữ liệu thật. */
  static since(a: Agent): number | null { return a.want.typing && a.emp?.job?.since ? a.emp.job.since : null; }
  static tilePos(a: Agent) { return { tx: Math.floor(a.x / TILE), ty: Math.floor(a.y / TILE) }; }
}
