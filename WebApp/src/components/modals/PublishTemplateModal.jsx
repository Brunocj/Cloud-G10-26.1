import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { X } from "../ui/Icon";

/**
 * PublishTemplateModal (REQ-JP-03 / REQ-AD-05).
 * Publica la topología de un slice como plantilla:
 *   personal → cualquiera (sobre sus slices)
 *   project  → jefeProyecto (elige uno de sus proyectos liderados) o admin
 *   global   → solo admin/superAdmin (backend valida imágenes generales)
 */
export const PublishTemplateModal = ({ sliceName, userRole, apiFetch, onConfirm, onClose }) => {
    const [name, setName]   = useState(`${sliceName ?? "Plantilla"}`);
    const [scope, setScope] = useState("personal");
    const [projectId, setProjectId] = useState("");
    const [ledProjects, setLedProjects] = useState([]);
    const [busy, setBusy] = useState(false);

    const isAdmin = userRole === "admin" || userRole === "superAdmin";
    const isJefe  = userRole === "jefeProyecto";

    useEffect(() => {
        if (!apiFetch) return;
        // Proyectos donde puede publicar: los de despliegue directo (liderados / todos si admin)
        apiFetch("/projects/eligible-for-deploy")
            .then(r => r.ok ? r.json() : [])
            .then(data => setLedProjects((data || []).filter(p => p.direct_deploy)))
            .catch(() => {});
    }, [apiFetch]);

    const scopes = [
        { id: "personal", label: "⭐ Plantilla Personal", desc: "Solo visible para ti." },
        ...(isJefe || isAdmin ? [{ id: "project", label: "👥 Plantilla de Proyecto", desc: "Visible para todos los miembros del proyecto elegido." }] : []),
        ...(isAdmin ? [{ id: "global", label: "🌎 Plantilla Global", desc: "Catálogo oficial de todos los usuarios. Solo imágenes generales." }] : []),
    ];

    const canSubmit = name.trim() && !(scope === "project" && !projectId) && !busy;

    const submit = async () => {
        setBusy(true);
        await onConfirm({
            name: name.trim(),
            scope,
            project_id: scope === "project" ? Number(projectId) : null,
        });
        setBusy(false);
    };

    return (
        <Overlay>
            <div style={{ background: T.surface, borderRadius: 14, width: 440, border: `1px solid ${T.border}`, padding: "24px 26px", boxShadow: "0 12px 40px rgba(0,0,0,0.25)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
                    <div style={{ fontSize: 16, fontWeight: 800, color: T.text }}>⭐ Publicar como Plantilla</div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Nombre de la Plantilla</label>
                <input value={name} onChange={e => setName(e.target.value)}
                    placeholder='Ej. "Lab 2 - OSPF Base"'
                    style={{ ...inp, marginTop: 5, marginBottom: 14 }} />

                <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Alcance</label>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6, marginBottom: 14 }}>
                    {scopes.map(s => (
                        <label key={s.id} style={{
                            display: "flex", alignItems: "flex-start", gap: 8, padding: "9px 12px",
                            borderRadius: 8, cursor: "pointer",
                            background: scope === s.id ? T.accentLight : T.surfaceElevated,
                            border: `1px solid ${scope === s.id ? T.accent + "66" : T.border}`,
                        }}>
                            <input type="radio" checked={scope === s.id} onChange={() => setScope(s.id)}
                                style={{ accentColor: T.accent, marginTop: 2 }} />
                            <span>
                                <span style={{ fontSize: 12, fontWeight: 700, color: T.text, display: "block" }}>{s.label}</span>
                                <span style={{ fontSize: 10.5, color: T.textMuted }}>{s.desc}</span>
                            </span>
                        </label>
                    ))}
                </div>

                {scope === "project" && (
                    <>
                        <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Proyecto destino</label>
                        <select value={projectId} onChange={e => setProjectId(e.target.value)}
                            style={{ ...inp, marginTop: 5, marginBottom: 14 }}>
                            <option value="">— Elige un proyecto —</option>
                            {ledProjects.map(p => <option key={p.project_id} value={p.project_id}>{p.project_name}</option>)}
                        </select>
                    </>
                )}

                <div style={{ display: "flex", gap: 10 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancelar</button>
                    <button onClick={submit} disabled={!canSubmit}
                        style={btnBase({
                            flex: 2, background: T.accent, color: "#fff", border: "none",
                            opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? "pointer" : "not-allowed",
                        })}>
                        {busy ? "Publicando…" : "Publicar Plantilla"}
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
