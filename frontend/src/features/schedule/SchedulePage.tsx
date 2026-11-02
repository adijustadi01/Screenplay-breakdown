import { useCallback, useEffect, useState } from "react";
import { api, eighths } from "../../api";
import { useDrill } from "../../components/Drill";

/** Stage 3: shooting schedule and DOOD. SHOOTING days (calendar) are kept apart from STORY days (plot). */
export function SchedulePage({ projectId, versionId }: { projectId: number; versionId: number }) {
  const [schedules, setSchedules] = useState<any[]>([]);
  const [sid, setSid] = useState<number | null>(null);
  const [r, setR] = useState<any>(null);
  const [story, setStory] = useState<any[]>([]);
  const [maxE, setMaxE] = useState(40);
  const [msg, setMsg] = useState("");
  const [revs, setRevs] = useState<any[]>([]);
  const drill = useDrill();

  const loadList = useCallback(() => api.get<any[]>(`/api/script-versions/${versionId}/schedules`).then((l) => {
    setSchedules(l); setSid((cur) => cur && l.some((x) => x.id === cur) ? cur : l[0]?.id ?? null);
  }), [versionId]);
  const load = useCallback(() => {
    if (sid) api.get(`/api/schedules/${sid}`).then(setR); else setR(null);
    api.get<any[]>(`/api/script-versions/${versionId}/story-days`).then(setStory);
  }, [sid, versionId]);
  useEffect(() => { loadList(); api.get<any[]>(`/api/projects/${projectId}/revisions`).then(setRevs); }, [loadList, projectId]);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<any>, done?: string) => { setMsg(""); try { const out = await fn(); if (done) setMsg(done + (out?.created ? `: ${out.created.join(", ") || "none needed"}` : "")); } catch (e) { setMsg(String(e)); } load(); };
  const create = () => run(async () => { const x = await api.post<any>(`/api/script-versions/${versionId}/schedules`, { name: "Main unit" }); await loadList(); setSid(x.id); });
  const carry = async (revId: number, oldSid: number) => run(async () => {
    const out = await api.post<any>(`/api/schedules/${oldSid}/migrate`, { revision_id: revId });
    await loadList(); setSid(out.schedule_id);
    setMsg(`Carried forward: ${out.kept} scheduled scene(s) kept; dropped ${out.dropped.map((d: any) => `${d.scene} (${d.reason})`).join(", ") || "none"}; ${out.new_scenes_unscheduled.length} new scene(s) to schedule.`);
  });

  const sv = r?.story_vs_shooting;
  const incoming = revs.filter((x) => x.to_version_id === versionId);
  return (
    <>
      <section className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>Schedule</h2>
          <select value={sid ?? ""} onChange={(e) => setSid(Number(e.target.value))}>
            {schedules.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.days} days)</option>)}</select>
          <button className="btn" onClick={create}>New schedule</button>
          <button className="btn" onClick={() => run(() => api.post(`/api/script-versions/${versionId}/story-days/infer`), "Story days inferred")}>Infer story days</button>
          {sid && <>
            <button className="btn" onClick={() => run(() => api.post(`/api/schedules/${sid}/days`, {}))}>+ Shooting day</button>
            <label className="small">auto-fill ≤ <input type="text" size={3} value={maxE} onChange={(e) => setMaxE(Number(e.target.value) || 40)} /> eighths</label>
            <button className="btn" onClick={() => run(() => api.post(`/api/schedules/${sid}/autofill`, { max_eighths: maxE }), "Suggestion added — review it")}>Suggest days</button>
            <button className="btn" onClick={() => run(() => api.post(`/api/script-versions/${versionId}/casting-roles/materialize`), "Casting roles created")}>Create missing casting roles</button>
          </>}
        </div>
        {incoming.map((rv) => <IncomingSchedules key={rv.id} rev={rv} onCarry={carry} />)}
        {msg && <p className="small">{msg}</p>}
      </section>
      {!sid ? <section className="panel"><p className="muted">Create a schedule. Story days can be inferred without one.</p></section> : !r ? null : (
        <>
          <section className="panel" style={{ borderColor: "var(--accent)" }}>
            <h2>Story time vs shooting time</h2>
            <div className="stats">
              <div className="stat"><b>{sv.shooting_days}</b><span>SHOOTING days (calendar — set by you)</span></div>
              <div className="stat"><b>{sv.story_days}</b><span>STORY days (plot{sv.story_days_inferred ? " — inferred, review" : ""})</span></div>
              <div className="stat"><b>{sv.night_scenes}</b><span>night scenes</span></div>
              <div className="stat"><b>{sv.story_nights}</b><span>story nights</span></div>
              <div className="stat"><b>{sv.shooting_nights}</b><span>shooting days with night work</span></div>
            </div>
            <p className="small">{sv.explanation}</p>
          </section>
          <div className="queue">
            <section className="panel">
              <h2>Unscheduled ({r.unscheduled.length})</h2>
              <div className="scroll">{r.unscheduled.map((u: any) => (
                <div key={u.scene_id} className="qitem">
                  <b>{u.scene}</b> <span className="small">{u.heading}</span>
                  <div className="small muted">{eighths(u.eighths)} pp · story {u.story_day ?? "?"} · {u.location}</div>
                  <select className="small" defaultValue="" onChange={(e) => e.target.value && run(() => api.post(`/api/days/${e.target.value}/scenes`, { scene_id: u.scene_id }))}>
                    <option value="">→ shooting day…</option>
                    {r.oneliner.map((d: any) => <option key={d.day_id} value={d.day_id}>Shoot day {d.day_number}{d.date ? ` (${d.date})` : ""}</option>)}
                  </select>
                </div>))}</div>
            </section>
            <section className="panel">
              <h2>One-liner — by SHOOTING day</h2>
              <div className="scroll">{r.oneliner.map((d: any) => (
                <div key={d.day_id} style={{ marginBottom: 12 }}>
                  <div className="row small">
                    <b>SHOOT DAY {d.day_number}</b>
                    <input type="date" value={d.date ?? ""} onChange={(e) => run(() => api.patch(`/api/days/${d.day_id}`, { shoot_date: e.target.value || null, label: d.label }))} />
                    <span>{eighths(d.eighths)} pp</span>
                    <span className="muted">story days in this shoot: {d.story_days.join(", ") || "?"}</span>
                    <span className="muted">{d.label}</span>
                    <button className="btn" onClick={() => run(() => api.post(`/api/days/${d.day_id}/move`, { day_number: Math.max(1, d.day_number - 1) }))}>↑</button>
                    <button className="btn danger" onClick={() => run(() => api.del(`/api/days/${d.day_id}`))}>delete day</button>
                  </div>
                  <table><tbody>{d.scenes.map((e: any) => (
                    <tr key={e.scene_id}>
                      <td><button className="cellbtn" onClick={() => drill(`Scene ${e.scene}`, [e.scene_id])}>{e.scene}</button></td>
                      <td className="small">{e.int_ext}</td>
                      <td className="small">{e.time}{e.time_effective !== e.time && <span className="muted"> → {e.time_effective}</span>}</td>
                      <td className="small"><span className="chip" title="story day (plot), not shooting day">story {e.story_day ?? "?"}</span></td>
                      <td className="small">{e.location}</td>
                      <td className="small num">{eighths(e.eighths)}{e.split && " (part)"}</td>
                      <td className="small">cast {e.cast_ids.join(", ")}</td>
                      <td><button className="btn" onClick={() => run(() => api.del(`/api/days/${d.day_id}/scenes/${e.scene_id}`))}>×</button></td>
                    </tr>))}</tbody></table>
                </div>))}</div>
            </section>
          </div>
          <Dood r={r} />
          {r.warnings.length > 0 && <section className="panel"><h2>Warnings</h2>{r.warnings.map((w: string, i: number) => <p key={i} className="small" style={{ color: "var(--warn)" }}>! {w}</p>)}</section>}
        </>
      )}
      <StoryDays rows={story} onSaved={load} />
    </>
  );
}

function IncomingSchedules({ rev, onCarry }: { rev: any; onCarry: (revId: number, oldSid: number) => void }) {
  const [old, setOld] = useState<any[]>([]);
  useEffect(() => { api.get<any[]>(`/api/script-versions/${rev.from_version_id}/schedules`).then(setOld); }, [rev.from_version_id]);
  if (!old.length) return null;
  return <p className="small">Previous draft v{rev.from_version_id} has schedule(s): {old.map((s) => (
    <button key={s.id} className="btn" onClick={() => onCarry(rev.id, s.id)}>Carry “{s.name}” forward</button>))}</p>;
}

function Dood({ r }: { r: any }) {
  const days = r.dood.days;
  return (
    <section className="panel">
      <h2>Day out of days — casting requirements × SHOOTING days</h2>
      <p className="small muted">Rows are who you hire (casting requirements), not characters: one character can need several (Young / Adult). Columns are shooting days only. SW start · W work · WF finish · SWF single day · H hold · WD/PW drop and pick-up after a gap longer than {r.schedule.drop_after_days} days.</p>
      <div className="scroll">
        <table>
          <thead><tr><th>#</th><th>Casting requirement</th><th>Character</th>
            {days.map((d: any) => <th key={d.day_number} className="num" title={d.date ?? ""}>{d.day_number}</th>)}
            <th className="num">Work</th><th className="num">Hold</th><th className="num">Drop</th><th>Start–finish</th></tr></thead>
          <tbody>{r.dood.cast.map((c: any) => (
            <tr key={c.key}>
              <td>{c.cast_id}</td>
              <td><b>{c.requirement}</b>{c.implicit && <span className="badge warning" title="No casting role yet — create one"> implicit</span>}
                {c.problems.length > 0 && <span className="badge error" title={c.problems.join("; ")}> unassigned</span>}</td>
              <td className="small">{c.character}</td>
              {days.map((d: any) => { const code = c.codes[d.day_number] ?? c.codes[String(d.day_number)];
                return <td key={d.day_number} className="num" style={{ background: code && code !== "H" ? "var(--accent-soft)" : undefined }}>{code}</td>; })}
              <td className="num">{c.work_days}</td><td className="num">{c.hold_days}</td><td className="num">{c.drop_days}</td>
              <td className="small">{c.start}–{c.finish}</td>
            </tr>))}</tbody>
        </table>
      </div>
      {r.dood.voice.length > 0 && <p className="small">Voice (recorded separately, not on set): {r.dood.voice.map((v: any) => v.requirement).join(", ")}</p>}
      {r.dood.background_groups.length > 0 && <p className="small">Background groups (casting/background department, not cast DOOD): {r.dood.background_groups.map((v: any) => v.requirement).join(", ")}</p>}
    </section>
  );
}

function StoryDays({ rows, onSaved }: { rows: any[]; onSaved: () => void }) {
  const [edit, setEdit] = useState<Record<number, string>>({});
  if (!rows.some((x) => x.label)) return null;
  const save = async (row: any) => {
    const label = edit[row.story_day_id];
    const m = /(\d+)/.exec(label);
    await api.post(`/api/facts/scene_story_days/${row.story_day_id}`, { action: "edit",
      changes: { label, story_day: m ? Number(m[1]) : null, is_flashback: /^FB/i.test(label) } });
    onSaved();
  };
  return (
    <section className="panel">
      <h2>Story days (plot time — inferred, editable)</h2>
      <p className="small muted">When each scene happens in the story. Inferred from times of day and transitions; correct them here. They never change shooting days.</p>
      <div className="scroll" style={{ maxHeight: 320 }}>
        <table><thead><tr><th>Sc</th><th>Time (as written)</th><th>Story day</th><th>Why</th><th></th></tr></thead>
          <tbody>{rows.filter((x) => !x.omitted).map((x) => (
            <tr key={x.scene_id}>
              <td>{x.scene}</td><td className="small">{x.time}</td>
              <td><input type="text" size={10} value={edit[x.story_day_id] ?? x.label ?? ""} onChange={(e) => setEdit({ ...edit, [x.story_day_id]: e.target.value })} />
                {x.inferred && x.review_status === "unreviewed" ? <span className="badge warning"> inferred</span> : <span className="badge ok"> {x.review_status}</span>}</td>
              <td className="small muted">{x.basis}</td>
              <td><button className="btn" disabled={!x.story_day_id || edit[x.story_day_id] === undefined} onClick={() => save(x)}>save</button>
                {x.story_day_id && <button className="btn" onClick={() => api.post(`/api/facts/scene_story_days/${x.story_day_id}`, { action: "confirm" }).then(onSaved)}>✓</button>}</td>
            </tr>))}</tbody></table>
      </div>
    </section>
  );
}
