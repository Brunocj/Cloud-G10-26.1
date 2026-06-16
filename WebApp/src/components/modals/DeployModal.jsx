import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Overlay } from "./Overlay";
import { Zap, AlertTriangle } from "../ui/Icon";

// AZs hardcoded como fallback; se cargan dinámicamente si el backend responde
const DEFAULT_AZS = [
    { id: 1, name: "Linux Cluster" },
    { id: 2, name: "OpenStack" },
];

export const DeployModal = ({ defaultName, nodes, edges, onDeploy, onClose, imageList = [], apiFetch, targetAz }) => {
    const [name, setName] = useState(defaultName || `slice-${Math.random().toString(36).slice(2, 6)}`);
    const [selectedAzId, setSelectedAzId] = useState(targetAz ? Number(targetAz) : 1);
    const [azList, setAzList] = useState(DEFAULT_AZS);
    const [azConflict, setAzConflict] = useState(null); // mensaje de incompatibilidad o null

    const totalRam  = nodes.reduce((s, n) => s + (n.ram || 0), 0);
    const ramLabel  = totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`;

    // ── Carga dinámica de AZs desde el backend ────────────────────────────────
    useEffect(() => {
        if (!apiFetch) return;
        apiFetch("/slices/utils/availability-zones")
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (Array.isArray(data) && data.length > 0) setAzList(data);
            })
            .catch(() => {}); // fail-safe: usa DEFAULT_AZS
    }, [apiFetch]);

    // ── Validación en tiempo real: imagen ↔ AZ ────────────────────────────────
    useEffect(() => {
        if (!imageList || imageList.length === 0) {
            setAzConflict(null);
            return;
        }
        // Construimos un mapa rápido: image_id → {az_id, az_name}
        const imgMap = {};
        imageList.forEach(img => {
            imgMap[img.id] = { az_id: img.availability_zone_id, az_name: img.az_name };
        });

        // Imagen por defecto que se usará si el nodo no tiene una asignada
        const azImageList = imageList.filter(img => img.availability_zone_id == selectedAzId || img.availability_zone_id == null);
        const defaultImg = azImageList[0] ?? imageList[0] ?? { id: 1, name: "Cirros" };

        for (const node of nodes) {
            const imgId = node.image_id || defaultImg.id; // Aplicamos el mismo fallback que App.jsx
            if (!imgId) continue;
            const imgInfo = imgMap[imgId];
            if (!imgInfo || imgInfo.az_id == null) continue; // sin restricción de AZ
            if (imgInfo.az_id !== selectedAzId) {
                const nodeName = node.data?.label || node.id || node.name || "nodo";
                const azName   = imgInfo.az_name || `AZ #${imgInfo.az_id}`;
                setAzConflict(
                    `La imagen seleccionada para el nodo "${nodeName}" pertenece a "${azName}" y no es compatible con la Zona de Disponibilidad elegida.`
                );
                return;
            }
        }
        setAzConflict(null);
    }, [selectedAzId, nodes, imageList]);

    const canDeploy = name.trim() && nodes.length > 0 && !azConflict;

    return (
        <Overlay>
            <div style={{
                background: T.surface, border: `1px solid ${T.border}`,
                borderRadius: 16, padding: 28, maxWidth: 420, width: "90%",
                boxShadow: T.shadowMd,
            }}>
                {/* Header */}
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <Zap size={18} color={T.accent} /> Desplegar Slice
                </div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 20 }}>
                    Asigne un nombre, elija la Zona de Disponibilidad y confirme el despliegue.
                </div>

                {/* Nombre del Slice */}
                <Label>Nombre del Slice</Label>
                <input
                    id="deploy-slice-name"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    disabled={!!defaultName}
                    style={{ ...inp, marginBottom: 14, opacity: defaultName ? 0.7 : 1 }}
                />

                {/* Selector de Zona de Disponibilidad — bloqueado si ya se fijó en el lienzo,
                    para evitar incompatibilidades con las imágenes ya asignadas a los nodos. */}
                <Label>Zona de Disponibilidad</Label>
                <select
                    id="deploy-az-select"
                    value={selectedAzId}
                    disabled={!!targetAz}
                    onChange={e => setSelectedAzId(Number(e.target.value))}
                    style={{
                        ...inp,
                        marginBottom: 4,
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
                        <option key={az.id} value={az.id}>
                            {az.name}
                        </option>
                    ))}
                </select>
                {targetAz && (
                    <div style={{ fontSize: 10, color: T.textFaint, marginBottom: 14 }}>
                        Fijada desde el lienzo — limpia el lienzo para elegir otra zona.
                    </div>
                )}
                {!targetAz && <div style={{ height: 14 }} />}

                {/* Banner de incompatibilidad AZ ↔ Imágenes */}
                {azConflict && (
                    <div style={{
                        display: "flex", alignItems: "flex-start", gap: 8,
                        background: "#ff4d4d18", border: "1px solid #ff4d4d55",
                        borderRadius: 8, padding: "10px 12px", marginBottom: 14,
                    }}>
                        <AlertTriangle size={16} color="#ff4d4d" style={{ flexShrink: 0, marginTop: 1 }} />
                        <span style={{ fontSize: 12, color: "#ff4d4d", lineHeight: 1.5 }}>
                            {azConflict}
                        </span>
                    </div>
                )}

                {/* Summary metrics */}
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

                {/* Action buttons */}
                <div style={{ display: "flex", gap: 10 }}>
                    <button id="deploy-cancel-btn" onClick={onClose} style={btnBase({ flex: 1 })}>
                        Cancelar
                    </button>
                    <button
                        id="deploy-confirm-btn"
                        onClick={() => onDeploy(name, selectedAzId)}
                        disabled={!canDeploy}
                        title={azConflict || (nodes.length === 0 ? "Agregue al menos una VM" : "")}
                        style={btnBase({
                            flex: 2, background: T.accent, color: "#fff", border: "none",
                            opacity: canDeploy ? 1 : 0.45,
                            cursor: canDeploy ? "pointer" : "not-allowed",
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                        })}
                    >
                        <Zap size={14} /> Desplegar Ahora
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
