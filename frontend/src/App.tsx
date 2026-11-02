import { useCallback, useEffect, useState } from "react";
import { api, Project } from "./api";
import { DrillProvider } from "./components/Drill";
import { ImportPage } from "./features/import/ImportPage";
import { QueryPage } from "./features/query/QueryPage";
import { ReportsPage } from "./features/reports/ReportsPage";
import { ReviewPage } from "./features/review/ReviewPage";
import { ScenesPage } from "./features/scenes/ScenesPage";

const TABS = [["import", "Import & coverage"], ["scenes", "Scenes"], ["review", "Review queue"], ["reports", "Reports"], ["query", "Query"]];

export function App() {
  const [tab, setTab] = useState(() => location.hash.slice(1) || "import");
  const [projects, setProjects] = useState<Project[]>([]);
  const [sel, setSel] = useState<{ p: number; v: number } | null>(null);
  const [openCount, setOpenCount] = useState<number | null>(null);

  const refresh = useCallback(() => {
    api.get<Project[]>("/api/projects").then((ps) => {
      setProjects(ps);
      setSel((cur) => {
        if (cur) return cur;
        const p = ps.find((x) => x.script_versions.length);
        return p ? { p: p.id, v: p.script_versions[p.script_versions.length - 1].id } : null;
      });
    });
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => { location.hash = tab; }, [tab]);
  useEffect(() => {
    if (sel) api.get<any>(`/api/projects/${sel.p}/review?version_id=${sel.v}&limit=1`).then((d) => setOpenCount(d.total));
  }, [sel, tab, projects]);

  const versions = projects.flatMap((p) => p.script_versions.map((v) => ({ p, v })));
  return (
    <DrillProvider versionId={sel?.v ?? null}>
      <header className="top">
        <h1>Screenplay Breakdown</h1>
        <nav className="tabs">{TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
            {label}{k === "review" && openCount ? ` (${openCount})` : ""}</button>))}</nav>
        <div className="ctx small">
          Script:
          <select value={sel ? `${sel.p}:${sel.v}` : ""} onChange={(e) => { const [p, v] = e.target.value.split(":").map(Number); setSel({ p, v }); }}>
            {!sel && <option value="">—</option>}
            {versions.map(({ p, v }) => <option key={v.id} value={`${p.id}:${v.id}`}>{p.title} — {v.label || v.file} (v{v.id}, {v.status})</option>)}
          </select>
        </div>
      </header>
      <main>
        {tab === "import" && <ImportPage projects={projects} versionId={sel?.v ?? null} onChanged={refresh} onSelect={(p, v) => setSel({ p, v })} />}
        {tab !== "import" && !sel && <p className="muted">Import a script first.</p>}
        {sel && tab === "scenes" && <ScenesPage versionId={sel.v} />}
        {sel && tab === "review" && <ReviewPage projectId={sel.p} versionId={sel.v} onChanged={refresh} />}
        {sel && tab === "reports" && <ReportsPage versionId={sel.v} />}
        {sel && tab === "query" && <QueryPage versionId={sel.v} />}
      </main>
    </DrillProvider>
  );
}
