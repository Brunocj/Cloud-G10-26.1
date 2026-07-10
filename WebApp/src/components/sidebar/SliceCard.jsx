import { useEffect, useState } from "react";
import { T, btnBase } from "../../theme/tokens";
import { Badge } from "../ui/Badge";
import { Trash2, Zap, Clock, AlertTriangle, User, FolderOpen } from "../ui/Icon";

// ─── TTL restante (REQ-US-09: columna "Tiempo Restante") ─────────────────────
// date_deployed viene en UTC "YYYY-MM-DD HH:MM:SS"; ttl_hours en horas.
const computeRemaining = (slice) => {
    if (slice.status !== "ACTIVE" || !slice.ttl_hours || !slice.date_deployed) return null;
    const deployed = new Date(slice.date_deployed.replace(" ", "T") + "Z");
    if (isNaN(deployed)) return null;
    const expiresAt = deployed.getTime() + slice.ttl_hours * 3600 * 1000;
    return expiresAt - Date.now();   // ms (puede ser negativo si ya venció)
};

const fmtRemaining = (ms) => {
    if (ms <= 0) return "expirando…";
    const totalMin = Math.floor(ms / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
    if (h > 0)   return `${h}h ${m}m`;
    return `${m}m`;
};

const TtlCountdown = ({ slice }) => {
    const [remaining, setRemaining] = useState(() => computeRemaining(slice));

    useEffect(() => {
        setRemaining(computeRemaining(slice));
        const t = setInterval(() => setRemaining(computeRemaining(slice)), 30000);
        return () => clearInterval(t);
    }, [slice.status, slice.ttl_hours, slice.date_deployed]);

    if (remaining === null) return null;
    const urgent = remaining < 30 * 60000;   // < 30 min

    return (
        <div style={{
            display: "flex", alignItems: "center", gap: 5, marginTop: 8,
            fontSize: 10, fontWeight: 700, borderRadius: 6, padding: "4px 8px",
            color: urgent ? T.red : T.textMuted,
            background: urgent ? T.redLight : T.surfaceElevated,
            border: `1px solid ${urgent ? T.red + "44" : T.border}`,
        }}>
            <Clock size={11} />
            TTL: {fmtRemaining(remaining)} restante{remaining <= 0 ? "" : "s"}
        </div>
    );
};

export const SliceCard = ({ slice, active, onClick, onDestroy, onDeploy, onPublish, showOwner = false }) => (
    <div
        onClick={onClick}
        style={{
            padding: "12px 14px", borderRadius: 10, cursor: "pointer",
            border: `1.5px solid ${active ? T.accent : T.border}`,
            background: active ? T.accentLight : T.surface,
            transition: "all 0.15s",
            boxShadow: active ? `0 0 0 3px ${T.accent}18` : T.shadow,
        }}>
        {/* Name + badge + destroy btn */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6 }}>
            <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{slice.name}</div>
                <Badge status={slice.status} />
                {/* Dueño + proyecto — solo para roles con visibilidad transversal */}
                {showOwner && (slice.owner_name || slice.project_name) && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 6 }}>
                        {slice.owner_name && (
                            <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: T.textMuted }}>
                                <User size={10} /> {slice.owner_name}
                            </span>
                        )}
                        {slice.project_name && (
                            <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: T.textMuted }}>
                                <FolderOpen size={10} /> {slice.project_name}
                            </span>
                        )}
                    </div>
                )}
            </div>
            <div style={{ display: "flex", gap: 2 }}>
                {onPublish && slice.nodeCount > 0 && (
                    <button
                        onClick={e => { e.stopPropagation(); onPublish(slice.id); }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4, display: "flex", fontSize: 13 }}
                        title="Publicar como Plantilla">
                        ⭐
                    </button>
                )}
                {slice.status !== "TERMINATED" && (
                    <button
                        onClick={e => { e.stopPropagation(); onDestroy(slice.id); }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4, display: "flex" }}
                        title="Eliminar slice">
                        <Trash2 size={15} />
                    </button>
                )}
            </div>
        </div>

        {/* Stats grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 5, marginTop: 6 }}>
            {[["VMs", slice.nodeCount], ["Enlaces", slice.edgeCount], ["vCPU", slice.vcpus], ["RAM", slice.ramLabel]].map(([l, v]) => (
                <div key={l} style={{ textAlign: "center", background: T.surfaceElevated, borderRadius: 6, padding: "5px 2px", border: `1px solid ${T.border}` }}>
                    <div style={{ fontSize: 13, fontWeight: 800, color: T.accent }}>{v}</div>
                    <div style={{ fontSize: 8, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                </div>
            ))}
        </div>

        {/* Tiempo restante de vida (solo ACTIVE con TTL) */}
        <TtlCountdown slice={slice} />

        {/* Fechas de ciclo de vida */}
        {(slice.status === "ACTIVE" && slice.date_deployed) && (
            <div style={{ fontSize: 9.5, color: T.textFaint, marginTop: 6 }}>
                Desplegado: {slice.date_deployed} UTC
            </div>
        )}
        {(slice.status === "TERMINATED" && slice.date_destruction) && (
            <div style={{ fontSize: 9.5, color: T.textFaint, marginTop: 6 }}>
                Destruido: {slice.date_destruction} UTC
            </div>
        )}

        {/* Comentario del revisor si fue rechazado */}
        {slice.status === "REJECTED" && slice.review?.comment && (
            <div style={{
                display: "flex", gap: 6, marginTop: 8, fontSize: 10.5, lineHeight: 1.45,
                color: "#e65100", background: "#fff3e0", border: "1px solid #e6510044",
                borderRadius: 6, padding: "6px 8px",
            }}>
                <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
                <span><b>Motivo del rechazo:</b> {slice.review.comment}</span>
            </div>
        )}

        {/* Deploy / re-solicitud (borradores y rechazados) */}
        {["DRAFT", "Draft", "REJECTED"].includes(slice.status) && (
            <button
                onClick={e => { e.stopPropagation(); onDeploy(slice.id); }}
                style={btnBase({ width: "100%", marginTop: 10, background: T.accent, color: "#fff", border: "none", padding: "7px 0", fontSize: 12,
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                <Zap size={13} /> {slice.status === "REJECTED" ? "Volver a Solicitar" : "Solicitar Despliegue"}
            </button>
        )}
    </div>
);
