import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

interface TableColumn {
  key: string;
  label: string;
  align?: "left" | "center" | "right";
  monospace?: boolean;
}

export function TablePrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const columns = (Array.isArray(data.columns) ? data.columns : []) as TableColumn[];
  const rows = (Array.isArray(data.rows) ? data.rows : []) as Record<string, unknown>[];

  if (!columns.length || !rows.length) {
    return <div className="text-xs text-slate-500 italic my-2">No table records available.</div>;
  }

  return (
    <div className="w-full overflow-x-auto my-2 rounded-xl border border-cyan-500/20 bg-slate-950/60">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="border-b border-cyan-500/20 bg-cyan-950/30">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`p-2.5 font-mono text-[11px] font-semibold text-cyan-300 uppercase tracking-wider ${
                  col.align === "right" ? "text-right" : col.align === "center" ? "text-center" : "text-left"
                }`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-cyan-500/10">
          {rows.map((row, rIdx) => (
            <tr key={rIdx} className="hover:bg-cyan-900/15 transition-colors">
              {columns.map((col) => {
                const val = row[col.key];
                const displayVal = val !== undefined && val !== null ? String(val) : "-";
                return (
                  <td
                    key={col.key}
                    className={`p-2.5 text-slate-200 ${
                      col.monospace ? "font-mono text-[11px] text-cyan-200" : ""
                    } ${
                      col.align === "right"
                        ? "text-right"
                        : col.align === "center"
                          ? "text-center"
                          : "text-left"
                    }`}
                  >
                    {displayVal}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
