import { useState } from "react";
import { T, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Link2, Circle, Network, GitBranch, Server } from "../ui/Icon";

const TEMPLATES = [
    { id: "linear", Icon: Link2,      name: "Cadena Lineal",    desc: "Nodos en serie",         minCount: 2, maxCount: 10 },
    { id: "ring",   Icon: Circle,     name: "Anillo (Ring)",    desc: "Nodos en bucle cerrado", minCount: 3, maxCount: 10 },
    { id: "mesh",   Icon: Network,    name: "Malla (Full Mesh)", desc: "Todos conectados",       minCount: 3, maxCount:  6 },
    { id: "tree",   Icon: GitBranch,  name: "\u00c1rbol Binario",   desc: "Jerarqu\u00eda padre-hijo",  minCount: 3, maxCount: 15 },
    { id: "bus",    Icon: Server,     name: "Bus (Hub)",         desc: "Hub central + clientes", minCount: 2, maxCount: 10 },
];

export const TemplatePicker = () => {
    const [counts, setCounts]     = useState({ linear: 4, ring: 5, mesh: 4, tree: 7, bus: 4 });
    const [dragging, setDragging] = useState(null);

    return (
        <div style={{ padding: "12px 14px 12px", borderBottom: `1px solid ${T.border}` }}>
            <Label>Templates — arrastrar al canvas</Label>
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                {TEMPLATES.map(tpl => (
                    <div key={tpl.id}
                        style={{
                            border: `1px solid ${dragging === tpl.id ? T.accent : T.border}`,
                            borderRadius: 10, overflow: "hidden",
                            background: dragging === tpl.id ? T.accentLight : T.surface,
                            boxShadow: T.shadow, transition: "border-color 0.15s, background 0.15s",
                        }}>
                        {/* Header */}
                        <div style={{
                            padding: "6px 10px", borderBottom: `1px solid ${T.border}`,
                            background: T.surfaceElevated,
                            display: "flex", alignItems: "center", gap: 6,
                        }}>
                            <tpl.Icon size={14} color={dragging === tpl.id ? T.accent : T.textMuted} />
                            <span style={{ fontSize: 11, fontWeight: 700, color: T.text, flex: 1 }}>{tpl.name}</span>
                            <span style={{ fontSize: 9, color: T.textMuted }}>VMs:</span>
                            <select
                                value={counts[tpl.id]}
                                onChange={e => setCounts(c => ({ ...c, [tpl.id]: Number(e.target.value) }))}
                                onClick={e => e.stopPropagation()}
                                style={{ ...inp, padding: "2px 4px", width: 48, fontSize: 12, fontWeight: 700, color: T.accent, cursor: "pointer" }}
                            >
                                {Array.from(
                                    { length: tpl.maxCount - tpl.minCount + 1 },
                                    (_, i) => tpl.minCount + i
                                ).map(n => <option key={n}>{n}</option>)}
                            </select>
                        </div>

                        {/* Draggable row */}
                        <div
                            draggable
                            onDragStart={e => {
                                e.dataTransfer.setData("templateType",  tpl.id);
                                e.dataTransfer.setData("templateCount", counts[tpl.id]);
                                setDragging(tpl.id);
                            }}
                            onDragEnd={() => setDragging(null)}
                            style={{
                                padding: "7px 10px", display: "flex", alignItems: "center", gap: 8,
                                cursor: "grab",
                            }}
                        >
                            <tpl.Icon size={18} color={dragging === tpl.id ? T.accent : T.textMuted} />
                            <div>
                                <div style={{ fontSize: 11, fontWeight: 600, color: T.text }}>
                                    {counts[tpl.id]} nodos · arrastrar para colocar
                                </div>
                                <div style={{ fontSize: 10, color: T.textMuted }}>{tpl.desc}</div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};
