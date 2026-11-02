import { useEffect, useState } from "react";
import { api, eighths, Project } from "../../api";
import { useDrill } from "../../components/Drill";

const BADGE: Record<string, string> = { unchanged: "", changed: "warning", added: "ok", removed: "error", omitted: "error" };

/** Stage 3: compare a revised draft with the previous one. */
export function RevisionsPage({ project, versionId, onChanged }: { project: Project; versionId: number; onChanged: () => void }) {
  const versions = project.script_versions;
  const others = versions.filter((v) => v.id !== versionId);
  const [fromId, setFromId] = useState<number | null>(others.length ? others[others.length - 1].id : null);
  const [rep, setRep] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const drill = useDrill();

  useEffect(() => {
    setRep(null);
    api.get<any[]>(`/api/projects/${project.id}/revisions`).then((rs) => {
      const r = rs.find((x) => x.to_version_id === versionId);
      if (r) { setFromId(r.from_version_id); api.get(`/api/revisions/${r.id}`).then(setRep); }
    });
  }, [project.id, versionId]);

  const compare = async () => {
    if (!fromId) return;
    setBusy("Comparing…"); setErr("");
    try {
      const r = await api.post<any>("/api/revisions", { from_version_id: fromId, to_version_id: versionId });
      setRep(await api.get(`/api/revisions/${r.revision_id}`)); onChanged();
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  };
  const extract = async (what: "cast" | "departments", extractor: string) => {
    const ids = rep.scenes_needing_extraction;
    const url = what === "cast" ? `/api/script-versions/${versionId}/extract` : `/api/script-versions/${versionId}/departments/extract`;
    setBusy(`Extracting ${ids.length} changed/added scene(s)…`); setErr("");
    try {
      await api.post(url, { extractor, scene_ids: ids, workers: 4 });
      for (let i = 0; i < 600; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const st = await api.get<any>(url);
        if (st.job?.status !== "running") break;
      }
      setRep(await api.get(`/api/revisions/${rep.revision_id}`));
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  };
  const rematch = async (lineageId: number, oldSceneId: string) => {
    await api.post(`/api/lineage/${lineageId}/rematch`, { old_scene_id: oldSceneId ? Number(oldSceneId) : null });
    setRep(await api.get(`/api/revisions/${rep.revision_id}`));
  };

  if (!others.length) return <section className="panel"><p>Import a revised draft into this project (Import tab) to compare drafts. The newest import becomes the current draft.</p></section>;
  const removedOld = rep ? rep.scenes.filter((s: any) => s.status === "removed").map((s: any) => s.old) : [];
  return (
    <>
      <section className="panel">
        <h2>Compare drafts</h2>
        <div className="row">
          Previous draft <select value={fromId ?? ""} onChange={(e) => setFromId(Number(e.target.value))}>
            {others.map((v) => <option key={v.id} value={v.id}>v{v.id} — {v.label || v.file}</option>)}</select>
          → revised draft <b>v{versionId}</b>
          <button className="btn primary" onClick={compare} disabled={!!busy}>Compare</button>
          {busy && <span className="small">{busy}</span>}
        </div>
        <p className="small muted">Unchanged scenes keep their reviewed cast, locations, department items and story days (carried forward). Only changed and added scenes need extraction.</p>
        {err && <p className="error">{err}</p>}
      </section>
      {rep && (
        <>
          <section className="panel">
            <div className="stats">
              {["unchanged", "changed", "added", "omitted", "removed"].map((k) => (
                <div className="stat" key={k}><b>{rep.summary[k] ?? 0}</b><span>{k}</span></div>))}
              <div className="stat"><b>{rep.pages_delta_eighths >= 0 ? "+" : "−"}{eighths(Math.abs(rep.pages_delta_eighths))}</b><span>pages</span></div>
              <div className="stat"><b>{rep.needs_review}</b><span>matches to check</span></div>
            </div>
            {(rep.not_extracted > 0 || rep.departments_not_extracted > 0) && <div className="row" style={{ marginTop: 10 }}>
              <span className="badge warning">{rep.not_extracted} scene(s) without cast extraction · {rep.departments_not_extracted} without department extraction</span>
              <button className="btn" onClick={() => extract("cast", "claude")}>Extract cast (Claude)</button>
              <button className="btn" onClick={() => extract("cast", "heuristic")}>Extract cast (offline)</button>
              <button className="btn" onClick={() => extract("departments", "claude")}>Departments (Claude)</button>
              <button className="btn" onClick={() => extract("departments", "heuristic")}>Departments (offline)</button>
            </div>}
            {!rep.cast_compared && <p className="small" style={{ color: "var(--warn)" }}>Cast not compared: one draft has no cast extraction yet.</p>}
            {rep.departments_not_compared.length > 0 && <p className="small" style={{ color: "var(--warn)" }}>
              Not compared (only one draft has a breakdown for them): {rep.departments_not_compared.join(", ")}.</p>}
            <div className="grid2">
              <div>
                <h3>Cast (characters) and casting requirements</h3>
                <p className="small">Characters new to the script: {rep.characters_new_to_script.join(", ") || "none"}<br />
                  Characters no longer in the script: {rep.characters_dropped_from_script.join(", ") || "none"}<br />
                  Casting roles new: {rep.casting_roles_new.join(", ") || "none"} · dropped: {rep.casting_roles_dropped.join(", ") || "none"}</p>
                <h3>Departments</h3>
                {Object.entries(rep.departments).map(([d, v]: any) => (v.new_to_script.length || v.dropped_from_script.length) ? (
                  <p key={d} className="small"><b>{d}</b>: {v.new_to_script.map((x: string) => <span key={x} className="chip">+ {x}</span>)}
                    {v.dropped_from_script.map((x: string) => <span key={x} className="chip">− {x}</span>)}</p>) : null)}
              </div>
              <div>
                <h3>Scheduled scenes affected</h3>
                {rep.affected_schedule.length === 0 ? <p className="small muted">None (or no schedule on the previous draft).</p> :
                  rep.affected_schedule.map((a: any, i: number) => <p key={i} className="small"><span className={`badge ${BADGE[a.status]}`}>{a.status}</span> scene {a.scene} — shooting day {a.shooting_day}{a.date ? ` (${a.date})` : ""}, {a.schedule}</p>)}
                <p className="small muted">Copy the schedule onto this draft from the Schedule tab ("Carry schedule forward").</p>
              </div>
            </div>
          </section>
          <section className="panel">
            <div className="row"><h2 style={{ margin: 0, flex: 1 }}>Scenes</h2>
              <label className="small"><input type="checkbox" checked={showUnchanged} onChange={(e) => setShowUnchanged(e.target.checked)} /> show unchanged</label></div>
            <table>
              <thead><tr><th>Status</th><th>Scene (was → now)</th><th>What changed</th><th></th></tr></thead>
              <tbody>{rep.scenes.filter((s: any) => showUnchanged || s.status !== "unchanged").map((s: any) => (
                <tr key={s.lineage_id}>
                  <td><span className={`badge ${BADGE[s.status]}`}>{s.status}</span>{s.needs_review && <div><span className="badge warning" title={s.review_reason}>check match</span></div>}
                    <div className="small muted">{s.method}{s.similarity != null ? ` · ${Math.round(s.similarity * 100)}%` : ""}</div></td>
                  <td className="small">{s.old ? <button className="cellbtn" onClick={() => drill("Previous draft", [s.old.scene_id])}>{s.old.number ?? `#${s.old.seq}`}</button> : "—"} → {s.new ? <button className="cellbtn" onClick={() => drill("Revised draft", [s.new.scene_id])}>{s.new.number ?? `#${s.new.seq}`}</button> : "—"}
                    <div className="muted">{(s.new || s.old).heading}</div></td>
                  <td className="small">
                    {s.not_extracted && <span className="badge warning">cast not extracted yet</span>}
                    {s.departments_not_extracted.length > 0 && <div><span className="badge warning">not compared yet: {s.departments_not_extracted.join(", ")}</span></div>}
                    {s.heading_changes.map((h: string) => <div key={h}>{h}</div>)}
                    {s.location && <div>location: {s.location.from} → {s.location.to ?? "—"}</div>}
                    {s.length_delta_eighths ? <div>length {s.length_delta_eighths > 0 ? "+" : "−"}{eighths(Math.abs(s.length_delta_eighths))}</div> : null}
                    {s.cast_added.length > 0 && <div>cast + {s.cast_added.join(", ")}</div>}
                    {s.cast_removed.length > 0 && <div>cast − {s.cast_removed.join(", ")}</div>}
                    {s.presence_changed.map((p: string) => <div key={p}>{p}</div>)}
                    {s.casting_roles_added.length > 0 && <div>casting roles + {s.casting_roles_added.join(", ")}</div>}
                    {s.casting_roles_removed.length > 0 && <div>casting roles − {s.casting_roles_removed.join(", ")}</div>}
                    {Object.entries(s.departments).map(([d, v]: any) => <div key={d}><b>{d}</b>: {[...v.added.map((x: string) => "+" + x), ...v.removed.map((x: string) => "−" + x), ...v.quantity_changed].join(", ")}</div>)}
                    {open === s.lineage_id && s.text_diff && <pre className="script" style={{ maxHeight: 240 }}>{s.text_diff.join("\n")}</pre>}
                  </td>
                  <td className="small">
                    {s.text_diff && <button className="btn" onClick={() => setOpen(open === s.lineage_id ? null : s.lineage_id)}>text diff</button>}
                    {s.new && (s.needs_review || s.status === "added") && <div style={{ marginTop: 4 }}>
                      <select defaultValue="" onChange={(e) => rematch(s.lineage_id, e.target.value)}>
                        <option value="">re-match to…</option>
                        <option value="">(new scene — no match)</option>
                        {removedOld.map((o: any) => <option key={o.scene_id} value={o.scene_id}>old {o.number ?? `#${o.seq}`} {o.heading}</option>)}
                      </select></div>}
                  </td>
                </tr>))}</tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}
