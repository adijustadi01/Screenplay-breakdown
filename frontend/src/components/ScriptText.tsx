export type ScriptEl = { id: number; type: string; text: string; page?: string | null; highlight?: boolean };

/** Renders script elements in screenplay layout, highlighting the supporting lines. */
export function ScriptText({ elements, highlight = [] }: { elements: ScriptEl[]; highlight?: number[] }) {
  return (
    <div className="script">
      {elements.map((e) => (
        <p key={e.id} className={`el ${e.type} ${e.highlight || highlight.includes(e.id) ? "hl" : ""}`}
           title={e.page ? `page ${e.page}` : undefined}>{e.text}</p>
      ))}
    </div>
  );
}
