import { useEffect, useState } from "react";
import { api, eighths, Project } from "../../api";

type Coverage = any;

/** Import a script, see the coverage check, run extraction and reconciliation. */
export function ImportPage({ projects, versionId, onChanged, onSelect }: {
  projects: Project[]; versionId: number | null; onChanged: () => void; onSelect: (p: number, v: number) => void;
}) {
  const [title, setTitle] = useState("");
  const [projectId, setProjectId] = useState<number | null>(projects[0]?.id ?? null);
  const [file, setFile] = useState<File | null>(null);
  const [ocr, setOcr] = useState("eng");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [job, setJob] = useState<any>(null);
  const [extractor, setExtractor] = useState("claude");
  const [recon, setRecon] = useState<any>(null);
  const [djob, setDjob] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [key, setKey] = useState("");
  useEffect(() => { api.get("/api/settings").then(setSettings).catch(() => setSettings(null)); }, []);
  const saveKey = async () => { setSettings(await api.post("/api/settings", { anthropic_api_key: key })); setKey(""); };
  const [depts, setDepts] = useState<{ key: string; label: string }[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  useEffect(() => { api.get<any[]>("/api/departments").then((d) => { setDepts(d); setChosen(d.map((x) => x.key)); }); }, []);
  useEffect(() => {
    if (!versionId) return;
    api.get<any>(`/api/script-versions/${versionId}/departments/extract`).then((r) => setDjob(r.job));
  }, [versionId]);
  useEffect(() => {
    if (!versionId || djob?.status !== "running") return;
    const t = setInterval(async () => {
      const r = await api.get<any>(`/api/script-versions/${versionId}/departments/extract`);
      setDjob(r.job);
      if (r.job?.status !== "running") onChanged();
    }, 1000);
    return () => clearInterval(t);
  }, [djob?.status, versionId, onChanged]);
  const extractDepts = async () => {
    setErr("");
    try { setDjob(await api.post(`/api/script-versions/${versionId}/departments/extract`, { extractor, departments: chosen, workers: 4 })); }
    catch (e) { setErr(String(e)); }
  };

  useEffect(() => { if (!projectId && projects[0]) setProjectId(projects[0].id); }, [projects, projectId]);
  useEffect(() => {
    setCoverage(null); setRecon(null);
    if (versionId) {
      api.get(`/api/script-versions/${versionId}/coverage`).then(setCoverage);
      api.get<any>(`/api/script-versions/${versionId}/extract`).then((r) => setJob(r.job));
    }
  }, [versionId]);
  useEffect(() => {
    if (!versionId || job?.status !== "running") return;
    const t = setInterval(async () => {
      const r = await api.get<any>(`/api/script-versions/${versionId}/extract`);
      setJob(r.job);
      if (r.job?.status !== "running") onChanged();
    }, 1000);
    return () => clearInterval(t);
  }, [job?.status, versionId, onChanged]);

  const createProject = async () => {
    if (!title.trim()) return;
    const p = await api.post<{ id: number }>("/api/projects", { title });
    setTitle(""); setProjectId(p.id); onChanged();
  };
  const upload = async () => {
    if (!file || !projectId) return;
    setBusy("Importing… (scanned PDFs take longer: OCR)"); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file); fd.append("ocr_lang", ocr);
      const r = await api.post<any>(`/api/projects/${projectId}/scripts`, fd);
      setCoverage(r.coverage); onChanged(); onSelect(projectId, r.script_version_id);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  };
  const extract = async () => {
    setErr("");
    try { setJob(await api.post(`/api/script-versions/${versionId}/extract`, { extractor, workers: 4 })); }
    catch (e) { setErr(String(e)); }
  };
  const reconcile = async () => {
    setRecon(await api.post(`/api/script-versions/${versionId}/reconcile`)); onChanged();
  };

  return (
    <>
      <div className="grid2">
        <section className="panel">
          <h2>1 · Import a script</h2>
          <div className="row">
            <select value={projectId ?? ""} onChange={(e) => setProjectId(Number(e.target.value))}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
            </select>
            <span className="muted">or</span>
            <input type="text" placeholder="New project title" value={title} onChange={(e) => setTitle(e.target.value)} />
            <button className="btn" onClick={createProject}>Create</button>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <input type="file" accept=".pdf,.docx,.fdx,.fountain,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <label className="small muted">OCR language <input type="text" size={8} value={ocr} onChange={(e) => setOcr(e.target.value)} /></label>
            <button className="btn primary" disabled={!file || !projectId || !!busy} onClick={upload}>Import</button>
          </div>
          <p className="small muted">PDF (text or scanned), Word (.docx), Final Draft (.fdx), Fountain.</p>
          {busy && <p>{busy}</p>}
          {err && <p className="error">{err}</p>}
        </section>
        <section className="panel">
          <h2>2 · Extract cast & locations · 3 · Reconcile</h2>
          {settings && !settings.has_api_key && (
            <div className="row small" style={{ marginBottom: 10 }}>
              <span>Claude extraction needs your Anthropic API key:</span>
              <input type="password" placeholder="sk-ant-..." value={key} onChange={(e) => setKey(e.target.value)} />
              <button className="btn" disabled={!key.trim()} onClick={saveKey}>Save key</button>
              <span className="muted">(stored only on this computer, in {settings.data_dir})</span>
            </div>)}
          {settings && settings.has_api_key && <p className="small muted">Anthropic API key set. <button className="btn" onClick={() => api.post("/api/settings", { anthropic_api_key: "" }).then(setSettings)}>Remove key</button></p>}
          {settings && !settings.ocr_available && <p className="small" style={{ color: "var(--warn)" }}>OCR (Tesseract) not found — scanned PDFs cannot be read; text PDFs, Word, Final Draft and Fountain work.</p>}
          {!versionId ? <p className="muted">Import or select a script first.</p> : (
            <>
              <div className="row">
                <select value={extractor} onChange={(e) => setExtractor(e.target.value)}>
                  <option value="claude">Claude (needs ANTHROPIC_API_KEY)</option>
                  <option value="heuristic">Offline rules (no key, lower accuracy)</option>
                </select>
                <button className="btn primary" disabled={job?.status === "running"} onClick={extract}>Run extraction</button>
              </div>
              {job && <p className={job.status === "failed" ? "error" : ""}>
                Extraction: {job.status}{job.total ? ` — ${job.done}/${job.total} scenes` : ""}{job.error ? ` — ${job.error}` : ""}</p>}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" onClick={reconcile}>Find aliases & location variants</button>
                {recon && <span className="small">{Object.keys(recon.proposals_created).length
                  ? Object.entries(recon.proposals_created).map(([k, v]) => `${v} ${k}`).join(", ") + " → review queue"
                  : "no new proposals"}</span>}
              </div>
              <h3>Stage 2 · Departmental breakdown</h3>
              <div className="row small">{depts.map((d) => (
                <label key={d.key}><input type="checkbox" checked={chosen.includes(d.key)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, d.key] : chosen.filter((x) => x !== d.key))} /> {d.label}</label>))}</div>
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn primary" disabled={djob?.status === "running" || !chosen.length} onClick={extractDepts}>Run department extraction</button>
                {djob && <span className={djob.status === "failed" ? "error small" : "small"}>Departments: {djob.status}{djob.total ? ` — ${djob.done}/${djob.total} scenes` : ""}{djob.error ? ` — ${djob.error}` : ""}</span>}
              </div>
              <p className="small muted">Run cast extraction first (department items are linked to the cast). Every extracted fact, inferred requirement and proposed merge goes to the Review queue. Nothing is merged until you accept it.</p>
            </>
          )}
        </section>
      </div>
      {coverage && <CoverageView c={coverage} />}
    </>
  );
}

function CoverageView({ c }: { c: Coverage }) {
  const ok = c.verdict === "COMPLETE";
  return (
    <section className="panel">
      <h2>Coverage check — {c.file} <span className={`badge ${ok ? "ok" : "error"}`}>{c.verdict}</span></h2>
      <div className="stats">
        <div className="stat"><b>{c.pages.total}</b><span>pages ({c.pages.ocr} OCR)</span></div>
        <div className="stat"><b>{c.scenes.total}</b><span>scenes ({c.scenes.omitted} omitted)</span></div>
        <div className="stat"><b>{eighths(c.scenes.total_eighths)}</b><span>pages of scenes</span></div>
        <div className="stat"><b>{c.text_accounting.difference ?? "?"}</b><span>characters unaccounted</span></div>
        <div className="stat"><b>{c.elements.unassigned.length}</b><span>lines outside any scene</span></div>
        <div className="stat"><b>{c.review_queue.open}</b><span>open review items</span></div>
      </div>
      <div className="grid2" style={{ marginTop: 12 }}>
        <div>
          <h3>Scenes</h3>
          <p className="small">Numbering: {JSON.stringify(c.scenes.number_status)}<br />
            Headings: {JSON.stringify(c.scenes.heading_status)}<br />
            Time (as written): {Object.entries(c.scenes.time_of_day).map(([k, v]) => `${k} ${v}`).join(", ")}<br />
            Relative times (inherited, flagged): {c.scenes.relative_time_scenes}</p>
          {c.pages.low_ocr_confidence.length > 0 && <p className="small error">Low OCR confidence: {c.pages.low_ocr_confidence.map((x: any) => `p${x.page}`).join(", ")}</p>}
          {c.elements.unassigned.map((x: any) => <p key={x.element_id} className="small error">Unassigned p{x.page}: {x.text}</p>)}
        </div>
        <div>
          <h3>Flagged at import</h3>
          <div className="scroll" style={{ maxHeight: 260 }}>
            {c.review_queue.items.slice(0, 60).map((i: any) => (
              <div key={i.id} className="small" style={{ marginBottom: 4 }}><span className={`badge ${i.severity}`}>{i.severity}</span> {i.message}</div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
