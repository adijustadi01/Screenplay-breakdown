import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { ScriptText } from "../../components/ScriptText";

const CHOICES: Record<string, string[]> = {
  presence: ["present", "off_screen", "voice_only", "mentioned", "uncertain"],
  speaking: ["speaking", "silent", "unknown"],
  int_ext: ["INT", "EXT", "INT/EXT", "UNKNOWN"],
  time_of_day: ["DAY", "NIGHT", "DAWN", "DUSK", "SUNRISE", "SUNSET", "MORNING", "AFTERNOON", "EVENING", "CONTINUOUS", "SAME", "LATER", "MOMENTS LATER", "UNSPECIFIED", "OTHER"],
  effective_time_of_day: ["DAY", "NIGHT", "DAWN", "DUSK", "MORNING", "AFTERNOON", "EVENING"],
  element_type: ["scene_heading", "action", "character", "parenthetical", "dialogue", "transition", "shot", "page_header", "unknown"],
};

/** The review queue: flagged and low-confidence items first, each beside its exact source passage. */
export function ReviewPage({ projectId, versionId, onChanged }: { projectId: number; versionId: number; onChanged: () => void }) {
  const [status, setStatus] = useState("open");
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");
  const [reason, setReason] = useState("");
  const [data, setData] = useState<any>({ items: [], facets: [], total: 0 });
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [checked, setChecked] = useState<Set<number>>(new Set());

  const load = useCallback(() => {
    const q = new URLSearchParams({ version_id: String(versionId), status });
    if (category) q.set("category", category);
    if (severity) q.set("severity", severity);
    if (reason) q.set("reason", reason);
    api.get<any>(`/api/projects/${projectId}/review?${q}`).then((d) => {
      setData(d); setChecked(new Set());
      setSel((cur) => cur ?? d.items[0]?.id ?? null);     // open the first item so the screen is never blank
    });
  }, [projectId, versionId, status, category, severity, reason]);
  useEffect(load, [load]);
  useEffect(() => {
    setChanges({}); setNote(""); setErr("");
    if (sel) api.get<any>(`/api/review/${sel}`).then(setDetail); else setDetail(null);
  }, [sel]);

  const act = async (action: string) => {
    setErr("");
    try {
      const body: any = { action, note: note || undefined };
      if (action === "edit") body.changes = changes;
      await api.post(`/api/review/${sel}`, body);
      const idx = data.items.findIndex((i: any) => i.id === sel);
      const next = data.items[idx + 1]?.id ?? null;
      load(); onChanged(); setSel(action === "undo" ? sel : next);
    } catch (e) { setErr(String(e)); }
  };
  const bulk = async (action: string) => {
    await api.post("/api/review-bulk", { item_ids: [...checked], action });
    load(); onChanged();
  };

  const reasons = [...new Set(data.facets.map((f: any) => f.reason))] as string[];
  return (
    <div className="queue">
      <section className="panel">
        <div className="row small" style={{ marginBottom: 8 }}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">open</option><option value="resolved">resolved</option><option value="dismissed">dismissed</option><option value="">all</option>
          </select>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">all categories</option><option value="import">import</option><option value="extraction">extraction</option><option value="reconciliation">merges</option>
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">all severities</option><option>error</option><option>warning</option><option>info</option>
          </select>
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">all reasons</option>{reasons.map((r) => <option key={r}>{r}</option>)}
          </select>
        </div>
        <div className="row small" style={{ marginBottom: 8 }}>
          <b style={{ flex: 1 }}>{data.total} item(s)</b>
          {checked.size > 0 && <>
            <button className="btn" onClick={() => bulk("confirm")}>Confirm {checked.size}</button>
            <button className="btn" onClick={() => bulk("accept")}>Accept merges</button>
            <button className="btn" onClick={() => bulk("dismiss")}>Dismiss</button></>}
        </div>
        <div className="scroll">
          {data.items.map((i: any) => (
            <div key={i.id} className={`qitem ${sel === i.id ? "sel" : ""}`} onClick={() => setSel(i.id)}>
              <div className="row" style={{ gap: 6 }}>
                <input type="checkbox" checked={checked.has(i.id)} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => { const s = new Set(checked); e.target.checked ? s.add(i.id) : s.delete(i.id); setChecked(s); }} />
                <span className={`badge ${i.severity}`}>{i.severity}</span>
                <span className="small muted">{i.category} · {i.reason}{i.scene_label ? ` · sc ${i.scene_label}` : ""}
                  {i.confidence !== null && ` · ${Math.round(i.confidence * 100)}%`}</span>
              </div>
              <div className="small" style={{ marginTop: 3 }}>{i.message.length > 160 ? i.message.slice(0, 160) + "…" : i.message}</div>
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        {!detail ? <p className="muted">Select an item. Errors and warnings are listed first, then the lowest-confidence items.</p> : (
          <>
            <div className="row"><span className={`badge ${detail.severity}`}>{detail.severity}</span><b>{detail.reason}</b>
              <span className="muted small">{detail.category} · {detail.status}{detail.resolution ? ` (${detail.resolution})` : ""}</span></div>
            <p>{detail.message}</p>
            {detail.entity && detail.entity_table === "breakdown_occurrences" && <p className="small muted">
              Item: <b>{detail.entity.item_name}</b> ({detail.entity.department}, {detail.entity.item_category}) — quantity {detail.entity.quantity_max ?? "unknown"}
              {detail.entity.is_inferred && <b> · INFERRED</b>}; extracted by {detail.entity.extracted_by}; evidence “{detail.entity.source_excerpt}”</p>}
            {detail.entity && detail.entity_table === "appearances" && <p className="small muted">
              Fact: <b>{detail.entity.character_name}</b> — {detail.entity.presence}, {detail.entity.speaking}; extracted by {detail.entity.extracted_by}; evidence “{detail.entity.source_excerpt}”</p>}
            {err && <p className="error">{err}</p>}
            {detail.status === "open" && (detail.has_proposal ? (
              <div className="row">
                <button className="btn primary" onClick={() => act("accept")}>Accept merge</button>
                <button className="btn" onClick={() => act("decline")}>Decline</button>
              </div>
            ) : (
              <>
                {detail.editable_fields.length > 0 && <div className="row small">
                  {detail.editable_fields.filter((f: string) => CHOICES[f] || ["quantity_min", "quantity_max", "quantity_raw", "description", "scene_number", "canonical_name", "name"].includes(f)).map((f: string) => (
                    <label key={f}>{f}{" "}
                      {CHOICES[f] ? (
                        <select value={changes[f] ?? detail.entity?.[f] ?? ""} onChange={(e) => setChanges({ ...changes, [f]: e.target.value })}>
                          <option value=""></option>{CHOICES[f].map((c) => <option key={c}>{c}</option>)}
                        </select>
                      ) : (
                        <input type="text" size={8} value={changes[f] ?? detail.entity?.[f] ?? ""} onChange={(e) => setChanges({ ...changes, [f]: e.target.value })} />
                      )}
                    </label>
                  ))}
                </div>}
                <div className="row" style={{ marginTop: 8 }}>
                  <input type="text" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
                  <button className="btn primary" onClick={() => act("confirm")}>Confirm</button>
                  <button className="btn" disabled={Object.keys(changes).length === 0} onClick={() => act("edit")}>Save edit</button>
                  {["appearances", "scene_locations", "characters", "locations", "scenes", "breakdown_occurrences", "breakdown_items"].includes(detail.entity_table) &&
                    <button className="btn danger" onClick={() => act("reject")}>Reject</button>}
                  <button className="btn" onClick={() => act("dismiss")}>Dismiss</button>
                </div>
              </>
            ))}
            {detail.can_undo && <button className="btn" onClick={() => act("undo")}>Undo this merge</button>}
            {detail.source && <><h3>Source passage</h3><ScriptText elements={detail.source.elements} /></>}
          </>
        )}
      </section>
    </div>
  );
}
