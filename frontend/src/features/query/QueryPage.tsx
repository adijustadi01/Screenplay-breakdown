import { useEffect, useState } from "react";
import { api, eighths, sceneLabel } from "../../api";
import { CountCell } from "../../components/CountCell";
import { useDrill } from "../../components/Drill";
import { Matrix } from "../reports/ReportsPage";

type Opts = { characters: { id: number; name: string }[]; locations: { id: number; label: string; depth: number }[];
  int_ext: string[]; times_as_written: string[]; times_effective: string[];
  elements: Record<string, { categories: string[]; items: { id: number; name: string }[] }> };
type El = { department?: string; category?: string; item_ids?: number[]; text?: string; character_id?: number; role?: string };
const elLabel = (e: El, opts: Opts) => [
  e.department?.replace("_", " "),
  e.category,
  e.item_ids?.map((i) => opts.elements[e.department ?? ""]?.items.find((x) => x.id === i)?.name).join(" / "),
  e.text && `“${e.text}”`,
  e.character_id && `with ${opts.characters.find((c) => c.id === e.character_id)?.name}${e.role ? ` as ${e.role}` : ""}`,
].filter(Boolean).join(" · ");

/** Composable structured filters across the whole breakdown. */
export function QueryPage({ versionId }: { versionId: number }) {
  const [opts, setOpts] = useState<Opts | null>(null);
  const [q, setQ] = useState<any>({ location_ids: [], int_ext: [], times: [], time_mode: "as_written", character_ids: [],
    character_match: "all", include_off_screen: true, include_voice_only: false, rows: "none", columns: "none",
    elements: [], rows_department: null });
  const [draft, setDraft] = useState<El>({});
  const [res, setRes] = useState<any>(null);
  const drill = useDrill();
  useEffect(() => { api.get<Opts>(`/api/script-versions/${versionId}/query/options`).then(setOpts); setRes(null); }, [versionId]);
  useEffect(() => { api.post(`/api/script-versions/${versionId}/query`, q).then(setRes); }, [q, versionId]);
  if (!opts) return <p className="muted">Loading…</p>;

  const toggle = (key: string, v: any) => setQ({ ...q, [key]: q[key].includes(v) ? q[key].filter((x: any) => x !== v) : [...q[key], v] });
  const times = q.time_mode === "effective" ? opts.times_effective : opts.times_as_written;
  const name = (id: number) => opts.characters.find((c) => c.id === id)?.name;
  const loc = (id: number) => opts.locations.find((l) => l.id === id)?.label;

  return (
    <>
      <section className="panel">
        <h2>Query the breakdown</h2>
        <div className="grid2">
          <div>
            <h3>Location (includes its sub-locations)</h3>
            <select value="" onChange={(e) => e.target.value && toggle("location_ids", Number(e.target.value))}>
              <option value="">+ add location</option>
              {opts.locations.map((l) => <option key={l.id} value={l.id}>{"  ".repeat(l.depth)}{l.label}</option>)}
            </select>
            <div>{q.location_ids.map((id: number) => <span key={id} className="chip">{loc(id)}<button onClick={() => toggle("location_ids", id)}>×</button></span>)}</div>
            <h3>Characters</h3>
            <div className="row">
              <select value="" onChange={(e) => e.target.value && toggle("character_ids", Number(e.target.value))}>
                <option value="">+ add character</option>
                {opts.characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <select value={q.character_match} onChange={(e) => setQ({ ...q, character_match: e.target.value })}>
                <option value="all">all of them together</option><option value="any">any of them</option>
              </select>
            </div>
            <div>{q.character_ids.map((id: number) => <span key={id} className="chip">{name(id)}<button onClick={() => toggle("character_ids", id)}>×</button></span>)}</div>
            <div className="row small" style={{ marginTop: 6 }}>
              <label><input type="checkbox" checked={q.include_off_screen} onChange={(e) => setQ({ ...q, include_off_screen: e.target.checked })} /> count O.S.</label>
              <label><input type="checkbox" checked={q.include_voice_only} onChange={(e) => setQ({ ...q, include_voice_only: e.target.checked })} /> count V.O.</label>
            </div>
          </div>
          <div>
            <h3>Department elements (each must be in the scene)</h3>
            <div className="row small">
              <select value={draft.department ?? ""} onChange={(e) => setDraft({ department: e.target.value || undefined })}>
                <option value="">department…</option>
                {Object.keys(opts.elements).map((d) => <option key={d} value={d}>{d.replace("_", " ")}</option>)}
              </select>
              {draft.department && <>
                <select value={draft.category ?? ""} onChange={(e) => setDraft({ ...draft, category: e.target.value || undefined })}>
                  <option value="">any category</option>
                  {opts.elements[draft.department].categories.map((c) => <option key={c}>{c}</option>)}
                </select>
                <select value={draft.item_ids?.[0] ?? ""} onChange={(e) => setDraft({ ...draft, item_ids: e.target.value ? [Number(e.target.value)] : undefined })}>
                  <option value="">any item</option>
                  {opts.elements[draft.department].items.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                </select>
              </>}
              <input type="text" size={10} placeholder="text, e.g. rain" value={draft.text ?? ""} onChange={(e) => setDraft({ ...draft, text: e.target.value || undefined })} />
              <select value={draft.character_id ?? ""} onChange={(e) => setDraft({ ...draft, character_id: e.target.value ? Number(e.target.value) : undefined })}>
                <option value="">involving anyone</option>
                {opts.characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="btn" disabled={!draft.department && !draft.text} onClick={() => { setQ({ ...q, elements: [...q.elements, draft] }); setDraft({}); }}>+ add</button>
            </div>
            <div>{q.elements.map((e: El, i: number) => <span key={i} className="chip">{elLabel(e, opts)}<button onClick={() => setQ({ ...q, elements: q.elements.filter((_: El, j: number) => j !== i) })}>×</button></span>)}</div>
            <h3>INT / EXT</h3>
            <div className="row small">{opts.int_ext.map((v) => <label key={v}><input type="checkbox" checked={q.int_ext.includes(v)} onChange={() => toggle("int_ext", v)} /> {v}</label>)}</div>
            <h3>Time of day</h3>
            <select className="small" value={q.time_mode} onChange={(e) => setQ({ ...q, time_mode: e.target.value, times: [] })}>
              <option value="as_written">as written</option><option value="effective">with inherited lighting</option>
            </select>
            <div className="row small" style={{ marginTop: 6 }}>{times.map((v) => <label key={v}><input type="checkbox" checked={q.times.includes(v)} onChange={() => toggle("times", v)} /> {v}</label>)}</div>
            <h3>Count by</h3>
            <div className="row small">
              rows <select value={q.rows} onChange={(e) => setQ({ ...q, rows: e.target.value })}>
                {["none", "character", "location", "story_location", "int_ext", "time", "item"].map((x) => <option key={x}>{x}</option>)}</select>
              {q.rows === "item" && <select value={q.rows_department ?? ""} onChange={(e) => setQ({ ...q, rows_department: e.target.value || null })}>
                <option value="">all departments</option>{Object.keys(opts.elements).map((d) => <option key={d} value={d}>{d}</option>)}</select>}
              columns <select value={q.columns} onChange={(e) => setQ({ ...q, columns: e.target.value })}>
                {["none", "time", "int_ext"].map((x) => <option key={x}>{x}</option>)}</select>
            </div>
          </div>
        </div>
      </section>
      {res && (
        <section className="panel">
          <h2>{res.description}</h2>
          <p><CountCell cell={res.total} title={res.description} /> scene(s) · {eighths(res.eighths)} pages</p>
          {(q.rows !== "none" || q.columns !== "none") && res.table.rows.length > 0 &&
            <Matrix rows={res.table.rows} cols={res.table.columns} cells={res.table.cells} rowTotals={res.table.row_totals} colTotals={res.table.column_totals} />}
          <h3>Scenes</h3>
          <table>
            <thead><tr><th>Sc</th><th>Heading</th><th>Time</th><th>Location</th><th className="num">Len</th></tr></thead>
            <tbody>{res.scenes.map((s: any) => (
              <tr key={s.id}><td><button className="cellbtn" onClick={() => drill(`Scene ${sceneLabel(s)}`, [s.id])}>{sceneLabel(s)}</button></td>
                <td>{s.heading}</td><td>{s.time}{s.time_inferred && <span className="muted small"> → {s.time_effective}</span>}</td>
                <td>{s.location}</td><td className="num">{eighths(s.eighths)}</td></tr>))}</tbody>
          </table>
        </section>
      )}
    </>
  );
}
