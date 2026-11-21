// Bản đồ ô vuông của văn phòng — gọn trong một khung, không cần cuộn:
//   hàng trên: phòng giám đốc · trợ lý + góc họp + tủ hồ sơ/lễ tân · phòng kỹ thuật · pantry
//   hành lang
//   hàng dưới: một văn phòng làm việc chung — khu sản xuất (bàn nhân viên) + góc nghỉ (biển tên văn phòng)
// Chỉ là dữ liệu + truy vấn; phần vẽ nằm ở furniture.ts / renderer.ts.
import { Dir, TILE, dist, hash2, standPos } from "./px";

export const MAP_W = 33;
export const MAP_H = 24;
export const MAX_PLAQUES = 1; // một biển tên văn phòng
const BAND_Y = 12;          // dải tường của hai phòng dưới
const ROOM_Y = 15;          // hàng sàn đầu tiên trong phòng dưới
const ROOM_W = 15;          // số cột sàn mỗi phòng
const ROOM_X = [1, 17];     // cột sàn đầu tiên của khu sản xuất / góc nghỉ (cùng một phòng)

export type Spot = {
  id: string; kind: string; x: number; y: number; dir: Dir; pose: "sit" | "stand";
  access: { tx: number; ty: number }; occupant: string | null; social?: boolean;
};
export type Furn = {
  type: string; tx: number; ty: number; w: number; h: number; x: number; y: number; pw: number; ph: number;
  sortY: number; solid: boolean; seat?: Spot; seats?: DeskSeat[]; color?: string; key?: string; team?: number;
};
export type DeskSeat = { id: string; seat: Spot; furn: Furn; side: "top" | "bottom"; role: string | null; team: number | null; decor: number };
export type Hot = { x: number; y: number; w: number; h: number; path: string; title: string; key: string };
export type Rug = { tx: number; ty: number; w: number; h: number; color: string; border: string };
export type Decor = { type: string; tx: number; w: number; y0: number; team?: number; label?: string };
/** Ô biển tên văn phòng chưa sẵn sàng (lúc khởi tạo). */
export type Slot = { tx: number; w: number; y0: number };
/** Vùng sàn của một phòng (nền để vẽ). */
export type Section = { kind: "staff" | "lounge"; x0: number; y0: number; w: number; h: number };
export type Door = { tx: number; y0: number; open: boolean };

// vị trí ngồi trong phòng sản xuất: cụm A, cụm B, bàn riêng trưởng nhóm
const SEAT_OF_ROLE: Record<string, string> = {
  research: "A0B", writer: "A1B", qa: "B0B", production: "B1B", lead: "L",
};
export const MACHINE_ROLE: Record<string, string> = { render: "production" };

export class World {
  w = MAP_W; h = MAP_H;
  floor: string[][] = [];
  blocked: boolean[][] = [];
  furniture: Furn[] = [];
  spots: Spot[] = [];
  desks: DeskSeat[] = [];
  rugs: Rug[] = [];
  decor: Decor[] = [];
  hots: Hot[] = [];
  sections: Section[] = [];
  slots: Slot[] = [];
  bands = [0];                                      // chỉ còn tường trên cùng (3 hàng)
  doors: Door[] = [];
  vwalls: { tx: number; ty: number; h: number }[] = [];
  private byId: Record<string, Spot> = {};

  /** officeIds: mã văn phòng có biển tên trên tường (tối đa MAX_PLAQUES). */
  constructor(public officeIds: number[]) {
    this.officeIds = officeIds.slice(0, MAX_PLAQUES);
    for (let y = 0; y < this.h; y++) {
      this.floor.push(new Array(MAP_W).fill("wood"));
      this.blocked.push(new Array(MAP_W).fill(false));
    }
    this.buildMain();
    // một phòng duy nhất: không còn dải tường ngăn giữa nửa trên và nửa dưới
    this.fillFloor(1, BAND_Y, MAP_W - 2, 3, "wood");
    this.buildStaff(ROOM_X[0]);
    this.buildLounge(ROOM_X[1]);
    // một văn phòng duy nhất: không vách ngăn giữa khu sản xuất và góc nghỉ
    this.fillFloor(16, ROOM_Y, 1, this.h - 1 - ROOM_Y, "wood");
    for (let x = 0; x < MAP_W; x++) this.setWall(x, this.h - 1);
    for (let y = 0; y < this.h; y++) { this.setWall(0, y); this.setWall(MAP_W - 1, y); }
  }

  // ---------- dựng ----------
  fillFloor(tx: number, ty: number, w: number, h: number, type: string) {
    for (let y = ty; y < ty + h; y++) for (let x = tx; x < tx + w; x++) this.floor[y][x] = type;
  }
  setWall(x: number, y: number) { this.floor[y][x] = "wall"; this.blocked[y][x] = true; }
  block(tx: number, ty: number, w = 1, h = 1) { for (let y = ty; y < ty + h; y++) for (let x = tx; x < tx + w; x++) this.blocked[y][x] = true; }

  add(type: string, tx: number, ty: number, w = 1, h = 1, opts: Partial<Furn> = {}): Furn {
    const f: Furn = { type, tx, ty, w, h, x: tx * TILE, y: ty * TILE, pw: w * TILE, ph: h * TILE, sortY: (ty + h) * TILE, solid: true, ...opts };
    if (f.solid) this.block(tx, ty, w, h);
    this.furniture.push(f);
    return f;
  }
  spot(id: string, kind: string, tx: number, ty: number, dir: Dir, opts: Partial<Spot> = {}): Spot {
    const p = standPos(tx, ty);
    const s: Spot = { id, kind, dir, x: p.x, y: p.y, pose: "stand", access: { tx, ty }, occupant: null, ...opts };
    this.spots.push(s);
    this.byId[id] = s;
    return s;
  }
  hot(tx: number, ty: number, w: number, h: number, path: string, title: string, key = path, padTop = 0) {
    this.hots.push({ x: tx * TILE, y: ty * TILE - padTop, w: w * TILE, h: h * TILE + padTop, path, title, key });
  }
  door(tx: number, open: boolean) {
    this.doors.push({ tx, y0: BAND_Y, open });
    for (const x of [tx, tx + 1]) for (let y = BAND_Y; y < BAND_Y + 3; y++) {
      this.floor[y][x] = "door";
      this.blocked[y][x] = !open;
    }
  }

  /** Cụm 2x2 bàn quay lưng vào nhau; (tx, ty) là góc trên-trái của hàng ghế trên. */
  cluster(name: string, tx: number, ty: number, team: number | null) {
    const deskTop = ty + 1;
    const c = this.add("deskCluster", tx, deskTop, 4, 2, { sortY: (deskTop + 2) * TILE + 3, seats: [] });
    this.block(tx, ty, 4, 1);
    this.block(tx, deskTop + 2, 4, 1);
    [tx + 1, tx + 3].forEach((cx, i) => {
      const top = this.spot(`${name}${i}T`, "desk", cx, ty, "down", { pose: "sit", x: cx * TILE, y: deskTop * TILE + 5, access: { tx: cx, ty: ty - 1 } });
      this.add("chairDown", cx - 1, ty, 2, 1, { solid: false, sortY: top.y - 1, seat: top, color: "#3b4252" });
      const bottom = this.spot(`${name}${i}B`, "desk", cx, deskTop + 2, "up", { pose: "sit", x: cx * TILE, y: (deskTop + 2) * TILE + 10, access: { tx: cx, ty: deskTop + 3 } });
      this.add("chairUp", cx - 1, deskTop + 2, 2, 1, { solid: false, sortY: bottom.y + 1, seat: bottom, color: "#3b4252" });
      for (const seat of [top, bottom]) {
        const d: DeskSeat = { id: seat.id, seat, furn: c, side: seat === top ? "top" : "bottom", role: null, team, decor: Math.floor(hash2(cx, ty, i) * 4) };
        c.seats!.push(d);
        this.desks.push(d);
      }
    });
    return c;
  }

  /** Bàn đơn, người ngồi quay mặt xuống (thấy lưng màn hình). (tx, ty) là ô ghế bên trái. */
  soloDesk(id: string, tx: number, ty: number, team: number | null, role: string) {
    const deskTop = ty + 1;
    const f = this.add("deskSolo", tx, deskTop, 2, 2, { sortY: (deskTop + 2) * TILE + 3, seats: [] });
    this.block(tx, ty, 2, 1);
    const cx = tx + 1;
    const seat = this.spot(id, "desk", cx, ty, "down", { pose: "sit", x: cx * TILE, y: deskTop * TILE + 5, access: { tx: cx, ty: ty - 1 } });
    this.add("chairDown", cx - 1, ty, 2, 1, { solid: false, sortY: seat.y - 1, seat, color: "#3b4252" });
    const d: DeskSeat = { id, seat, furn: f, side: "top", role, team, decor: 1 };
    f.seats!.push(d);
    this.desks.push(d);
    return d;
  }

  buildMain() {
    for (let x = 0; x < MAP_W; x++) for (let y = 0; y < 3; y++) this.setWall(x, y);
    this.fillFloor(1, 3, 8, 7, "darkwood");
    this.fillFloor(13, 3, 6, 6, "carpetBlue");
    this.fillFloor(22, 3, 5, 3, "server");
    this.fillFloor(27, 3, 5, 8, "tile");

    // phòng giám đốc (bạn)
    this.add("glassH", 1, 10, 8, 1, { sortY: 11 * TILE - 1 });
    for (const ty of [3, 4, 5, 8, 9, 10]) this.add("glassV", 9, ty, 1, 1, { sortY: (ty + 1) * TILE - 1 });
    this.add("doorFrame", 9, 6, 1, 2, { solid: false, sortY: 6 * TILE });
    this.add("bookshelf", 1, 3, 2, 1);
    this.hot(1, 3, 2, 1, "/memory", "Tủ quy tắc công ty — bộ nhớ cấp công ty", "/memory", 20);
    this.add("trophyCabinet", 6, 3, 2, 1);
    this.add("plantBig", 8, 3);
    this.add("plantSmall", 1, 9);
    this.rugs.push({ tx: 2, ty: 4, w: 5, h: 5, color: "#7d2f3a", border: "#c9a24b" });
    this.add("directorDesk", 3, 5, 3, 2, { sortY: 7 * TILE + 2 });
    this.hot(3, 5, 3, 2, "/approvals", "Bàn giám đốc — khay hồ sơ chờ bạn duyệt", "/approvals", 4);
    const chair = this.spot("boss", "boss", 4, 4, "down", { pose: "sit", x: 4 * TILE + 8, y: 5 * TILE + 5 });
    this.add("directorChair", 4, 4, 1, 1, { solid: false, sortY: chair.y - 1, seat: chair });
    for (const tx of [3, 4, 5]) this.spot("visit" + tx, "approval", tx, 7, "up");
    this.spot("waitD1", "approval", 7, 8, "left");
    this.spot("waitD2", "approval", 6, 9, "up");
    this.spot("waitD3", "approval", 2, 8, "right");
    this.spot("waitD4", "approval", 10, 7, "left");
    this.spot("waitD5", "approval", 10, 8, "left");
    this.spot("waitD6", "approval", 11, 8, "left");

    // trợ lý điều hành + tủ hồ sơ (xưởng video)
    this.soloDesk("exec", 10, 4, null, "exec_assistant");
    this.add("printer", 10, 9);
    this.add("fileCabinet", 11, 9, 2, 1);
    this.hot(10, 9, 3, 1, "/requests", "Tủ hồ sơ — các yêu cầu nhạc & báo cáo từng bước", "/requests", 10);
    this.spot("window10", "window", 10, 3, "up");

    // góc họp: báo cáo & ý tưởng
    this.add("meetingTable", 14, 5, 4, 2);
    this.hot(14, 5, 4, 2, "/reports", "Góc họp — báo cáo & nhật ký", "/reports", 4);
    for (let i = 0; i < 4; i++) {
      const tx = 14 + i;
      const n = this.spot("meetN" + i, "meeting", tx, 4, "down", { pose: "sit", x: tx * TILE + 8, y: 5 * TILE + 5, access: { tx, ty: 3 } });
      this.add("chairDown", tx, 4, 1, 1, { sortY: n.y - 1, seat: n, color: "#4b5d7a" });
      const s = this.spot("meetS" + i, "meeting", tx, 7, "up", { pose: "sit", x: tx * TILE + 8, y: 7 * TILE + 9, access: { tx, ty: 8 } });
      this.add("chairUp", tx, 7, 1, 1, { sortY: s.y + 1, seat: s, color: "#4b5d7a" });
    }
    this.add("whiteboardStand", 13, 5, 1, 2);
    this.hot(13, 5, 1, 2, "/requests?new=1", "Bảng trắng — gửi yêu cầu nhạc mới (link bài mẫu + thể loại)", "/requests-new", 10);
    this.spot("board14", "board", 14, 3, "up");

    // quầy lễ tân cạnh hành lang
    this.add("reception", 17, 9, 4, 1);
    this.hot(17, 9, 4, 1, "/requests?new=1", "Quầy lễ tân — gửi yêu cầu nhạc mới", "/reception", 12);
    this.add("trashBin", 21, 9);

    // phòng kỹ thuật: vách kính, cửa bên trái
    this.add("racks", 22, 3, 5, 1, { key: "racks" });
    this.add("glassH", 22, 6, 5, 1, { sortY: 7 * TILE - 1 });
    for (const ty of [3, 6]) this.add("glassV", 21, ty, 1, 1, { sortY: (ty + 1) * TILE - 1 });
    this.add("doorFrame", 21, 4, 1, 2, { solid: false, sortY: 4 * TILE });
    this.hot(22, 3, 5, 3, "/settings", "Phòng kỹ thuật — kết nối Claude Code, Antigravity CLI, FFmpeg, yt-dlp, Worker", "/settings", 20);
    this.add("plantBig", 26, 7);

    // pantry nhỏ: nước, cà phê, tủ lạnh, bàn đứng, sofa
    this.add("waterCooler", 27, 3);
    this.spot("water", "water", 27, 4, "up");
    this.add("counter", 28, 3, 3, 1);
    this.spot("coffee1", "coffee", 28, 4, "up");
    this.spot("coffee2", "coffee", 29, 4, "up");
    this.spot("sink", "sink", 30, 4, "up");
    this.add("fridge", 31, 3);
    this.spot("fridge", "snack", 31, 4, "up");
    this.add("vending", 31, 6);
    this.spot("vending", "snack", 30, 6, "right");
    this.add("pantryTable", 28, 7, 2, 1);
    for (const tx of [28, 29]) {
      const n = this.spot("stoolN" + tx, "pantrySeat", tx, 6, "down", { pose: "sit", x: tx * TILE + 8, y: 7 * TILE + 4, social: true });
      this.add("stool", tx, 6, 1, 1, { solid: false, sortY: n.y - 6 });
      const s = this.spot("stoolS" + tx, "pantrySeat", tx, 8, "up", { pose: "sit", x: tx * TILE + 8, y: 8 * TILE + 10, social: true });
      this.add("stool", tx, 8, 1, 1, { solid: false, sortY: s.y - 1 });
    }
    const sofa = this.add("sofaUp", 28, 10, 3, 1, { sortY: 10 * TILE + 10 });
    for (let i = 0; i < 3; i++) {
      const tx = 28 + i;
      this.spot("sofa" + i, "sofa", tx, 10, "up", { pose: "sit", x: tx * TILE + 8, y: 10 * TILE + 8, access: { tx, ty: 9 }, social: true });
    }
    sofa.sortY = this.get("sofa0").y + 1;
    this.add("plantSmall", 27, 10);
    this.add("plantSmall", 31, 10);


    this.hots.push({ x: 10 * TILE, y: 8, w: 3 * TILE, h: 38, path: "/requests", title: "Biển ROC — các yêu cầu nhạc của văn phòng", key: "office" });
    this.decor.push(
      { type: "window", tx: 4, w: 2, y0: 0 }, { type: "certificate", tx: 7, w: 1, y0: 0 },
      { type: "logo", tx: 10, w: 3, y0: 0, team: this.officeIds[0] }, { type: "board", tx: 14, w: 3, y0: 0 },
      { type: "calendar", tx: 17, w: 1, y0: 0 }, { type: "window", tx: 18, w: 2, y0: 0 },
      { type: "clock", tx: 24, w: 1, y0: 0 }, { type: "coffeeSign", tx: 27, w: 1, y0: 0 },
      { type: "cabinets", tx: 28, w: 3, y0: 0 },
    );
    this.hots.push({ x: 14 * TILE, y: 8, w: 3 * TILE, h: 38, path: "/jobs", title: "Bảng công việc — số việc thật theo trạng thái", key: "/jobs" });
    this.hots.push({ x: 17 * TILE, y: 8, w: TILE, h: 38, path: "/reports", title: "Lịch — báo cáo & nhật ký", key: "/reports" });
  }

  /** Phòng sản xuất rộng 15 cột: máy móc dùng chung sát tường, 2 cụm bàn + bàn trưởng nhóm cho 7 nhân viên cố định. */
  buildStaff(r: number) {
    const Y = ROOM_Y;
    this.sections.push({ kind: "staff", x0: r, y0: Y, w: ROOM_W, h: this.h - 1 - Y });
    this.fillFloor(r, Y, 11, 3, "studio");

    // máy móc dọc tường (hàng Y+1), lối đi ở hàng Y và Y+2
    this.add("renderRig", r, Y + 1, 3, 1, { key: "render" });
    this.hot(r, Y + 1, 3, 1, "/requests", "Máy dựng — chuẩn hóa âm thanh & dựng video nhạc", "render", 18);
    this.spot("op-render", "machine", r + 1, Y + 2, "up");
    this.add("fileCabinet", r + 4, Y + 1, 2, 1);
    this.hot(r + 4, Y + 1, 2, 1, "/requests?tab=done", "Tủ video thành phẩm — các yêu cầu đã hoàn thành", "finished", 18);
    this.add("bookshelf", r + 7, Y + 1, 2, 1);
    this.hot(r + 7, Y + 1, 2, 1, "/memory", "Tủ sách — bộ nhớ: quy tắc công ty, gu âm nhạc & điều cần tránh", "mem", 20);
    this.add("plantSmall", r + 10, Y + 1);

    // bàn làm việc của nhân viên cố định (dùng chung cả văn phòng)
    this.cluster("sA", r, Y + 3, null);
    this.cluster("sB", r + 5, Y + 3, null);
    this.soloDesk("sL", r + 11, Y + 3, null, "lead");
    for (const d of this.desks) {
      if (d.role || !d.id.startsWith("s")) continue;
      const code = d.id.slice(1);
      const role = Object.keys(SEAT_OF_ROLE).find((x) => SEAT_OF_ROLE[x] === code);
      if (role) d.role = role;
    }
    this.add("plantSmall", r + 14, Y + 1);
    this.add("plantBig", r + 14, this.h - 2);
    this.add("fileCabinet", r + 10, this.h - 2, 2, 1);

  }

  /** Góc nghỉ trong cùng văn phòng: chỗ nghỉ của nhân viên. */
  buildLounge(r: number) {
    const Y = ROOM_Y, h = this.h - 1 - Y;
    this.sections.push({ kind: "lounge", x0: r, y0: Y, w: ROOM_W, h });
    // cùng một văn phòng: sàn gỗ liền, chỉ trải thảm cho khu nghỉ (không biển tên riêng)
    this.fillFloor(r, Y, ROOM_W, h, "wood");
    this.fillFloor(r + 1, Y + 1, ROOM_W - 2, h - 2, "carpetGreen");

    // góc nghỉ: sofa + bàn trà, máy game, bàn đứng, tủ sách
    this.add("coffeeTable", r + 2, Y + 3, 3, 1);
    const sofa = this.add("sofaUp", r + 2, Y + 5, 3, 1);
    for (let i = 0; i < 3; i++) {
      const tx = r + 2 + i;
      this.spot("sofaB" + i, "sofa", tx, Y + 5, "up", { pose: "sit", x: tx * TILE + 8, y: (Y + 5) * TILE + 8, access: { tx, ty: Y + 4 }, social: true });
    }
    sofa.sortY = this.get("sofaB0").y + 1;
    this.add("arcade", r + 6, Y + 1);
    this.spot("arcade", "arcade", r + 6, Y + 2, "up");
    this.add("pantryTable", r + 9, Y + 4, 2, 1);
    for (const tx of [r + 9, r + 10]) {
      const n = this.spot("stoolN" + tx, "pantrySeat", tx, Y + 3, "down", { pose: "sit", x: tx * TILE + 8, y: (Y + 4) * TILE + 4, social: true });
      this.add("stool", tx, Y + 3, 1, 1, { solid: false, sortY: n.y - 6 });
      const s = this.spot("stoolS" + tx, "pantrySeat", tx, Y + 5, "up", { pose: "sit", x: tx * TILE + 8, y: (Y + 5) * TILE + 10, social: true });
      this.add("stool", tx, Y + 5, 1, 1, { solid: false, sortY: s.y - 1 });
    }
    this.add("bookshelf", r + 10, this.h - 2, 2, 1);
    this.spot("bookC", "book", r + 11, this.h - 3, "down");
    this.add("plantBig", r + 14, Y + 1);
    this.add("plantSmall", r + 14, this.h - 2);
    this.add("plantSmall", r + 5, this.h - 2);
  }

  // ---------- truy vấn ----------
  inBounds(tx: number, ty: number) { return tx >= 0 && ty >= 0 && tx < this.w && ty < this.h; }
  isWalkable(tx: number, ty: number) { return this.inBounds(tx, ty) && !this.blocked[ty][tx]; }
  get(id: string) { return this.byId[id]; }
  deskOf(team: number | null, role: string) { return this.desks.find((d) => d.team === team && d.role === role); }

  zoneAt(tx: number, ty: number) {
    const f = this.inBounds(tx, ty) ? this.floor[ty][tx] : "wall";
    return ({ darkwood: "director", carpetBlue: "meeting", tile: "pantry", carpetGreen: "lounge" } as Record<string, string>)[f] || (f === "wall" ? "wall" : "work");
  }

  free(kind: string) { return this.spots.filter((s) => s.kind === kind && !s.occupant); }
  reserve(s: Spot | undefined, who: string) {
    if (!s || (s.occupant && s.occupant !== who)) return false;
    s.occupant = who;
    return true;
  }
  releaseAll(who: string, keep?: Spot | null) { for (const s of this.spots) if (s.occupant === who && s !== keep) s.occupant = null; }
  /** Giữ chỗ trống gần nhất thuộc một trong các loại. */
  claim(kinds: string[], who: string, near?: { x: number; y: number }, seed = 0): Spot | null {
    const free = this.spots.filter((s) => kinds.includes(s.kind) && (!s.occupant || s.occupant === who));
    if (!free.length) return null;
    const s = near ? free.sort((a, b) => dist(a.x, a.y, near.x, near.y) - dist(b.x, b.y, near.x, near.y))[0]
      : free[Math.floor(hash2(seed, free.length) * free.length)];
    this.reserve(s, who);
    return s;
  }

  randomTile(zones: string[], seed: number) {
    for (let i = 0; i < 200; i++) {
      const tx = 1 + Math.floor(hash2(seed, i, 1) * (MAP_W - 2)), ty = 3 + Math.floor(hash2(seed, i, 2) * 8);
      if (this.isWalkable(tx, ty) && zones.includes(this.zoneAt(tx, ty))) return { tx, ty };
    }
    return { tx: 28, ty: 9 };
  }

  nearestWalkable(tx: number, ty: number) {
    if (this.isWalkable(tx, ty)) return { tx, ty };
    for (let r = 1; r < 6; r++) for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) === r && this.isWalkable(tx + dx, ty + dy)) return { tx: tx + dx, ty: ty + dy };
    }
    return { tx: 16, ty: 11 };
  }

  /** Ô trống cạnh bàn của ai đó (để mang tài liệu bàn giao). */
  besideDesk(d: DeskSeat) {
    const a = d.seat.access;
    const opts = [{ tx: a.tx - 1, ty: a.ty }, { tx: a.tx + 1, ty: a.ty }, a];
    return opts.find((t) => this.isWalkable(t.tx, t.ty)) || this.nearestWalkable(a.tx, a.ty);
  }
}

// --- tìm đường A* 8 hướng, không cắt góc ---------------------------------------------------
const DIRS8: [number, number, number][] = [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2]];

export function findPath(w: World, start: { tx: number; ty: number }, goal: { tx: number; ty: number }) {
  if (start.tx === goal.tx && start.ty === goal.ty) return [];
  if (!w.isWalkable(goal.tx, goal.ty)) return null;
  const W = w.w, key = (x: number, y: number) => y * W + x;
  const h = (x: number, y: number) => { const dx = Math.abs(x - goal.tx), dy = Math.abs(y - goal.ty); return Math.max(dx, dy) + (Math.SQRT2 - 1) * Math.min(dx, dy); };
  const g = new Map([[key(start.tx, start.ty), 0]]);
  const came = new Map<number, number>();
  const closed = new Set<number>();
  const open: { k: number; f: number }[] = [{ k: key(start.tx, start.ty), f: h(start.tx, start.ty) }];
  const push = (k: number, f: number) => {
    open.push({ k, f });
    let i = open.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (open[p].f <= open[i].f) break; [open[p], open[i]] = [open[i], open[p]]; i = p; }
  };
  const pop = () => {
    const top = open[0], last = open.pop()!;
    if (open.length) {
      open[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1, r = l + 1;
        let m = i;
        if (l < open.length && open[l].f < open[m].f) m = l;
        if (r < open.length && open[r].f < open[m].f) m = r;
        if (m === i) break;
        [open[m], open[i]] = [open[i], open[m]];
        i = m;
      }
    }
    return top.k;
  };
  while (open.length) {
    const k = pop();
    if (closed.has(k)) continue;
    closed.add(k);
    const x = k % W, y = (k - x) / W;
    if (x === goal.tx && y === goal.ty) {
      const path: { tx: number; ty: number }[] = [];
      let c = k;
      while (c !== key(start.tx, start.ty)) { const cx = c % W; path.push({ tx: cx, ty: (c - cx) / W }); c = came.get(c)!; }
      return path.reverse();
    }
    for (const [dx, dy, cost] of DIRS8) {
      const nx = x + dx, ny = y + dy;
      if (!w.isWalkable(nx, ny)) continue;
      if (dx && dy && (!w.isWalkable(x + dx, y) || !w.isWalkable(x, y + dy))) continue;
      const nk = key(nx, ny);
      if (closed.has(nk)) continue;
      const ng = g.get(k)! + cost;
      if (ng < (g.get(nk) ?? Infinity)) { g.set(nk, ng); came.set(nk, k); push(nk, ng + h(nx, ny)); }
    }
  }
  return null;
}
