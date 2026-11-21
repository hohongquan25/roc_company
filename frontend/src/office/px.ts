// Nền tảng pixel-art: ô 16px, hàm vẽ cơ bản và nhân vật (chuyển từ project my_company).
export const TILE = 16;
export type G = CanvasRenderingContext2D;
export type Dir = "down" | "up" | "left" | "right";
export type Pose = "stand" | "walk" | "sit";
export type Holding = "cup" | "paper" | null;

export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
export const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(bx - ax, by - ay);
export const tileOf = (px: number) => Math.floor(px / TILE);
/** Vị trí bàn chân của người đứng trên ô (tx, ty). */
export const standPos = (tx: number, ty: number) => ({ x: tx * TILE + 8, y: ty * TILE + 13 });

export function R(g: G, x: number, y: number, w: number, h: number, c: string) { g.fillStyle = c; g.fillRect(x, y, w, h); }

export function hash2(x: number, y: number, s = 0) {
  let h = (x * 374761393 + y * 668265263 + s * 1442695041) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
export function hashStr(s: string) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); }

function hexToRgb(hex: string) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
export function shade(hex: string, amt: number) {
  const [r, g, b] = hexToRgb(hex);
  const f = (v: number) => clamp(Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt), 0, 255);
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

export function makeCanvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return c;
}

export function roundRect(g: G, x: number, y: number, w: number, h: number, r: number) {
  r = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

export function wrapText(g: G, text: string, maxWidth: number, maxLines: number) {
  const words = text.split(" ");
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const test = line ? line + " " + word : word;
    if (g.measureText(test).width <= maxWidth || !line) line = test;
    else { lines.push(line); line = word; }
  }
  if (line) lines.push(line);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    let last = kept[maxLines - 1] + "…";
    while (g.measureText(last).width > maxWidth && last.length > 2) last = last.slice(0, -2) + "…";
    kept[maxLines - 1] = last;
    return kept;
  }
  return lines;
}

// --- vai trò & ngoại hình ---------------------------------------------------------------
export const ROLE_COLOR: Record<string, string> = {
  lead: "#ef4444", research: "#14b8a6", writer: "#8b5cf6", production: "#f97316",
  qa: "#3b82f6", exec_assistant: "#64748b", boss: "#263247",
};
export const CLI_COLOR: Record<string, string> = { claude: "#e8834d", antigravity: "#4d8ee8" };

export type Look = { skin: string; hair: string; hairStyle: string; shirt: string; pants: string; shoes: string; glasses: boolean; blush: boolean; suit?: boolean };
const SKIN_TONES = ["#f8d5b0", "#f1c194", "#e0a878", "#c68a5c", "#9a6440"];
const HAIR_COLORS = ["#2b1d14", "#3d2817", "#5a3a22", "#7a4a26", "#1c1c24", "#a8743c", "#8c3b2a"];
const HAIR_STYLES = ["short", "spiky", "long", "bob", "bun", "ponytail", "curly", "side"];
const PANTS_COLORS = ["#2f3b52", "#3a3a44", "#4a3c2e", "#28466b", "#55524c"];
const SHOE_COLORS = ["#2a2420", "#3b2f2a", "#1f2430", "#e6e6e6"];

/** Ngoại hình cố định theo mã nhân viên (cùng người luôn trông giống nhau). */
export function lookFor(id: string, role: string): Look {
  if (role === "boss") {
    return { skin: SKIN_TONES[1], hair: "#2f2f35", hairStyle: "side", shirt: "#263247", pants: "#263247", shoes: "#1a1a1a", glasses: true, blush: false, suit: true };
  }
  const h = hashStr(id);
  const at = <T,>(a: T[], k: number) => a[Math.floor(hash2(h, k) * a.length)];
  return { skin: at(SKIN_TONES, 1), hair: at(HAIR_COLORS, 2), hairStyle: at(HAIR_STYLES, 3), shirt: ROLE_COLOR[role] || "#64748b",
    pants: at(PANTS_COLORS, 4), shoes: at(SHOE_COLORS, 5), glasses: hash2(h, 6) < 0.25, blush: hash2(h, 7) < 0.5 };
}

// --- nhân vật pixel ------------------------------------------------------------------------
const SPRITE_W = 18, SPRITE_H = 26;
const OUTLINE = "rgba(28, 22, 36, 0.9)";
type RF = (x: number, y: number, w: number, h: number, c: string) => void;

function palette(l: Look) {
  return {
    skin: l.skin, skinD: shade(l.skin, -0.14), hair: l.hair, hairL: shade(l.hair, 0.22),
    shirt: l.shirt, shirtD: shade(l.shirt, -0.22), shirtL: shade(l.shirt, 0.25),
    pants: l.pants, pantsD: shade(l.pants, -0.25), shoes: l.shoes, shoesD: shade(l.shoes, -0.3),
    eye: "#2b2430", blush: "#f0a0a0",
  };
}
type Pal = ReturnType<typeof palette>;

function drawHair(Rf: RF, style: string, dir: string, p: Pal, dy: number) {
  const H = p.hair, L = p.hairL;
  const r = (x: number, y: number, w: number, h: number, c = H) => Rf(x, y + dy, w, h, c);
  if (dir === "down") {
    switch (style) {
      case "spiky": r(4, 1, 1, 1); r(6, 0, 1, 2); r(8, 0, 1, 2); r(10, 1, 2, 1); r(4, 2, 8, 3); r(3, 3, 1, 3); r(12, 3, 1, 3); r(4, 5, 1, 1); r(6, 5, 2, 1); r(10, 5, 2, 1); break;
      case "long": r(5, 1, 6, 1); r(4, 2, 8, 3); r(3, 3, 2, 11); r(11, 3, 2, 11); r(5, 5, 6, 1); break;
      case "bob": r(5, 1, 6, 1); r(4, 2, 8, 4); r(3, 3, 2, 8); r(11, 3, 2, 8); break;
      case "bun": r(6, -1, 4, 3, H); r(7, -1, 2, 1, L); r(4, 2, 8, 3); r(3, 3, 1, 5); r(12, 3, 1, 5); r(4, 5, 2, 1); r(10, 5, 2, 1); break;
      case "ponytail": r(5, 1, 6, 1); r(4, 2, 8, 3); r(3, 3, 1, 4); r(12, 3, 1, 4); r(4, 5, 3, 1); r(9, 5, 3, 1); r(13, 5, 1, 4); break;
      case "curly": r(4, 0, 8, 1); r(3, 1, 10, 4); r(2, 2, 1, 4); r(13, 2, 1, 4); r(3, 5, 1, 3); r(12, 5, 1, 3); r(4, 5, 1, 1); r(6, 5, 1, 1); r(9, 5, 1, 1); r(11, 5, 1, 1); r(5, 1, 1, 1, L); r(9, 2, 1, 1, L); r(11, 1, 1, 1, L); break;
      case "side": r(5, 1, 6, 1); r(4, 2, 8, 3); r(3, 3, 1, 3); r(12, 3, 1, 3); r(4, 5, 6, 1); r(4, 6, 2, 1); break;
      default: r(5, 1, 6, 1); r(4, 2, 8, 3); r(3, 3, 1, 4); r(12, 3, 1, 4); r(4, 5, 4, 1); r(11, 5, 1, 1);
    }
    r(6, 2, 3, 1, L);
  } else if (dir === "up") {
    r(5, 1, 6, 1); r(4, 2, 8, 9); r(3, 3, 1, 5); r(12, 3, 1, 5);
    switch (style) {
      case "spiky": r(4, 1, 1, 1); r(6, 0, 1, 2); r(8, 0, 1, 2); r(10, 1, 2, 1); break;
      case "long": r(3, 3, 10, 11); break;
      case "bob": r(3, 3, 10, 8); break;
      case "bun": r(6, -1, 4, 3); r(7, -1, 2, 1, L); break;
      case "ponytail": r(7, 10, 2, 1, p.hairL); r(7, 11, 2, 5); break;
      case "curly": r(3, 1, 10, 9); r(2, 2, 12, 6); break;
    }
    r(6, 3, 4, 1, L);
  } else {
    r(5, 1, 6, 1); r(4, 2, 8, 3); r(4, 5, 3, 4); r(10, 5, 2, 1);
    switch (style) {
      case "spiky": r(5, 0, 1, 2); r(7, 0, 1, 2); r(9, 1, 2, 1); break;
      case "long": r(3, 5, 4, 9); break;
      case "bob": r(3, 5, 4, 6); break;
      case "bun": r(2, 2, 3, 3); r(2, 2, 1, 1, L); break;
      case "ponytail": r(2, 5, 2, 6); r(3, 4, 1, 1, L); break;
      case "curly": r(3, 0, 9, 5); r(3, 5, 4, 5); break;
      case "side": r(9, 5, 3, 1); break;
    }
    r(7, 2, 3, 1, L);
  }
}

function drawTorso(Rf: RF, l: Look, p: Pal, dir: string, dy: number) {
  const x = dir === "right" ? 5 : 4, w = dir === "right" ? 6 : 8;
  Rf(x, 12 + dy, w, 6, p.shirt);
  Rf(x, 17 + dy, w, 1, p.shirtD);
  if (dir === "right") Rf(x, 12 + dy, 1, 6, p.shirtD);
  if (l.suit && dir === "down") { Rf(6, 12 + dy, 4, 3, "#f1f5f9"); Rf(7, 13 + dy, 2, 4, "#c0392b"); Rf(7, 13 + dy, 1, 1, "#e74c3c"); }
  else if (l.suit && dir === "right") Rf(10, 12 + dy, 1, 2, "#f1f5f9");
  else if (dir === "down") { Rf(6, 12 + dy, 4, 1, p.shirtL); Rf(7, 12 + dy, 2, 1, p.skinD); }
}

function drawHead(Rf: RF, l: Look, p: Pal, dir: string, dy: number) {
  if (dir === "right") {
    Rf(5, 4 + dy, 7, 7, p.skin); Rf(6, 11 + dy, 5, 1, p.skin); Rf(12, 8 + dy, 1, 2, p.skin); Rf(10, 8 + dy, 1, 2, p.eye);
    if (l.blush) Rf(9, 10 + dy, 1, 1, p.blush);
    Rf(7, 7 + dy, 1, 2, p.skinD);
    if (l.glasses) { Rf(9, 8 + dy, 3, 1, "#1e293b"); Rf(6, 8 + dy, 3, 1, "#1e293b"); }
  } else {
    Rf(4, 4 + dy, 8, 7, p.skin); Rf(5, 11 + dy, 6, 1, p.skin); Rf(3, 7 + dy, 1, 2, p.skin); Rf(12, 7 + dy, 1, 2, p.skin);
    if (dir === "down") {
      Rf(6, 8 + dy, 1, 2, p.eye); Rf(9, 8 + dy, 1, 2, p.eye);
      if (l.blush) { Rf(5, 10 + dy, 1, 1, p.blush); Rf(10, 10 + dy, 1, 1, p.blush); }
      Rf(7, 11 + dy, 2, 1, p.skinD);
      if (l.glasses) Rf(5, 8 + dy, 6, 1, "#1e293b");
    }
  }
}

function drawBody(Rf: RF, l: Look, dir: string, pose: Pose, frame: number, holding: Holding, tf: number) {
  const p = palette(l);
  const sit = pose === "sit", dy = sit ? 3 : 0, walk = pose === "walk";
  const f1 = walk && frame === 1, f3 = walk && frame === 3;
  if (sit) {
    if (dir === "down") { Rf(4, 21, 8, 2, p.pants); Rf(4, 22, 8, 1, p.pantsD); Rf(4, 23, 3, 1, p.shoes); Rf(9, 23, 3, 1, p.shoes); }
    else if (dir === "up") Rf(4, 21, 8, 2, p.pantsD);
    else { Rf(5, 21, 7, 2, p.pants); Rf(11, 23, 2, 1, p.shoes); }
  } else if (dir === "right") {
    Rf(5, 18, 6, 1, p.pantsD);
    if (f1 || f3) {
      const front = f1 ? p.pants : p.pantsD, back = f1 ? p.pantsD : p.pants;
      Rf(4, 19, 3, 3, back); Rf(3, 22, 3, 2, p.shoesD); Rf(8, 19, 3, 3, front); Rf(9, 22, 3, 2, p.shoes);
    } else { Rf(6, 19, 4, 3, p.pants); Rf(6, 22, 5, 2, p.shoes); }
  } else {
    Rf(4, 18, 8, 1, p.pantsD);
    const lUp = f1 ? 1 : 0, rUp = f3 ? 1 : 0;
    Rf(5, 19, 3, 3 - lUp, p.pants); Rf(5, 22 - lUp, 3, 2, p.shoes);
    Rf(8, 19, 3, 3 - rUp, p.pants); Rf(8, 22 - rUp, 3, 2, p.shoes);
    Rf(7, 19, 1, 3, p.pantsD);
  }
  drawTorso(Rf, l, p, dir, dy);
  if (dir === "right") {
    if (holding) { Rf(8, 12 + dy, 2, 3, p.shirtD); Rf(9, 15 + dy, 2, 1, p.skin); }
    else if (sit) { Rf(8, 13 + dy, 2, 3, p.shirtD); Rf(10, 16 + dy, 2, 1, p.skin); }
    else { const ax = f1 ? 8 : f3 ? 6 : 7; Rf(ax, 12, 2, 3, p.shirtD); Rf(ax + (f1 ? 1 : f3 ? -1 : 0), 15, 2, 2, p.skin); }
  } else if (sit) {
    const typing = !!tf;
    if (dir === "down") { Rf(3, 15, 1, 3, p.shirtD); Rf(12, 15, 1, 3, p.shirtD); Rf(4, 18 + (typing ? 1 : 0), 2, 1, p.skin); Rf(10, 18 + (typing ? 0 : 1), 2, 1, p.skin); }
    else { Rf(3, 15 + (typing ? 1 : 0), 1, 3, p.shirtD); Rf(12, 15 + (typing ? 0 : 1), 1, 3, p.shirtD); }
  } else {
    const lo = f1 ? 1 : f3 ? -1 : 0, ro = -lo;
    Rf(3, 12 + lo, 1, 3, p.shirtD); Rf(3, 15 + lo, 1, 2, p.skin);
    if (holding === "cup" && dir === "down") { Rf(12, 12, 1, 3, p.shirtD); Rf(12, 15, 1, 1, p.skin); }
    else { Rf(12, 12 + ro, 1, 3, p.shirtD); Rf(12, 15 + ro, 1, 2, p.skin); }
  }
  drawHead(Rf, l, p, dir, dy);
  drawHair(Rf, l.hairStyle, dir, p, dy);
  if (holding === "cup") {
    if (dir === "down") { Rf(12, 12 + dy, 2, 3, "#f8fafc"); Rf(12, 12 + dy, 2, 1, "#7c4a2d"); }
    else if (dir === "right") { Rf(11, 13 + dy, 2, 3, "#f8fafc"); Rf(11, 13 + dy, 2, 1, "#7c4a2d"); }
  } else if (holding === "paper") {
    if (dir === "down") { Rf(5, 14 + dy, 6, 4, "#ffffff"); Rf(6, 15 + dy, 4, 1, "#94a3b8"); Rf(6, 16 + dy, 3, 1, "#94a3b8"); }
    else if (dir === "right") Rf(10, 13 + dy, 3, 4, "#ffffff");
  }
}

function addOutline(c: HTMLCanvasElement) {
  const g = c.getContext("2d")!;
  const img = g.getImageData(0, 0, c.width, c.height);
  const d = img.data, w = c.width, h = c.height;
  const solid = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && d[(y * w + x) * 4 + 3] > 0;
  const mark: [number, number][] = [];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (!solid(x, y) && (solid(x - 1, y) || solid(x + 1, y) || solid(x, y - 1) || solid(x, y + 1))) mark.push([x, y]);
  }
  g.fillStyle = OUTLINE;
  for (const [x, y] of mark) g.fillRect(x, y, 1, 1);
}

function buildSprite(l: Look, dir: Dir, pose: Pose, frame: number, holding: Holding, tf: number) {
  const flip = dir === "left";
  const c = makeCanvas(SPRITE_W, SPRITE_H);
  const g = c.getContext("2d")!;
  drawBody((x, y, w, h, color) => { g.fillStyle = color; g.fillRect(x + 1, y + 1, w, h); }, l, flip ? "right" : dir, pose, frame, holding, tf);
  addOutline(c);
  if (!flip) return c;
  const m = makeCanvas(SPRITE_W, SPRITE_H);
  const mg = m.getContext("2d")!;
  mg.translate(SPRITE_W, 0); mg.scale(-1, 1); mg.drawImage(c, 0, 0);
  return m;
}

export class SpriteCache {
  private map = new Map<string, HTMLCanvasElement | string>();
  get(l: Look, dir: Dir, pose: Pose, frame: number, holding: Holding, tf: number): HTMLCanvasElement {
    const key = `${JSON.stringify(l)}|${dir}|${pose}|${frame}|${holding || ""}|${tf}`;
    let c = this.map.get(key) as HTMLCanvasElement | undefined;
    if (!c) { c = buildSprite(l, dir, pose, frame, holding, tf); this.map.set(key, c); }
    return c;
  }
  /** Ảnh chân dung phóng to cho giao diện (data URL). */
  avatar(l: Look, scale = 3): string {
    const key = "avatar|" + JSON.stringify(l) + scale;
    let url = this.map.get(key) as string | undefined;
    if (!url) {
      const src = this.get(l, "down", "stand", 0, null, 0);
      const c = makeCanvas(SPRITE_W * scale, 16 * scale);
      const g = c.getContext("2d")!;
      g.imageSmoothingEnabled = false;
      g.drawImage(src, 0, 0, SPRITE_W, 16, 0, 0, SPRITE_W * scale, 16 * scale);
      url = c.toDataURL();
      this.map.set(key, url);
    }
    return url;
  }
}
export const sprites = new SpriteCache();
