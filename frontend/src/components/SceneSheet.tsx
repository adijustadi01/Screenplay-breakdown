import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

const LABELS: Record<string, string> = {
  vehicles: "Vehicles", props: "Props", costumes: "Costumes", makeup_hair: "Makeup & hair", background: "Background",
  animals: "Animals", action_effects: "Action & effects", sound_music: "Songs & sound", production: "Production",
};

/** Per-scene breakdown sheet: every department's items, correctable in place. */
export function SceneSheet({ sceneId }: { sceneId: number }) {
  const [sheet, setSheet] = useState<Record<string, any[]> | null>(null);
  const [add, setAdd] = useState({ department: "props", name: "", quantity: "" });
  const [err, setErr] = useState("");
  const load = useCallback(() => { api.get<Record<string, any[]>>(`/api/scenes/${sceneId}/sheet`).then(setSheet); }, [sceneId]);
  useEffect(load, [load]);
  const act = async (id: number, action: string, changes?: Record<string, unknown>) => {
    setErr("");
    try { await api.post(`/api/facts/breakdown_occurrences/${id}`, { action, changes }); load(); } catch (e) { setErr(String(e)); }
  };
  const addItem = async () => {
    if (!add.name.trim()) return;
    try {
      await api.post(`/api/scenes/${sceneId}/items`, { department: add.department, name: add.name,
        quantity: add.quantity ? Number(add.quantity) : null });
      setAdd({ ...add, name: "", quantity: "" }); load();
    } catch (e) { setErr(String(e)); }
  };
  if (!sheet) return null;
  const any = Object.values(sheet).some((v) => v.length);
  return (
    <div>
      <h3>Breakdown sheet</h3>
      {err && <p className="error">{err}</p>}
      {!any && <p className="muted small">No department items yet — run department extraction on the Import tab.</p>}
      {Object.entries(sheet).filter(([, v]) => v.length).map(([d, items]) => (
        <div key={d} style={{ marginBottom: 8 }}>
          <b className="small">{LABELS[d] ?? d}</b>
          <table><tbody>{items.map((it) => (
            <tr key={it.occurrence_id}>
              <td style={{ width: "40%" }}>{it.name}{it.inferred && <span className="badge warning"> inferred — review</span>}
                {it.needs_review && !it.inferred && <span className="badge warning"> review</span>}
                <div className="small muted">{it.category}{Object.entries(it.details || {}).filter(([k]) => k !== "unresolved_characters").map(([k, v]) => ` · ${k}: ${v}`).join("")}</div></td>
              <td className="small">{it.quantity_max ?? (it.quantity_raw ? <span className="badge warning">? “{it.quantity_raw}”</span> : "")}</td>
              <td className="small">{it.characters.map((c: any) => `${c.name} (${c.role})`).join(", ")}</td>
              <td className="row" style={{ gap: 4, justifyContent: "flex-end" }}>
                <button className="btn" onClick={() => act(it.occurrence_id, "confirm")}>✓</button>
                <button className="btn danger" onClick={() => act(it.occurrence_id, "reject")}>✕</button>
              </td>
            </tr>))}</tbody></table>
        </div>
      ))}
      <div className="row small">
        <select value={add.department} onChange={(e) => setAdd({ ...add, department: e.target.value })}>
          {Object.entries(LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <input type="text" placeholder="Add missed item" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} />
        <input type="text" size={4} placeholder="qty" value={add.quantity} onChange={(e) => setAdd({ ...add, quantity: e.target.value })} />
        <button className="btn" onClick={addItem}>Add</button>
      </div>
    </div>
  );
}
