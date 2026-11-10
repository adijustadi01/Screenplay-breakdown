import { useCallback, useEffect, useState } from "react";
import { api, eighths } from "../api";
import { SceneSheet } from "./SceneSheet";
import { ScriptText, ScriptEl } from "./ScriptText";

type Appearance = {
  id: number; character_name: string; casting_role: string | null; presence: string; speaking: string;
  dialogue_block_count: number; confidence: number; needs_review: boolean; review_status: string;
  review_reason: string | null; source_element_start_id: number | null; source_excerpt: string | null;
  quantity_min: number | null; quantity_max: number | null; quantity_raw: string | null; extracted_by: string;
  casting_role_id: number | null; role_choices: { id: number; name: string; kind: string }[];
};
type Scene = {
  id: number; scene_number: string | null; seq: number; heading_raw: string | null; int_ext: string;
  time_of_day: string; effective_time_of_day: string | null; start_page: string | null; end_page: string | null;
  length_eighths: number | null; elements: ScriptEl[]; review_reason: string | null;
};
const PRESENCES = ["present", "off_screen", "voice_only", "mentioned", "uncertain"];

/** One scene: the exact script text beside its extracted cast and location, all correctable. */
export function SceneDetail({ sceneId }: { sceneId: number }) {
  const [scene, setScene] = useState<Scene | null>(null);
  const [bd, setBd] = useState<{ synopsis: string | null; appearances: Appearance[]; locations: any[] } | null>(null);
  const [hl, setHl] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    api.get<Scene>(`/api/scenes/${sceneId}`).then(setScene);
    api.get<any>(`/api/scenes/${sceneId}/breakdown`).then(setBd);
  }, [sceneId]);
  useEffect(load, [load]);

  const act = async (id: number, action: string, changes?: Record<string, unknown>) => {
    setErr("");
    try { await api.post(`/api/facts/appearances/${id}`, { action, changes }); load(); }
    catch (e) { setErr(String(e)); }
  };
  const add = async () => {
    if (!newName.trim()) return;
    try {
      await api.post(`/api/scenes/${sceneId}/appearances`, { new_character_name: newName, presence: "present", speaking: "silent" });
      setNewName(""); load();
    } catch (e) { setErr(String(e)); }
  };

  if (!scene || !bd) return <p className="muted">Loading…</p>;
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Scene {scene.scene_number ?? `#${scene.seq}`} <span className="muted small">pp {scene.start_page}–{scene.end_page} · {eighths(scene.length_eighths)}</span></h2>
      <p><b>{scene.int_ext}</b> · {scene.time_of_day}
        {scene.effective_time_of_day && <span className="muted"> (lighting inherited: {scene.effective_time_of_day})</span>}
        {" · "}{bd.locations.map((l) => l.location_path).join(", ") || <span className="muted">no location</span>}</p>
      {bd.synopsis && <p className="muted">{bd.synopsis}</p>}
      <h3>Cast in this scene</h3>
      {err && <p className="error">{err}</p>}
      <table>
        <thead><tr><th>Character</th><th>Presence</th><th>Speaking</th><th className="num">Dlg</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {bd.appearances.map((a) => (
            <tr key={a.id} className={a.review_status === "rejected" ? "rejected" : ""}
                onMouseEnter={() => setHl(a.source_element_start_id)} onMouseLeave={() => setHl(null)}>
              <td><b>{a.character_name}</b>
                {a.role_choices.length > 1 ? (
                  <div className="small">Played by:{" "}
                    <select value={a.casting_role_id ?? ""} title="Which actor (age) plays this character in this scene"
                            onChange={(e) => act(a.id, "edit", { casting_role_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">— not chosen yet —</option>
                      {a.role_choices.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </select></div>
                ) : a.casting_role && a.casting_role !== a.character_name && <div className="small muted">as {a.casting_role}</div>}
                {(a.quantity_raw || a.quantity_min !== null) && <div className="small muted">headcount: {a.quantity_min ?? "?"}{a.quantity_max !== a.quantity_min ? `–${a.quantity_max ?? "?"}` : ""} {a.quantity_raw && `(“${a.quantity_raw}”)`}</div>}</td>
              <td><select value={a.presence} onChange={(e) => act(a.id, "edit", { presence: e.target.value })}>
                {PRESENCES.map((p) => <option key={p}>{p}</option>)}</select></td>
              <td><select value={a.speaking} onChange={(e) => act(a.id, "edit", { speaking: e.target.value })}>
                {["speaking", "silent", "unknown"].map((p) => <option key={p}>{p}</option>)}</select></td>
              <td className="num">{a.dialogue_block_count}</td>
              <td>{a.needs_review ? <span className="badge warning" title={a.review_reason ?? ""}>review</span>
                : <span className={`badge ${a.review_status === "unreviewed" ? "" : "ok"}`}>{a.review_status}</span>}
                <div className="small muted">{a.extracted_by} · {Math.round(a.confidence * 100)}%</div></td>
              <td className="row" style={{ gap: 4 }}>
                <button className="btn" onClick={() => act(a.id, "confirm")}>✓</button>
                <button className="btn danger" onClick={() => act(a.id, "reject")} title="Reject: not in this scene">✕</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <input type="text" placeholder="Add a missed character (name)" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <button className="btn" onClick={add}>Add as present</button>
      </div>
      <SceneSheet sceneId={sceneId} />
      <h3>Script text</h3>
      <ScriptText elements={scene.elements} highlight={hl ? [hl] : []} />
    </div>
  );
}
