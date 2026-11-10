import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

type Section = { key: string; label: string; group: string; available: boolean; note: string | null };

const GROUPS: [string, string][] = [
  ["Main", "Main"], ["Reports", "Reports"], ["Departments", "Departments"], ["Schedule", "Schedule & DOOD"],
  ["Other", "Other"],
];

export const downloadUrl = (versionId: number, fmt: "xlsx" | "pdf", keys?: string[]) =>
  `/api/script-versions/${versionId}/export.${fmt}` + (keys ? `?sections=${encodeURIComponent(keys.join(","))}` : "");

/** "Choose what to download": tick any parts of the breakdown, then get them as Excel or PDF. */
export function ExportDialog({ versionId, preselect, onClose, goStart }: {
  versionId: number | null; preselect: string[] | null; onClose: () => void; goStart: () => void;
}) {
  const [sections, setSections] = useState<Section[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!versionId) return;
    api.get<Section[]>(`/api/script-versions/${versionId}/export/sections`).then((s) => {
      setSections(s);
      const available = new Set(s.filter((x) => x.available).map((x) => x.key));
      // A preselected part with nothing in it yet (e.g. no ages split) is left unticked.
      setPicked(new Set((preselect ?? [...available]).filter((k) => available.has(k))));
    }).catch((e) => setErr(String(e.message ?? e)));
  }, [versionId, preselect]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const keys = useMemo(() => (sections ?? []).filter((s) => picked.has(s.key)).map((s) => s.key), [sections, picked]);
  const toggle = (k: string) => setPicked((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const setMany = (ks: string[], on: boolean) => setPicked((p) => {
    const n = new Set(p); ks.forEach((k) => (on ? n.add(k) : n.delete(k))); return n;
  });

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Download" onClick={(e) => e.stopPropagation()}>
        <header>
          <b style={{ flex: 1 }}>⬇ Download your breakdown</b>
          <button className="btn" onClick={onClose}>Close</button>
        </header>
        <div className="body">
          {!versionId ? (
            <p>There is no script yet. Go to <button className="linkbtn" onClick={() => { onClose(); goStart(); }}>Start here</button>,
              choose your script and click <b>Break down my script</b>. Then come back to download.</p>
          ) : err ? <p className="error">{err}</p> : !sections ? <p className="muted">Loading…</p> : (
            <>
              <p className="small">Tick what you want in the file. Everything that has something in it is ticked to start with.</p>
              <div className="row small" style={{ marginBottom: 8 }}>
                <button className="btn" onClick={() => setMany(sections.map((s) => s.key), true)}>Select all</button>
                <button className="btn" onClick={() => setPicked(new Set())}>Clear all</button>
              </div>
              <div className="exgroups">
                {GROUPS.map(([g, title]) => {
                  const list = sections.filter((s) => s.group === g);
                  if (!list.length && g !== "Schedule") return null;
                  const all = list.length > 0 && list.every((s) => picked.has(s.key));
                  return (
                    <fieldset key={g} className="exgroup">
                      <legend>
                        <label><input type="checkbox" checked={all} disabled={!list.length}
                          onChange={(e) => setMany(list.map((s) => s.key), e.target.checked)} /> <b>{title}</b></label>
                      </legend>
                      {!list.length && <p className="small muted">No schedule made yet (Schedule & DOOD tab).</p>}
                      {list.map((s) => (
                        <label key={s.key} className={`exitem ${s.available ? "" : "muted"}`}>
                          <input type="checkbox" checked={picked.has(s.key)} onChange={() => toggle(s.key)} />
                          <span>{s.label}{s.note && <span className="small muted"> — {s.note}</span>}</span>
                        </label>
                      ))}
                    </fieldset>
                  );
                })}
              </div>
            </>
          )}
        </div>
        {versionId && sections && (
          <footer>
            <span className="small muted" style={{ flex: 1 }}>{keys.length} part{keys.length === 1 ? "" : "s"} selected</span>
            <a className={`btn primary big ${keys.length ? "" : "disabled"}`} href={keys.length ? downloadUrl(versionId, "xlsx", keys) : undefined}
              download title="One sheet per part, with filters">⬇ Excel</a>
            <a className={`btn primary big ${keys.length ? "" : "disabled"}`} href={keys.length ? downloadUrl(versionId, "pdf", keys) : undefined}
              download title="Ready to print or send (A4 landscape)">⬇ PDF</a>
          </footer>
        )}
      </div>
    </div>
  );
}
