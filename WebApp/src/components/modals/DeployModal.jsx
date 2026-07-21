import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Overlay } from "./Overlay";
import { Zap, AlertTriangle, CheckCircle, Send, Users, Clock } from "../ui/Icon";

const DEFAULT_AZS = [
    { id: 1, name: "Linux Cluster" },
    { id: 2, name: "OpenStack" },
];

const NO_PROJECT = "__NONE__";

// Opciones de TTL en horas (REQ-US-08)
const TTL_OPTIONS = [1, 2, 4, 8, 12, 24, 48, 72, 168];

export const DeployModal = ({ defaultName, nodes, edges, onDeploy, onBulkDeploy, onClose, imageList = [], apiFetch, targetAz, userRole }) => {
    const [name, setName] = useState(defaultName || `slice-${Math.random().toString(36).slice(2, 6)}`);
    const [selectedAzId, setSelectedAzId] = useState(targetAz ? Number(targetAz) : 1);
    const [azList, setAzList] = useState(DEFAULT_AZS);
    const [azConflict, setAzConflict] = useState(null);

    // TTL + motivo (REQ-US-08): roles elevados arrancan con TTL ilimitado,
    // usuarios normales con 4h. Ambos pueden cambiarlo; el motivo es obligatorio.
    const isElevated = ["jefeProyecto", "admin", "superAdmin"].includes(userRole);
    const [unlimited, setUnlimited] = useState(isElevated);
    const [ttlHours,  setTtlHours]  = useState(4);
    const [motivo,    setMotivo]    = useState("");

    // Project selection
    const [eligibleProjects, setEligibleProjects] = useState([]);
    const [selectedProject, setSelectedProject] = useState(NO_PROJECT);
    const [projectsLoaded, setProjectsLoaded] = useState(false);

    // Bulk deploy toggle
    const [bulkMode, setBulkMode] = useState(false);
    const [bulkDeploying, setBulkDeploying] = useState(false);

    const totalRam  = nodes.reduce((s, n) => s + (n.ram || 0), 0);
    const ramLabel  = totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`;

    // Load AZs
    useEffect(() => {
        if (!apiFetch) return;
        apiFetch("/slices/utils/availability-zones")
            .then(r => r.ok ? r.json() : null)
            .then(data => { if (Array.isArray(data) && data.length > 0) setAzList(data); })
            .catch(() => {});
    }, [apiFetch]);

    // Load eligible projects
    useEffect(() => {
        if (!apiFetch) return;
        apiFetch("/projects/eligible-for-deploy")
            .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
            .then(data => { if (Array.isArray(data)) setEligibleProjects(data); })
            .catch(e => console.error("eligible-for-deploy failed:", e))
            .finally(() => setProjectsLoaded(true));
    }, [apiFetch]);

    // Image ↔ AZ validation
    useEffect(() => {
        if (!imageList || imageList.length === 0) { setAzConflict(null); return; }
        const imgMap = {};
        for (const img of imageList) imgMap[img.id] = { az_id: img.availability_zone_id, az_name: img.az_name };
        const targetAzName = azList.find(a => a.id === selectedAzId)?.name;
        for (const node of nodes) {
            const info = imgMap[node.image_id];
            if (!info || !info.az_id) continue;
            if (info.az_id !== selectedAzId) {
                setAzConflict(`La VM "${node.id}" usa la imagen "${node.image}" que pertenece a ${info.az_name}, pero seleccionaste ${targetAzName}.`);
                return;
            }
        }
        setAzConflict(null);
    }, [selectedAzId, nodes, imageList, azList]);

    // Determine deploy mode
    const chosenProjectMeta = selectedProject === NO_PROJECT
        ? null
        : eligibleProjects.find(p => String(p.project_id) === String(selectedProject));

    const isAdminGlobal = userRole === "admin" || userRole === "superAdmin";
    const isDirect = isAdminGlobal ? true : (chosenProjectMeta ? chosenProjectMeta.direct_deploy : false);

    // Bulk deploy is available when a direct-deploy project is selected
    const canBulk = chosenProjectMeta?.direct_deploy === true && onBulkDeploy;

    // Reset bulk mode when project changes
    useEffect(() => { if (!canBulk) setBulkMode(false); }, [selectedProject]);

    // El motivo solo se exige a quien necesita aprobación de un tercero.
    const canDeploy = !azConflict && nodes.length > 0 && name.trim()
        && (isAdminGlobal || motivo.trim()) && projectsLoaded && !bulkDeploying;

    const handleSubmit = async () => {
        const projectId = selectedProject === NO_PROJECT ? null : Number(selectedProject);
        const effectiveTtl = unlimited ? 0 : ttlHours;   // 0 = persistente (sin expiración)
        // `motivo: str` es obligatorio en el schema del SliceManager y admin puede
        // dejarlo vacío, así que se sustituye por un texto legible en vez de ""
        // para que la entrada de bitácora no quede sin justificación.
        const motivoFinal = motivo.trim() || "Despliegue directo por administrador";
        if (bulkMode && onBulkDeploy) {
            setBulkDeploying(true);
            await onBulkDeploy(name, selectedAzId, projectId, effectiveTtl, motivoFinal);
            setBulkDeploying(false);
        } else {
            onDeploy(name, selectedAzId, projectId, isDirect, effectiveTtl, motivoFinal);
        }
    };

    // Select dropdown style
    const selectStyle = {
        ...inp, marginBottom: 6,
        appearance: "none",
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E")`,
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 10px center",
        paddingRight: 28,
    };

    return (
        <Overlay>
            <div style={{
                background: T.surface, borderRadius: 14, width: 460,
                border: `1px solid ${T.border}`, boxShadow: "0 12px 40px rgba(0,0,0,0.25)",
                padding: "24px 26px",
            }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4 }}>
                    {bulkMode ? "Despliegue Masivo" : "Desplegar Slice"}
                </div>
                <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 16 }}>
                    {bulkMode
                        ? "Se creará un slice para cada miembro del proyecto."
                        : "Configura el destino y confirma el despliegue."}
                </div>

                {/* Name (prefix in bulk mode) */}
                <Label>{bulkMode ? "Prefijo de nombre" : "Nombre del Slice"}</Label>
                <input value={name} onChange={e => setName(e.target.value)}
                    placeholder={bulkMode ? "ej: lab1 → lab1-abc123" : ""}
                    style={{ ...inp, marginBottom: 14 }} />

                {/* AZ */}
                <Label>Zona de Disponibilidad</Label>
                <select value={selectedAzId} disabled={!!targetAz}
                    onChange={e => setSelectedAzId(Number(e.target.value))}
                    style={{ ...selectStyle, marginBottom: 4, cursor: targetAz ? "not-allowed" : "pointer", opacity: targetAz ? 0.6 : 1 }}>
                    {azList.map(az => <option key={az.id} value={az.id}>{az.name}</option>)}
                </select>
                {targetAz
                    ? <div style={{ fontSize: 10, color: T.textFaint, marginBottom: 14 }}>Fijada desde el lienzo.</div>
                    : <div style={{ height: 14 }} />}

                {/* Project */}
                <Label>Proyecto</Label>
                <select value={selectedProject} onChange={e => setSelectedProject(e.target.value)}
                    style={selectStyle}>
                    <option value={NO_PROJECT}>Sin proyecto (slice personal)</option>
                    {eligibleProjects.map(p => (
                        <option key={p.project_id} value={p.project_id}>
                            {p.project_name} {p.direct_deploy ? "· despliegue directo" : "· requiere aprobación"}
                        </option>
                    ))}
                </select>

                {/* TTL (REQ-US-08) */}
                <Label>Tiempo de Vida (TTL)</Label>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                    <select value={ttlHours} disabled={unlimited}
                        onChange={e => setTtlHours(Number(e.target.value))}
                        style={{ ...selectStyle, marginBottom: 0, flex: 1, opacity: unlimited ? 0.45 : 1, cursor: unlimited ? "not-allowed" : "pointer" }}>
                        {TTL_OPTIONS.map(h => (
                            <option key={h} value={h}>
                                {h < 24 ? `${h} hora${h > 1 ? "s" : ""}` : `${h / 24} día${h > 24 ? "s" : ""} (${h}h)`}
                            </option>
                        ))}
                    </select>
                    <label style={{
                        display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 700,
                        color: unlimited ? T.accent : T.textMuted, cursor: "pointer", whiteSpace: "nowrap",
                        padding: "8px 10px", borderRadius: 8,
                        background: unlimited ? T.accentLight : T.surfaceElevated,
                        border: `1px solid ${unlimited ? T.accent + "66" : T.border}`,
                    }}>
                        <input type="checkbox" checked={unlimited} onChange={e => setUnlimited(e.target.checked)}
                            style={{ accentColor: T.accent, cursor: "pointer" }} />
                        TTL Ilimitado
                    </label>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: T.textFaint, marginBottom: 12 }}>
                    <Clock size={11} />
                    {unlimited
                        ? "El slice será persistente: no se destruirá automáticamente."
                        : `El slice se destruirá automáticamente ${ttlHours}h después de activarse.`}
                </div>

                {/* Motivo — obligatorio solo si la solicitud la aprueba alguien más.
                    admin y superAdmin despliegan de forma directa (isAdminGlobal ⇒
                    isDirect), así que no hay aprobador que lea el motivo: para
                    ellos el campo queda como registro opcional en la bitácora. */}
                <Label>{isAdminGlobal ? "Motivo (opcional)" : "Motivo de la solicitud"}</Label>
                <textarea value={motivo} onChange={e => setMotivo(e.target.value)}
                    rows={2}
                    placeholder={isAdminGlobal
                        ? 'Opcional — queda registrado en la bitácora'
                        : 'Ej. "Laboratorio 3 de Redes" o "Para mi Tesis"'}
                    style={{
                        ...inp, marginBottom: (isAdminGlobal || motivo.trim()) ? 12 : 4,
                        resize: "vertical", minHeight: 44, fontFamily: "inherit",
                    }} />
                {!isAdminGlobal && !motivo.trim() && (
                    <div style={{ fontSize: 10, color: T.textFaint, marginBottom: 12 }}>
                        Campo obligatorio — visible para quien apruebe la solicitud.
                    </div>
                )}

                {/* Bulk toggle — only when direct-deploy project selected */}
                {canBulk && (
                    <button onClick={() => setBulkMode(v => !v)}
                        style={{
                            width: "100%", padding: "8px 12px", marginBottom: 6, marginTop: 4,
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                            background: bulkMode ? T.accent : T.surfaceElevated,
                            color: bulkMode ? "#fff" : T.accent,
                            border: `1px solid ${bulkMode ? T.accent : T.accent + "44"}`,
                            borderRadius: 8, fontSize: 11, fontWeight: 700,
                            cursor: "pointer", fontFamily: "inherit",
                            transition: "all 0.15s",
                        }}>
                        <Users size={13} />
                        {bulkMode ? "✓ Despliegue masivo activado" : "Activar despliegue masivo para el proyecto"}
                    </button>
                )}

                {/* Status hint */}
                <div style={{
                    display: "flex", alignItems: "center", gap: 8, marginBottom: 14, marginTop: 6,
                    padding: "8px 12px", borderRadius: 8, fontSize: 11,
                    background: bulkMode ? "#7c3aed15" : (isDirect ? "#16a34a15" : "#f59e0b18"),
                    color: bulkMode ? "#7c3aed" : (isDirect ? "#16a34a" : "#a16207"),
                    border: `1px solid ${bulkMode ? "#7c3aed44" : (isDirect ? "#16a34a44" : "#f59e0b44")}`,
                }}>
                    {bulkMode
                        ? <><Users size={13} /> Un slice por cada miembro del proyecto (excluyéndote a ti).</>
                        : isDirect
                            ? <><CheckCircle size={13} /> El slice se desplegará inmediatamente.</>
                            : <><Send size={13} /> La solicitud quedará pendiente de aprobación.</>
                    }
                </div>

                {/* AZ conflict */}
                {azConflict && (
                    <div style={{
                        display: "flex", alignItems: "flex-start", gap: 8,
                        background: "#ff4d4d18", border: "1px solid #ff4d4d55",
                        borderRadius: 8, padding: "10px 12px", marginBottom: 14,
                    }}>
                        <AlertTriangle size={16} color="#ff4d4d" style={{ flexShrink: 0, marginTop: 1 }} />
                        <span style={{ fontSize: 12, color: "#ff4d4d", lineHeight: 1.5 }}>{azConflict}</span>
                    </div>
                )}

                {/* Metrics */}
                <div style={{
                    display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10,
                    margin: "0 0 18px", background: T.accentLight, borderRadius: 10,
                    padding: "14px 12px", border: `1px solid ${T.accent}33`,
                }}>
                    {[
                        ["VMs",     nodes.length],
                        ["Enlaces", edges.length],
                        ["vCPU",    nodes.reduce((s, n) => s + (n.vcores || 0), 0)],
                        ["RAM",     ramLabel],
                    ].map(([l, v]) => (
                        <div key={l} style={{ textAlign: "center" }}>
                            <div style={{ fontSize: 20, fontWeight: 900, color: T.accent }}>{v}</div>
                            <div style={{ fontSize: 9, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                        </div>
                    ))}
                </div>

                {/* Actions */}
                <div style={{ display: "flex", gap: 10 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancelar</button>
                    <button onClick={handleSubmit} disabled={!canDeploy}
                        title={azConflict || (nodes.length === 0 ? "Agregue al menos una VM" : "")}
                        style={btnBase({
                            flex: 2, background: bulkMode ? "#7c3aed" : T.accent, color: "#fff", border: "none",
                            opacity: canDeploy ? 1 : 0.45,
                            cursor: canDeploy ? "pointer" : "not-allowed",
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                        })}>
                        {bulkDeploying
                            ? "Desplegando..."
                            : bulkMode
                                ? <><Users size={14} /> Desplegar para Todos</>
                                : isDirect
                                    ? <><Zap size={14} /> Desplegar Ahora</>
                                    : <><Send size={14} /> Enviar Solicitud</>
                        }
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
