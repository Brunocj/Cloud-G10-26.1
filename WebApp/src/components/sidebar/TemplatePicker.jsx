import { useState } from "react";
import { T, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { AzureTemplate, AzureNetwork } from "../ui/AzureIcons";

const TEMPLATES = [
    { id: "linear", name: "Cadena Lineal",    desc: "Nodos en serie",         minCount: 2, maxCount: 10 },
    { id: "ring",   name: "Anillo (Ring)",    desc: "Nodos en bucle cerrado", minCount: 3, maxCount: 10 },
    { id: "mesh",   name: "Malla (Full Mesh)", desc: "Todos conectados",       minCount: 3, maxCount:  6 },
    { id: "tree",   name: "Árbol Binario",   desc: "Jerarquía padre-hijo",  minCount: 3, maxCount: 15 },
    { id: "bus",    name: "Bus (Hub)",         desc: "Hub central + clientes", minCount: 2, maxCount: 10 },
];

export const TemplatePicker = () => {
    const [counts, setCounts]     = useState({ linear: 4, ring: 5, mesh: 4, tree: 7, bus: 4 });
    const [dragging, setDragging] = useState(null);

    return (
        <div style={{ padding: "12px 14px 12px", borderBottom: `1px solid ${T.border}` }}>
            <Label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <AzureTemplate size={16} /> Plantillas de Despliegue
            </Label>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                {TEMPLATES.map(tpl => (
                    <div key={tpl.id}
                        style={{
                            border: `1px solid ${dragging === tpl.id ? T.accent : T.border}`,
                            borderRadius: 6, overflow: "hidden",
                            background: T.surface,
                            boxShadow: "0 1.6px 3.6px 0 rgba(0,0,0,0.1), 0 0.3px 0.9px 0 rgba(0,0,0,0.1)",
                            transition: "border-color 0.15s, background 0.15s, transform 0.1s",
                            transform: dragging === tpl.id ? "scale(0.98)" : "none",
                        }}>
                        {/* Header */}
                        <div style={{
                            padding: "6px 10px", borderBottom: `1px solid ${T.border}`,
                            background: T.surfaceElevated,
                            display: "flex", alignItems: "center", gap: 6,
                        }}>
                            <AzureNetwork size={14} />
                            <span style={{ fontSize: 11, fontWeight: 600, color: T.text, flex: 1 }}>{tpl.name}</span>
                            <span style={{ fontSize: 9, color: T.textMuted, fontWeight: 700 }}>VMs:</span>
                            <select
                                value={counts[tpl.id]}
                                onChange={e => setCounts(c => ({ ...c, [tpl.id]: Number(e.target.value) }))}
                                onClick={e => e.stopPropagation()}
                                style={{ ...inp, padding: "1px 4px", width: 48, fontSize: 11, fontWeight: 700, color: T.accent, cursor: "pointer", border: `1px solid ${T.border}`, background: T.surface }}
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
                                padding: "8px 10px", display: "flex", alignItems: "center", gap: 8,
                                cursor: "grab",
                            }}
                        >
                            <AzureTemplate size={24} />
                            <div>
                                <div style={{ fontSize: 11, fontWeight: 600, color: T.text }}>
                                    {counts[tpl.id]} Nodos (Arrastrar al Lienzo)
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
