import { useEffect, useState } from "react";
import { api, Cell } from "../../api";
import { CountCell } from "../../components/CountCell";
import { useDrill } from "../../components/Drill";

type Dept = { key: string; label: string; item_noun: string };
const EXTRA_COLS: Record<string, [string, string][]> = {
  vehicles: [["drivers", "Drivers"], ["actions", "Actions"], ["condition", "Condition"]],
  props: [["users", "Used by"], ["hero_prop", "Hero"], ["consumable", "Consumable"]],
  costumes: [["wearers", "Worn by"], ["states", "States"], ["needs_multiples", "Multiples"]],
  makeup_hair: [["wearers", "On"], ["states", "States (in order)"], ["special", "Special"]],
  background: [["wardrobe", "Wardrobe"], ["featured", "Featured"]],
  animals: [["actions", "Action"], ["trained_behaviour", "Trained"], ["handler_assessment_required", "Handler"]],
  action_effects: [["performers", "Performers"], ["practical", "Practical"], ["vfx_candidate", "VFX?"], ["undecided", "Undecided"], ["stunt_double_needed", "Double"]],
  sound_music: [["song_title", "Title"], ["playback", "Playback"], ["lip_sync", "Lip-sync"], ["performers", "Performers"]],
  production: [["inferred_only", "Inferred only"], ["reasons", "Why"]],
};
const show = (v: unknown) => Array.isArray(v) ? v.join(", ") : v === true ? "yes" : v === false ? "" : v == null ? "" : String(v);

/** Stage 2: per-department breakdowns, all computed in code; every number opens its scenes. */
export function DepartmentsPage({ versionId }: { versionId: number }) {
  const [depts, setDepts] = useState<Dept[]>([]);
  const [tab, setTab] = useState("vehicles");
  const [loaded, setLoaded] = useState<{ key: string; data: any } | null>(null);
  const key = `${versionId}|${tab}`;
  useEffect(() => { api.get<Dept[]>("/api/departments").then(setDepts); }, []);
  useEffect(() => {
    let live = true;
    api.get(`/api/script-versions/${versionId}/departments/${tab}/report`).then((d) => live && setLoaded({ key, data: d }));
    return () => { live = false; };
  }, [versionId, tab, key]);
  const r = loaded?.key === key ? loaded.data : null;
  return (
    <section className="panel">
      <nav className="tabs" style={{ marginBottom: 12 }}>
        {depts.map((d) => <button key={d.key} className={tab === d.key ? "active" : ""} onClick={() => setTab(d.key)}>{d.label}</button>)}
        <button className={tab === "continuity" ? "active" : ""} onClick={() => setTab("continuity")}>Continuity</button>
      </nav>
      {!r ? <p className="muted">Loading…</p> : tab === "continuity" ? <Continuity r={r} /> : <DeptReport r={r} />}
    </section>
  );
}

function DeptReport({ r }: { r: any }) {
  const extra = EXTRA_COLS[r.department] ?? [];
  return (
    <>
      <div className="stats">
        <div className="stat"><b>{r.unique_items}</b><span>unique {r.item_noun}s</span></div>
        <div className="stat"><b><CountCell cell={r.scenes} title={`${r.label}: scenes`} /></b><span>scenes</span></div>
        <div className="stat"><b>{r.peak_units_in_one_scene ? r.peak_units_in_one_scene.count : 0}</b>
          <span>peak at once{r.peak_units_in_one_scene ? ` (sc ${r.peak_units_in_one_scene.scene})` : ""}</span></div>
        <div className="stat"><b><CountCell cell={r.scenes_with_unknown_quantity} title="Unknown quantity" /></b><span>unknown quantity</span></div>
        <div className="stat"><b><CountCell cell={r.inferred} title="Inferred — review" /></b><span>inferred — review</span></div>
        <div className="stat"><b><CountCell cell={r.needs_review} title="To review" /></b><span>to review</span></div>
      </div>
      <p className="small muted">By category: {Object.entries(r.by_category).map(([k, v]) => <span key={k} className="chip">{k} <CountCell cell={v as Cell} title={`${r.label}: ${k}`} /></span>)}</p>
      <div className="scroll">
        <table>
          <thead><tr><th>{r.item_noun}</th><th>Category</th><th>Owner</th><th className="num">Scenes</th><th className="num">Max / scene</th>
            <th>From</th><th>Characters</th>{extra.map(([, l]) => <th key={l}>{l}</th>)}<th className="num">Review</th></tr></thead>
          <tbody>{r.items.map((it: any) => (
            <tr key={it.item_id}>
              <td><b>{it.name}</b>{it.inferred.count > 0 && <span className="badge warning"> inferred</span>}</td>
              <td className="small">{it.category}</td><td className="small">{it.owner}</td>
              <td className="num"><CountCell cell={it.scenes} title={it.name} /></td>
              <td className="num">{it.max_in_one_scene ?? (it.unknown_quantity.count ? <span className="badge warning">unknown</span> : "")}</td>
              <td>{it.first_scene}</td>
              <td className="small">{it.characters.map((c: any) => <span key={`${c.character_id}-${c.role}`} className="chip">{c.name} · {c.role} <CountCell cell={c} title={`${it.name}: ${c.name}`} /></span>)}</td>
              {extra.map(([k]) => <td key={k} className="small">{show(it[k])}</td>)}
              <td className="num"><CountCell cell={it.needs_review} title={`${it.name}: to review`} /></td>
            </tr>))}</tbody>
        </table>
      </div>
    </>
  );
}

function Continuity({ r }: { r: any }) {
  const drill = useDrill();
  return (
    <div className="scroll">
      {r.characters.map((c: any) => (
        <div key={c.character_id} style={{ marginBottom: 16 }}>
          <h3 style={{ margin: "8px 0" }}>{c.name} <span className="muted small">{c.changes} change(s)</span></h3>
          <table>
            <thead><tr><th>Sc</th><th>Time</th><th>Costume / makeup / hair (state)</th></tr></thead>
            <tbody>{c.timeline.map((t: any) => (
              <tr key={t.scene_id} style={{ background: t.changed ? "var(--warn-soft)" : undefined }}>
                <td><button className="cellbtn" onClick={() => drill(`Scene ${t.scene}`, [t.scene_id])}>{t.scene}</button>{t.changed && <span className="badge warning"> change</span>}</td>
                <td className="small">{t.time}</td>
                <td className="small">{t.nothing_recorded ? <span className="muted">nothing recorded</span> :
                  t.entries.map((e: any) => <span key={e.occurrence_id} className="chip">{e.item}{e.state && <b> · {e.state}</b>}</span>)}</td>
              </tr>))}</tbody>
          </table>
          {c.continuity_checks.map((k: any, i: number) => <p key={i} className="small" style={{ color: "var(--warn)" }}>? {k.message}</p>)}
        </div>
      ))}
    </div>
  );
}
