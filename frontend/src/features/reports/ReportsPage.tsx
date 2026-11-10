import { useEffect, useState } from "react";
import { api, Cell, eighths } from "../../api";
import { CountCell } from "../../components/CountCell";

const TABS = [
  ["scenes", "Scenes"], ["characters", "Characters"], ["locations", "Locations"],
  ["combinations", "Who appears together"], ["crosstab:character_time", "Character × time"],
  ["crosstab:location_time", "Location × time"], ["crosstab:character_location_time", "Character × location × time"],
];

/** Reports computed in code from the reviewed facts. Every number opens its scenes. */
// The download section that matches each report tab.
const EXPORT_KEY: Record<string, string> = {
  scenes: "scene_summary", characters: "characters", locations: "locations", combinations: "combinations",
  "crosstab:character_time": "character_time", "crosstab:location_time": "location_time",
  "crosstab:character_location_time": "character_location_time",
};
const REPORT_KEYS = [...Object.values(EXPORT_KEY), "cast_by_role"];
// The Characters tab downloads the totals and the per-age / per-actor table together.
const exportKeys = (tab: string) => (tab === "characters" ? ["characters", "cast_by_role"] : [EXPORT_KEY[tab]]);

export function ReportsPage({ versionId, onDownload }: { versionId: number; onDownload: (preselect: string[] | null) => void }) {
  const [tab, setTab] = useState("scenes");
  const [mode, setMode] = useState("as_written");
  // Keep which tab/mode the data belongs to, so a tab switch never renders stale data.
  const [loaded, setLoaded] = useState<{ key: string; data: any } | null>(null);
  const key = `${versionId}|${tab}|${mode}`;
  useEffect(() => {
    let live = true;
    api.get(`/api/script-versions/${versionId}/reports/${tab}?time_mode=${mode}`)
      .then((data) => { if (live) setLoaded({ key, data }); });
    return () => { live = false; };
  }, [versionId, tab, mode, key]);
  const r = loaded?.key === key ? loaded.data : null;
  return (
    <section className="panel">
      <div className="row" style={{ marginBottom: 12 }}>
        <nav className="tabs">{TABS.map(([k, label]) =>
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>)}</nav>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={() => onDownload(exportKeys(tab))}
          title="Download this report as Excel or PDF — you can tick more reports in the next window">⬇ Download this report</button>
        <button className="btn" onClick={() => onDownload(REPORT_KEYS)} title="All the reports on this page">⬇ All reports</button>
        <label className="small">Time of day{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="as_written">as written (CONTINUOUS stays CONTINUOUS)</option>
            <option value="effective">with inherited lighting (inferred, flagged)</option>
          </select></label>
      </div>
      {!r ? <p className="muted">Loading…</p> :
        tab === "scenes" ? <SceneReport r={r} /> :
        tab === "characters" ? <CharacterReport r={r} /> :
        tab === "locations" ? <LocationReport r={r} /> :
        tab === "combinations" ? <Combos r={r} /> : <CrossTab r={r} />}
    </section>
  );
}

function SceneReport({ r }: { r: any }) {
  const t = r.int_ext_by_time;
  return (
    <>
      <div className="stats">
        <div className="stat"><b><CountCell cell={r.total} title="All scenes" /></b><span>scenes</span></div>
        <div className="stat"><b>{eighths(r.total_eighths)}</b><span>pages</span></div>
        <div className="stat"><b><CountCell cell={r.omitted} title="Omitted" /></b><span>omitted</span></div>
        <div className="stat"><b><CountCell cell={r.needs_review} title="Scenes with flags" /></b><span>flagged headings</span></div>
        <div className="stat"><b><CountCell cell={r.inferred_time} title="Inherited time" /></b><span>inherited times</span></div>
      </div>
      <h3>INT/EXT × time of day</h3>
      <Matrix rows={t.rows} cols={t.columns} cells={t.cells} rowTotals={r.by_int_ext} colTotals={r.by_time} />
      <p className="small muted">Page counts: {Object.entries(r.by_int_ext).map(([k, v]: any) => `${k} ${eighths(v.eighths)}`).join(" · ")}</p>
    </>
  );
}

function CharacterReport({ r }: { r: any }) {
  const hasAges = r.characters.some((c: any) => c.by_role?.length);
  const [byAge, setByAge] = useState(true);
  return (
    <>
      <p>{r.unique_characters} characters on set or in voice · {r.speaking_characters} speaking · {r.silent_only_characters} silent only · {r.mentioned_only_characters} only mentioned</p>
      {hasAges && <label className="small"><input type="checkbox" checked={byAge} onChange={(e) => setByAge(e.target.checked)} />{" "}
        Also show scenes for each age / actor (e.g. child and adult Arjun) under the character's total</label>}
      <div className="scroll">
        <table>
          <thead><tr><th>Character</th><th>Casting roles</th><th className="num">Scenes</th><th className="num">On screen</th><th className="num">O.S.</th>
            <th className="num">V.O.</th><th className="num">Mentioned</th><th className="num">Speaking</th><th className="num">Silent</th>
            <th className="num">Dialogue</th><th className="num">Pages</th><th>First</th><th>Last</th><th className="num">To review</th></tr></thead>
          <tbody>{r.characters.flatMap((c: any) => [
            <tr key={c.character_id}>
              <td><b>{c.name}</b>{c.kind === "group" && <span className="badge"> group</span>}</td>
              <td className="small">{c.casting_roles.join(", ")}</td>
              <td className="num"><CountCell cell={c.appearances} title={`${c.name}: scenes`} /></td>
              <td className="num"><CountCell cell={c.on_screen} title={`${c.name}: on screen`} /></td>
              <td className="num"><CountCell cell={c.off_screen} title={`${c.name}: off screen`} /></td>
              <td className="num"><CountCell cell={c.voice_only} title={`${c.name}: voice only`} /></td>
              <td className="num"><CountCell cell={c.mentioned_only} title={`${c.name}: mentioned only`} /></td>
              <td className="num"><CountCell cell={c.speaking} title={`${c.name}: speaking`} /></td>
              <td className="num"><CountCell cell={c.silent} title={`${c.name}: silent`} /></td>
              <td className="num">{c.dialogue_blocks}</td>
              <td className="num">{eighths(c.eighths)}</td>
              <td>{c.first_appearance && (c.first_appearance.number ?? `#${c.first_appearance.seq}`)}</td>
              <td>{c.last_appearance && (c.last_appearance.number ?? `#${c.last_appearance.seq}`)}</td>
              <td className="num"><CountCell cell={c.unreviewed} title={`${c.name}: unreviewed`} /></td>
            </tr>,
            ...(byAge ? (c.by_role ?? []).map((ro: any) => (
              <tr key={`${c.character_id}:${ro.casting_role_id}`} className="small">
                <td style={{ paddingLeft: 22 }}>↳ {ro.casting_role_id === null ? <span className="badge warning">{ro.name}</span> : ro.name}</td>
                <td className="muted">{ro.age_note ? `age ${ro.age_note}` : ""}</td>
                <td className="num"><CountCell cell={ro.appearances} title={`${ro.name}: scenes`} /></td>
                <td colSpan={4}></td>
                <td className="num"><CountCell cell={ro.speaking} title={`${ro.name}: speaking`} /></td>
                <td></td>
                <td className="num">{ro.dialogue_blocks}</td>
                <td className="num">{eighths(ro.eighths)}</td>
                <td>{ro.first_appearance.number ?? `#${ro.first_appearance.seq}`}</td>
                <td>{ro.last_appearance.number ?? `#${ro.last_appearance.seq}`}</td>
                <td></td>
              </tr>)) : [])]
            )}</tbody>
        </table>
      </div>
      <p className="small muted">Scenes = on screen + O.S. (actor needed). V.O. and mentioned are never counted as on set.</p>
    </>
  );
}

function LocationReport({ r }: { r: any }) {
  return (
    <>
      <p>{r.story_locations} story locations · {r.sets} sets · <CountCell cell={r.scenes_without_location} title="No location" /> scenes without a location</p>
      <div className="scroll">
        <table>
          <thead><tr><th>Location</th><th className="num">Scenes</th><th className="num">Pages</th><th>INT/EXT</th><th>Time</th><th>Characters required</th></tr></thead>
          <tbody>{r.locations.map((n: any) => (
            <tr key={n.location_id}>
              <td style={{ paddingLeft: n.parent_id ? 28 : 8 }}>{n.parent_id ? "└ " : ""}<b>{n.name}</b></td>
              <td className="num"><CountCell cell={n.scenes} title={n.label} /></td>
              <td className="num">{eighths(n.eighths)}</td>
              <td className="small">{Object.entries(n.by_int_ext).map(([k, v]) => <span key={k}>{k} <CountCell cell={v as Cell} title={`${n.label} ${k}`} /> </span>)}</td>
              <td className="small">{Object.entries(n.by_time).map(([k, v]) => <span key={k}>{k} <CountCell cell={v as Cell} title={`${n.label} ${k}`} /> </span>)}</td>
              <td className="small">{n.characters.map((c: any) => <span key={c.character_id} className="chip">{c.name} <CountCell cell={c} title={`${c.name} at ${n.label}`} /></span>)}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </>
  );
}

function Combos({ r }: { r: any }) {
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>Characters</th><th className="num">Together</th><th>By time</th><th>Where</th></tr></thead>
        <tbody>{r.combinations.map((c: any) => (
          <tr key={c.character_ids.join("-")}>
            <td><b>{c.names.join(" + ")}</b></td>
            <td className="num"><CountCell cell={c.together} title={c.names.join(" + ")} /></td>
            <td className="small">{Object.entries(c.by_time).map(([k, v]) => <span key={k}>{k} <CountCell cell={v as Cell} title={`${c.names.join(" + ")} ${k}`} /> </span>)}</td>
            <td className="small">{Object.entries(c.by_location).map(([k, v]) => <span key={k} className="chip">{k} <CountCell cell={v as Cell} title={`${c.names.join(" + ")} at ${k}`} /></span>)}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function CrossTab({ r }: { r: any }) {
  return <Matrix rows={r.rows} cols={r.columns} cells={r.cells} rowTotals={r.row_totals} />;
}

export function Matrix({ rows, cols, cells, rowTotals, colTotals }: {
  rows: string[]; cols: string[]; cells: Record<string, Record<string, Cell>>;
  rowTotals?: Record<string, Cell>; colTotals?: Record<string, Cell>;
}) {
  return (
    <div className="scroll">
      <table>
        <thead><tr><th></th>{cols.map((c) => <th key={c} className="num">{c}</th>)}{rowTotals && <th className="num">Total</th>}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}><td><b>{r}</b></td>
              {cols.map((c) => <td key={c} className="num"><CountCell cell={cells[r]?.[c]} title={`${r} · ${c}`} /></td>)}
              {rowTotals && <td className="num"><CountCell cell={rowTotals[r]} title={r} /></td>}
            </tr>))}
          {colTotals && <tr><td className="muted">Total</td>{cols.map((c) => <td key={c} className="num"><CountCell cell={colTotals[c]} title={c} /></td>)}{rowTotals && <td />}</tr>}
        </tbody>
      </table>
    </div>
  );
}
