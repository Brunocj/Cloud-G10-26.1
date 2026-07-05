import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Overlay } from "./Overlay";
import { Zap, AlertTriangle, CheckCircle, Send } from "../ui/Icon";

const DEFAULT_AZS = [
    { id: 1, name: "Linux Cluster" },
    { id: 2, name: "OpenStack" },
];

const NO_PROJECT = "__NONE__";

export const DeployModal = ({ defaultName, nodes, edges, onDeploy, onClose, imageList = [], apiFetch, targetAz, userRole }) => {
    const [name, setName] = useState(defaultName || `slice-${Math.random().toString(36).slice(2, 6)}`);
    const [selectedAzId, setSelectedAzId] = useState(targetAz ? Number(targetAz) : 1);
    const [azList, setAzList] = useState(DEFAULT_AZS);
    const [azConflict, setAzConflict] = useState(null);

    // Project selection
    const [eligibleProjects, setEligibleProjects] = useState([]);
    const [selectedProject, setSelectedProject] = useState(NO_PROJECT);
    const [projectsLoaded, setProjectsLoaded] = useState(false);

    const totalRam  = nodes.reduce((s, n) => s + (n.ram || 0), 0);
    const ramLabel  = totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`;

    // ── Load AZs ───────────────────────────────────────────────────────────
    useEffect(() => {
        if (!apiFetch) return;
        apiFetch("/slices/utils/availability-zones")
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (Array.isArray(data) && data.length > 0) setAzList(data);
            })
            .catch(() => {});
    }, [apiFetch]);

    // ── Load eligible projects ─────────────────────────────────────────────
    useEffect(() => {
        if (!apiFetch) return;
        apiFetch("/projects/eligible-for-deploy")
            .then(r => r.ok ? r.json() : [])
            .then(data => {
                if (Array.isArray(data)) setEligibleProjects(data);
            })
            .catch(() => {})
            .finally(() => setProjectsLoaded(true));
    }, [apiFetch]);

    // ── Image ↔ AZ validation ──────────────────────────────────────────────
    useEffect(() => {
        if (!imageList || imageList.length === 0) { setAzConflict(null); return; }
        const imgMap = {};
        for (const img of imageList) {
            imgMap[img.id] = { az_id: img.availability_zone_id, az_name: img.az_name };
        }
        const targetAzObj = azList.find(a => a.id === selectedAzId);
        const targetAzName = targetAzObj?.name;

        for (const node of nodes) {
            const info = imgMap[node.image_id];
            if (!info || !info.az_id) continue;
            if (info.az_id !== selectedAzId) {
                setAzConflict(
                    `La VM "${node.id}" usa la imagen "${node.image}" que pertenece a ${info.az_name}, pero seleccionaste ${targetAzName}.`
                );
                return;
            }
        }
        setAzConflict(null);
    }, [selectedAzId, nodes, imageList, azList]);

    // ── Determine if selected project is direct-deploy ─────────────────────
    const chosenProjectMeta = selectedProject === NO_PROJECT
        ? null
        : eligibleProjects.find(p => String(p.project_id) === String(selectedProject));

    // Espeja la lógica del backend (can_deploy_directly):
    //  - admin/superAdmin: siempre directo
    //  - jefeProyecto: directo solo si es jefe del proyecto elegido
    //  - resto (incluye "sin proyecto" para no-admins): requiere aprobación
    const isAdminGlobal = userRole === "admin" || userRole === "superAdmin";
    const isDirect = isAdminGlobal
        ? true
        : (chosenProjectMeta ? chosenProjectMeta.direct_deploy : false);

    const canDeploy = !azConflict && nodes.length > 0 && name.trim() && projectsLoaded;

    const handleSubmit = () => {
        const projectId = selectedProject === NO_PROJECT ? null : Number(selectedProject);
        onDeploy(name, selectedAzId, projectId, isDirect);
    };

    return (
        <Overlay>
            <div style={{
                background: T.surface, borderRadius: 14, width: 460,
                border: `1px solid ${T.border}`, boxShadow: "0 12px 40px rgba(0,0,0,0.25)",
                padding: "24px 26px",
            }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4 }}>
                    Desplegar Slice
                </div>
                <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 16 }}>
                    Configura el destino y confirma el despliegue.
                </div>

                {/* Name */}
                <Label>Nombre del Slice</Label>
                <input value={name} onChange={e => setName(e.target.value)}
                    style={{ ...inp, marginBottom: 14 }} />

                {/* AZ */}
                <Label>Zona de Disponibilidad</Label>
                <select
                    value={selectedAzId}
                    disabled={!!targetAz}
                    onChange={e => setSelectedAzId(Number(e.target.value))}
                    style={{
                        ...inp, marginBottom: 4,
                        cursor: targetAz ? "not-allowed" : "pointer",
                        opacity: targetAz ? 0.6 : 1,
                        appearance: "none",
                        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E")`,
                        backgroundRepeat: "no-repeat",
                        backgroundPosition: "right 10px center",
                        paddingRight: 28,
                    }}
                >
                    {azList.map(az => (
                        <option key={az.id} value={az.id}>{az.name}</option>
                    ))}
                </select>
                {targetAz
                    ? <div style={{ fontSize: 10, color: T.textFaint, marginBottom: 14 }}>
                        Fijada desde el lienzo — limpia el lienzo para elegir otra zona.
                      </div>
                    : <div style={{ height: 14 }} />
                }

                {/* Project selector */}
                <Label>Proyecto</Label>
                <select
                    value={selectedProject}
                    onChange={e => setSelectedProject(e.target.value)}
                    style={{
                        ...inp, marginBottom: 6,
                        appearance: "none",
                        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E")`,
                        backgroundRepeat: "no-repeat",
                        backgroundPosition: "right 10px center",
                        paddingRight: 28,
                    }}
                >
                    <option value={NO_PROJECT}>Sin proyecto (slice personal)</option>
                    {eligibleProjects.map(p => (
                        <option key={p.project_id} value={p.project_id}>
                            {p.project_name} {p.direct_deploy ? "· despliegue directo" : "· requiere aprobación"}
                        </option>
                    ))}
                </select>

                {/* Direct vs. approval hint */}
                <div style={{
                    display: "flex", alignItems: "center", gap: 8, marginBottom: 14,
                    padding: "8px 12px", borderRadius: 8, fontSize: 11,
                    background: isDirect ? "#16a34a15" : "#f59e0b18",
                    color: isDirect ? "#16a34a" : "#a16207",
                    border: `1px solid ${isDirect ? "#16a34a44" : "#f59e0b44"}`,
                }}>
                    {isDirect
                        ? <><CheckCircle size={13} /> El slice se desplegará inmediatamente.</>
                        : <><Send size={13} /> La solicitud quedará pendiente de aprobación.</>
                    }
                </div>

                {/* AZ conflict banner */}
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
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>
                        Cancelar
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={!canDeploy}
                        title={azConflict || (nodes.length === 0 ? "Agregue al menos una VM" : "")}
                        style={btnBase({
                            flex: 2, background: T.accent, color: "#fff", border: "none",
                            opacity: canDeploy ? 1 : 0.45,
                            cursor: canDeploy ? "pointer" : "not-allowed",
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                        })}
                    >
                        {isDirect
                            ? <><Zap size={14} /> Desplegar Ahora</>
                            : <><Send size={14} /> Enviar Solicitud</>
                        }
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
