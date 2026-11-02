import { useEffect, useState } from "react";
import { api, eighths } from "../../api";
import { SceneDetail } from "../../components/SceneDetail";

/** All scenes with their parsed heading facts; click one to see its text and cast. */
export function ScenesPage({ versionId }: { versionId: number }) {
  const [scenes, setScenes] = useState<any[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { api.get<any[]>(`/api/script-versions/${versionId}/scenes`).then(setScenes); setSel(null); }, [versionId]);
  const shown = scenes.filter((s) => !filter || `${s.scene_number} ${s.heading_raw} ${s.synopsis ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="queue">
      <section className="panel">
        <div className="row" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0, flex: 1 }}>Scenes ({scenes.length})</h2>
          <input type="text" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="scroll">
          <table>
            <thead><tr><th>Sc</th><th>Heading</th><th>Time</th><th className="num">Len</th></tr></thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.id} onClick={() => setSel(s.id)} style={{ cursor: "pointer", background: sel === s.id ? "var(--accent-soft)" : undefined }}>
                  <td>{s.scene_number ?? <span className="badge warning">#{s.seq}</span>}</td>
                  <td>{s.is_omitted ? <span className="muted">OMITTED</span> : s.heading ?? <span className="badge error">no heading</span>}
                    {s.needs_review && <span className="badge warning" title={s.review_reason}> flag</span>}</td>
                  <td className="small">{s.time_of_day}{s.effective_time_of_day && <span className="muted"> → {s.effective_time_of_day}</span>}</td>
                  <td className="num">{eighths(s.length_eighths)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">{sel ? <SceneDetail sceneId={sel} /> : <p className="muted">Select a scene.</p>}</section>
    </div>
  );
}
