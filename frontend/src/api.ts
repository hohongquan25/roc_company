// Lớp gọi API. Mọi yêu cầu ghi gửi header X-Mini (backend từ chối nếu thiếu).

export class ApiError extends Error {}

async function handle(r: Response) {
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError((body && body.detail) || `Lỗi ${r.status}`);
  return body;
}

export const api = {
  get: (p: string) => fetch(p).then(handle),
  send: (method: string, p: string, body?: unknown) =>
    fetch(p, {
      method,
      headers: { "Content-Type": "application/json", "X-Mini": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(handle),
  post: (p: string, body?: unknown) => api.send("POST", p, body ?? {}),
  patch: (p: string, body: unknown) => api.send("PATCH", p, body),
  put: (p: string, body: unknown) => api.send("PUT", p, body),
  del: (p: string) => api.send("DELETE", p),
  upload: (p: string, form: FormData) =>
    fetch(p, { method: "POST", headers: { "X-Mini": "1" }, body: form }).then(handle),
};

export type Json = any; // dữ liệu động từ API

export const fmtTime = (ts?: number | null) =>
  ts ? new Date(ts * 1000).toLocaleString("vi-VN", { hour12: false }) : "—";

export const fmtIso = (iso?: string | null, tz?: string) =>
  iso ? new Date(iso).toLocaleString("vi-VN", { hour12: false, timeZone: tz || undefined }) : "—";
