import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, eighths, sceneLabel, SceneRef } from "../api";
import { SceneDetail } from "./SceneDetail";

type DrillFn = (title: string, sceneIds: number[]) => void;
const Ctx = createContext<DrillFn>(() => {});
export const useDrill = () => useContext(Ctx);

/** Drill-down drawer: every number in every report opens the scenes behind it. */
export function DrillProvider({ versionId, children }: { versionId: number | null; children: ReactNode }) {
  const [state, setState] = useState<{ title: string; ids: number[] } | null>(null);
  const [rows, setRows] = useState<SceneRef[]>([]);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    if (!state || !versionId || state.ids.length === 0) { setRows([]); return; }
    api.get<SceneRef[]>(`/api/script-versions/${versionId}/scene-list?ids=${state.ids.join(",")}`).then(setRows);
  }, [state, versionId]);

  return (
    <Ctx.Provider value={(title, ids) => { setOpen(null); setState({ title, ids }); }}>
      {children}
      {state && (
        <div className="drawer" role="dialog" aria-label={state.title}>
          <header>
            {open !== null && <button className="btn" onClick={() => setOpen(null)}>← back</button>}
            <b style={{ flex: 1 }}>{open === null ? `${state.title} — ${state.ids.length} scene(s)` : "Scene"}</b>
            <button className="btn" onClick={() => setState(null)}>Close</button>
          </header>
          <div className="body">
            {open === null ? (
              <table>
                <thead><tr><th>Sc</th><th>Heading</th><th>I/E</th><th>Time</th><th>Location</th><th className="num">Len</th></tr></thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} style={{ cursor: "pointer" }} onClick={() => setOpen(s.id)}>
                      <td><button className="cellbtn">{sceneLabel(s)}</button></td>
                      <td>{s.heading}</td><td>{s.int_ext}</td>
                      <td>{s.time}{s.time_inferred && <span className="muted small"> → {s.time_effective}</span>}</td>
                      <td>{s.location}</td><td className="num">{eighths(s.eighths)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <SceneDetail sceneId={open} />
            )}
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
