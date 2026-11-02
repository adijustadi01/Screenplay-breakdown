import { useCallback, useEffect, useState } from "react";
import { api, Project } from "../../api";

type Progress = {
  project_id: number; file: string; scenes: number; cast_done: boolean; departments_done: boolean;
  open_reviews: number;
  job: null | { status: string; step: number; steps: number; step_label: string; done: number; total: number; error: string | null };
};

export const exportUrl = (versionId: number) => `/api/script-versions/${versionId}/export.xlsx`;

/** The simple path: 1 choose script → 2 break it down → 3 check → 4 download Excel. */
export function StartPage({ projects, versionId, onChanged, onSelect, goTo }: {
  projects: Project[]; versionId: number | null; onChanged: () => void;
  onSelect: (p: number, v: number) => void; goTo: (tab: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [prog, setProg] = useState<Progress | null>(null);
  const [settings, setSettings] = useState<any>(null);
  const [key, setKey] = useState("");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [inputKey, setInputKey] = useState(0);

  useEffect(() => { api.get("/api/settings").then(setSettings).catch(() => setSettings(null)); }, []);
  const load = useCallback(() => {
    if (!versionId) { setProg(null); return; }
    api.get<Progress>(`/api/script-versions/${versionId}/progress`).then(setProg).catch(() => setProg(null));
  }, [versionId]);
  useEffect(load, [load]);
  const running = prog?.job?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(async () => {
      const p = await api.get<Progress>(`/api/script-versions/${versionId}/progress`);
      setProg(p);
      if (p.job?.status !== "running") onChanged();
    }, 1000);
    return () => clearInterval(t);
  }, [running, versionId, onChanged]);

  const current = projects.flatMap((p) => p.script_versions.map((v) => ({ p, v }))).find((x) => x.v.id === versionId);

  const upload = async (newFilm: boolean) => {
    if (!file) return;
    setBusy("Reading your script… (a scanned PDF can take a few minutes)"); setErr("");
    try {
      let pid = newFilm ? null : (projectId ?? current?.p.id ?? null);
      if (!pid) pid = (await api.post<{ id: number }>("/api/projects", { title: file.name.replace(/\.[^.]+$/, "") })).id;
      const fd = new FormData();
      fd.append("file", file); fd.append("ocr_lang", "eng");
      const r = await api.post<any>(`/api/projects/${pid}/scripts`, fd);
      setFile(null); setInputKey((k) => k + 1); onChanged(); onSelect(pid, r.script_version_id);
    } catch (e) { setErr(String(e).replace(/^Error: /, "")); } finally { setBusy(""); }
  };

  const useClaude = !!settings?.has_api_key;
  const breakdown = async () => {
    setErr("");
    try {
      await api.post(`/api/script-versions/${versionId}/breakdown-all`, { extractor: useClaude ? "claude" : "heuristic", workers: 4 });
      load();
    } catch (e) { setErr(String(e).replace(/^Error: /, "")); }
  };
  const saveKey = async () => { setSettings(await api.post("/api/settings", { anthropic_api_key: key })); setKey(""); setShowKey(false); };

  const step1 = !!versionId;
  const step2 = !!prog?.cast_done && !!prog?.departments_done;
  const job = prog?.job;

  return (
    <div className="start">
      <p className="lead">Four steps. Do them in order — each one turns green when it's done.</p>

      <Step n={1} done={step1} title="Choose your script">
        {current && <p>Working on: <b>{current.p.title}</b> <span className="muted">({current.v.file})</span></p>}
        <div className="row">
          <input key={inputKey} type="file" accept=".pdf,.docx,.fdx,.fountain,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {!current && <button className="btn primary big" disabled={!file || !!busy} onClick={() => upload(true)}>Import script</button>}
          {current && file && <>
            <button className="btn primary big" disabled={!!busy} onClick={() => upload(true)}>Import as a new film</button>
            <button className="btn" disabled={!!busy} onClick={() => upload(false)} title="A revised draft of the same film">
              Import as a new draft of “{current.p.title}”</button>
          </>}
        </div>
        {current && !file && <p className="small muted">To work on a different script, choose it in the <b>Script</b> menu at the top right, or pick a new file above.</p>}
        <p className="small muted">PDF (typed or scanned), Word (.docx), Final Draft (.fdx) or Fountain.</p>
        {projects.length > 0 && current && file &&
          <p className="small muted">New draft of the same film? Choose the second button — you can compare drafts later under <b>Revisions</b>.
            {projects.length > 1 && <> Film: <select value={projectId ?? current.p.id} onChange={(e) => setProjectId(Number(e.target.value))}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></>}</p>}
        {busy && <p><span className="spinner" /> {busy}</p>}
        {step1 && prog && <p className="okline">✓ {prog.scenes} scenes found.
          <button className="linkbtn" onClick={() => goTo("import")}>See the import check</button></p>}
      </Step>

      <Step n={2} done={step2} disabled={!step1} title="Break down the script">
        <p>Reads every scene and lists the cast, locations, props, costumes, vehicles, makeup, extras, animals, stunts & effects, songs and production needs.</p>
        <div className="row">
          <button className="btn primary big" disabled={!step1 || running} onClick={breakdown}>
            {step2 ? "Break it down again" : "Break down my script"}</button>
          <span className="small muted">
            {useClaude ? "Uses Claude (AI) — most accurate."
              : <>Using the free built-in reader (less accurate). <button className="linkbtn" onClick={() => setShowKey(!showKey)}>Use Claude AI instead</button></>}
          </span>
        </div>
        {showKey && !useClaude && <div className="hint">
          <p className="small">Claude reads scripts much more accurately. It needs an Anthropic API key (paid per use, from console.anthropic.com).
            The key is stored only on this computer.</p>
          <div className="row"><input type="password" placeholder="sk-ant-..." value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn" disabled={!key.trim()} onClick={saveKey}>Save key</button></div>
        </div>}
        {running && job && <div className="progress">
          <div className="small">Step {job.step} of {job.steps}: {job.step_label}{job.total ? ` — ${job.done} of ${job.total} scenes` : ""}</div>
          <div className="bar"><div style={{ width: `${(((job.step - 1) + (job.total ? job.done / job.total : 0)) / job.steps) * 100}%` }} /></div>
        </div>}
        {job?.status === "failed" && <p className="error">Something went wrong: {job.error}</p>}
        {step2 && !running && <p className="okline">✓ Breakdown ready.</p>}
      </Step>

      <Step n={3} done={step2 && prog?.open_reviews === 0} disabled={!step2} title="Check what the app wasn't sure about">
        <p>The app never guesses. When something is unclear (for example "a crowd" with no number, or two names that may be the same person) it asks you.</p>
        <div className="row">
          <button className="btn big" disabled={!step2} onClick={() => goTo("review")}>
            Open the checklist{prog && step2 ? ` (${prog.open_reviews} to check)` : ""}</button>
          <span className="small muted">Optional, but it makes the breakdown reliable. You can do it any time and download again.</span>
        </div>
      </Step>

      <Step n={4} done={false} disabled={!step2} title="Get your breakdown">
        <div className="row">
          <a className={`btn primary big ${!step2 ? "disabled" : ""}`} href={step2 && versionId ? exportUrl(versionId) : undefined}
            download onClick={(e) => { if (!step2) e.preventDefault(); }}>⬇ Download Excel file</a>
          <span className="small muted">One file with a sheet each for scenes, characters, locations, every department, continuity and your schedule. It goes to your Downloads folder.</span>
        </div>
        <p className="small">Or look at it here:{" "}
          <button className="linkbtn" disabled={!step2} onClick={() => goTo("scenes")}>Scenes</button> ·{" "}
          <button className="linkbtn" disabled={!step2} onClick={() => goTo("reports")}>Cast & location reports</button> ·{" "}
          <button className="linkbtn" disabled={!step2} onClick={() => goTo("departments")}>Departments</button> ·{" "}
          <button className="linkbtn" disabled={!step2} onClick={() => goTo("schedule")}>Schedule & DOOD</button></p>
      </Step>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

function Step({ n, title, done, disabled, children }: { n: number; title: string; done: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <section className={`panel step ${done ? "done" : ""} ${disabled ? "off" : ""}`}>
      <div className="stepnum">{done ? "✓" : n}</div>
      <div className="stepbody"><h2>{title}</h2>{children}</div>
    </section>
  );
}
