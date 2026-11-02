import { Cell } from "../api";
import { useDrill } from "./Drill";

/** A number that opens the exact scenes behind it. */
export function CountCell({ cell, title }: { cell?: Cell; title: string }) {
  const drill = useDrill();
  if (!cell || cell.count === 0) return <span className="cellbtn zero">{cell ? 0 : ""}</span>;
  return (
    <button className="cellbtn" title={`Show the ${cell.count} scene(s)`} onClick={() => drill(title, cell.scene_ids)}>
      {cell.count}
    </button>
  );
}
