import { useState, useEffect } from "react";
import { T, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { AzureTemplate, AzureNetwork } from "../ui/AzureIcons";
import { Trash2, Globe, Users, User } from "../ui/Icon";
import { useConfirm } from "../../hooks/useConfirm";

const SCOPE_META = {
    global:   { label: "Globales",       icon: Globe },
    project:  { label: "De mi Proyecto", icon: Users },
    personal: { label: "Mis Plantillas", icon: User  },
};

const TEMPLATES = [
    { id: "linear", name: "Cadena Lineal",    desc: "Nodos en serie",         minCount: 2, maxCount: 10 },
    { id: "ring",   name: "Anillo (Ring)",    desc: "Nodos en bucle cerrado", minCount: 3, maxCount: 10 },
    { id: "mesh",   name: "Malla (Full Mesh)", desc: "Todos conectados",       minCount: 3, maxCount:  6 },
    { id: "tree",   name: "Árbol Binario",   desc: "Jerarquía padre-hijo",  minCount: 3, maxCount: 15 },
    { id: "bus",    name: "Bus (Hub)",         desc: "Hub central + clientes", minCount: 2, maxCount: 10 },
];

export const TemplatePicker = ({ apiFetch, onLoadTemplate, flash }) => {
    const [askConfirm, confirmDialog] = useConfirm();
    const [counts, setCounts]     = useState({ linear: 4, ring: 5, mesh: 4, tree: 7, bus: 4 });
    const [dragging, setDragging] = useState(null);
    const [saved, setSaved]       = useState([]);

    const loadSaved = async () => {
        if (!apiFetch) return;
        try {
            const res = await apiFetch("/slices/templates/list");
            if (res.ok) setSaved(await res.json());
        } catch { /* silencioso */ }
    };
    useEffect(() => { loadSaved(); }, [apiFetch]);

    const deleteTemplate = (tpl, e) => {
        e.stopPropagation();
        askConfirm({
            title: "Eliminar plantilla",
            msg: `¿Eliminar la plantilla «${tpl.name}»? Los slices ya desplegados a partir de ella no se ven afectados.`,
            onOk: async () => {
                const res = await apiFetch(`/slices/templates/${tpl.id}`, { method: "DELETE" });
                if (res.ok) { flash?.(`Plantilla "${tpl.name}" eliminada`); loadSaved(); }
                else flash?.("No se pudo eliminar la plantilla", "error");
            },
        });
    };

    // Agrupar por alcance en el orden del TDR: proyecto → globales → personales
    const grouped = ["project", "global", "personal"]
        .map(scope => ({ scope, items: saved.filter(t => t.scope === scope) }))
        .filter(g => g.items.length > 0);

    return (
        <div style={{ padding: "12px 14px 12px", borderBottom: `1px solid ${T.border}` }}>
            {/* ── Plantillas guardadas (REQ-US-04 / REQ-JP-03) ───────────── */}
            {grouped.map(({ scope, items }) => {
                const Meta = SCOPE_META[scope];
                return (
                    <div key={scope} style={{ marginBottom: 12 }}>
                        <Label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <Meta.icon size={12} color={T.accent} /> {Meta.label}
                        </Label>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                            {items.map(tpl => (
                                <div key={tpl.id}
                                    onClick={() => onLoadTemplate?.(tpl)}
                                    title="Clic para cargar en el lienzo (reemplaza el contenido actual)"
                                    style={{
                                        border: `1px solid ${T.border}`, borderRadius: 6,
                                        background: T.surface, padding: "7px 10px",
                                        display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                                        transition: "border-color 0.15s",
                                    }}
                                    onMouseEnter={e => e.currentTarget.style.borderColor = T.accent}
                                    onMouseLeave={e => e.currentTarget.style.borderColor = T.border}>
                                    <AzureTemplate size={20} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: 11, fontWeight: 700, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            {tpl.name}
                                        </div>
                                        <div style={{ fontSize: 9.5, color: T.textMuted }}>
                                            {tpl.nodeCount} nodos · {tpl.edgeCount} enlaces
                                            {tpl.project_name ? ` · ${tpl.project_name}` : ""}
                                        </div>
                                    </div>
                                    {tpl.can_delete && (
                                        <button onClick={(e) => deleteTemplate(tpl, e)} title="Eliminar plantilla"
                                            style={{ background: "none", border: "none", cursor: "pointer", color: T.textFaint, padding: 2, display: "flex" }}>
                                            <Trash2 size={12} />
                                        </button>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                );
            })}
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
            {confirmDialog}
        </div>
    );
};
