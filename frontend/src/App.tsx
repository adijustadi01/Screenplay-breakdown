import { useCallback, useEffect, useState } from "react";
import { api, Project } from "./api";
import { DrillProvider } from "./components/Drill";
import { DepartmentsPage } from "./features/departments/DepartmentsPage";
import { ImportPage } from "./features/import/ImportPage";
import { RevisionsPage } from "./features/revisions/RevisionsPage";
import { SchedulePage } from "./features/schedule/SchedulePage";
import { QueryPage } from "./features/query/QueryPage";
import { ReportsPage } from "./features/reports/ReportsPage";
import { ReviewPage } from "./features/review/ReviewPage";
import { ScenesPage } from "./features/scenes/ScenesPage";
import { StartPage } from "./features/start/StartPage";
import { ExportDialog } from "./components/ExportDialog";

const TABS: [string, string, string][] = [
  ["start", "Start here", ""],
  ["scenes", "Scenes", "Every scene in script order. Click a scene to see who is in it, what it needs, and the script text."],
  ["review", "To check", "Things the app wasn't sure about. Read the script passage on the right, then click Confirm if it's right, Edit to correct it, or Reject if it's wrong. Your answers update every report and the Excel file."],
  ["reports", "Cast & locations", "Summaries of characters and locations. Click any number to see the scenes behind it."],
  ["departments", "Departments", "Props, costumes, vehicles, makeup & hair, extras, animals, stunts & effects, songs and production needs. Pick a department on the left."],
  ["schedule", "Schedule & DOOD", "Put scenes into shooting days. The one-liner and Day Out of Days (DOOD) are built from your shooting days."],
  ["query", "Search", "Find scenes by combining filters, e.g. night + exterior + a character + a motorcycle."],
  ["revisions", "Compare drafts", "Import a new draft of the same film (Start here → step 1), then compare it with the old one here."],
  ["import", "Import details", "Advanced: the import check (was every page read?) and manual extraction controls."],
];

export function App() {
  const [tab, setTab] = useState(() => location.hash.slice(1) || "start");
  const [projects, setProjects] = useState<Project[]>([]);
  const [sel, setSel] = useState<{ p: number; v: number } | null>(null);
  const [openCount, setOpenCount] = useState<number | null>(null);
  const [desktop, setDesktop] = useState(false);
  // Download dialog: null = closed; otherwise the parts to tick first (null inside = everything).
  const [exporting, setExporting] = useState<{ pre: string[] | null } | null>(null);
  const openExport = useCallback((pre: string[] | null = null) => setExporting({ pre }), []);
  useEffect(() => { api.get<any>("/api/settings").then((s) => setDesktop(!!s.desktop)).catch(() => {}); }, []);

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
          <button className="btn primary" onClick={() => openExport()} title="Choose what to download, as Excel or PDF">⬇ Download Excel / PDF</button>
          {desktop && <button className="btn" title="Close Screenplay Breakdown" onClick={() => {
            if (confirm("Quit Screenplay Breakdown? Your work is saved.")) api.post("/api/quit").finally(() => window.close());
          }}>Quit</button>}
        </div>
      </header>
      <main>
        {TABS.find(([k]) => k === tab)?.[2] && <p className="tabhelp">{TABS.find(([k]) => k === tab)![2]}</p>}
        {tab === "start" && <StartPage projects={projects} versionId={sel?.v ?? null} onChanged={refresh}
          onSelect={(p, v) => setSel({ p, v })} goTo={setTab} onDownload={openExport} />}
        {tab === "import" && <ImportPage projects={projects} versionId={sel?.v ?? null} onChanged={refresh} onSelect={(p, v) => setSel({ p, v })} />}
        {tab !== "import" && tab !== "start" && !sel && <p className="muted">No script yet — go to <button className="linkbtn" onClick={() => setTab("start")}>Start here</button> and import one.</p>}
        {sel && tab === "scenes" && <ScenesPage versionId={sel.v} />}
        {sel && tab === "review" && <ReviewPage projectId={sel.p} versionId={sel.v} onChanged={refresh} />}
        {sel && tab === "reports" && <ReportsPage versionId={sel.v} onDownload={openExport} />}
        {sel && tab === "departments" && <DepartmentsPage versionId={sel.v} />}
        {sel && tab === "query" && <QueryPage versionId={sel.v} />}
        {sel && tab === "revisions" && projects.find((p) => p.id === sel.p) &&
          <RevisionsPage project={projects.find((p) => p.id === sel.p)!} versionId={sel.v} onChanged={refresh} />}
        {sel && tab === "schedule" && <SchedulePage projectId={sel.p} versionId={sel.v} />}
      </main>
      {exporting && <ExportDialog versionId={sel?.v ?? null} preselect={exporting.pre} onClose={() => setExporting(null)}
        goStart={() => setTab("start")} />}
    </DrillProvider>
  );
}
