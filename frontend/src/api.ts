// Thin typed wrapper over the FastAPI backend.
export type Cell = { count: number; scene_ids: number[] };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

export const api = {
  get: <T,>(p: string) => req<T>(p),
  post: <T,>(p: string, body?: unknown) =>
    req<T>(p, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
};

export type Version = { id: number; label: string | null; file: string; format: string; status: string; pages: number };
export type Project = { id: number; title: string; script_versions: Version[] };

export type SceneRef = {
  id: number; seq: number; number: string | null; heading: string | null; int_ext: string;
  time: string; time_effective: string; time_inferred: boolean; eighths: number; location: string; omitted: boolean;
};

export function eighths(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const p = Math.floor(n / 8), e = n % 8;
  if (p && e) return `${p} ${e}/8`;
  return p ? `${p}` : `${e}/8`;
}

export const sceneLabel = (s: { number: string | null; seq: number }) => s.number ?? `#${s.seq}`;
