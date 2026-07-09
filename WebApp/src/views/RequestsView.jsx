/**
 * RequestsView — Bandeja de Evaluación de Solicitudes (REQ-JP-05 / REQ-AD-03).
 *
 * jefeProyecto ve las solicitudes de sus proyectos; admin/superAdmin ven todas.
 * Cada solicitud se puede Aprobar (encola el despliegue) o Rechazar (estado
 * REJECTED, editable por el alumno). El comentario es obligatorio al rechazar.
 */
import { useState, useEffect } from "react";
import { T, btnBase } from "../theme/tokens";
import { UserAvatar }  from "../components/ui/UserAvatar";
import {
    ArrowLeft, X, Check, AlertTriangle, Inbox, Server, Cpu,
} from "../components/ui/Icon";

// ─── Modal de revisión (aprobar / rechazar con comentario) ────────────────────
const ReviewModal = ({ request, action, onConfirm, onClose }) => {
    const [comment, setComment] = useState("");
    const [saving, setSaving]   = useState(false);
    const isReject = action === "reject";

    const submit = async () => {
        if (isReject && !comment.trim()) return;
        setSaving(true);
        await onConfirm(request.slice_id, action, comment.trim());
        setSaving(false);
    };

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 440, border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: isReject ? T.red : T.accent }}>
                            {isReject ? "❌ Rechazar solicitud" : "✅ Aprobar solicitud"}
                        </div>
                        <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>
                            "{request.slice_name}" — {request.owner_name}
                        </div>
                    </div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                    Comentario de Evaluación {isReject ? "(obligatorio)" : "(opcional)"}
                </label>
                <textarea value={comment} onChange={e => setComment(e.target.value)}
                    rows={4}
                    placeholder={isReject
                        ? 'Ej. "Reduce la RAM de tus routers a 512MB y te lo apruebo"'
                        : "Comentario para el solicitante…"}
                    style={{
                        width: "100%", padding: "8px 10px", fontSize: 12, marginTop: 6,
                        background: T.surfaceElevated, border: `1px solid ${T.border}`,
                        borderRadius: 8, color: T.text, fontFamily: "inherit",
                        outline: "none", boxSizing: "border-box", resize: "vertical",
                    }} />

                <div style={{ display: "flex", gap: 8, marginTop: 18, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        Cancelar
                    </button>
                    <button onClick={submit} disabled={saving || (isReject && !comment.trim())}
                        style={btnBase({
                            padding: "8px 16px", fontSize: 12, fontWeight: 700,
                            background: isReject ? T.red : T.accent, color: "#fff", border: "none",
                            opacity: (isReject && !comment.trim()) ? 0.5 : 1,
                        })}>
                        {saving ? "Enviando…" : (isReject ? "Rechazar" : "Aprobar y desplegar")}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── RequestsView ─────────────────────────────────────────────────────────────
export const RequestsView = ({ user, onBack, onProfile, apiFetch, flash, onChanged }) => {
    const [requests, setRequests] = useState([]);
    const [loading,  setLoading]  = useState(true);
    const [error,    setError]    = useState(null);
    const [review,   setReview]   = useState(null);   // { request, action }
    const [expanded, setExpanded] = useState(null);   // slice_id expandido

    const load = async () => {
        try {
            const res = await apiFetch("/requests/pending");
            if (res.ok) { setRequests(await res.json()); setError(null); }
            else setError(`Error ${res.status} al cargar solicitudes`);
        } catch {
            setError("No se pudo conectar al servidor");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const doReview = async (sliceId, action, comment) => {
        try {
            const res = await apiFetch(`/requests/${sliceId}/${action}`, {
                method: "POST",
                body: JSON.stringify({ comment: comment || null }),
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                flash(action === "approve"
                    ? "Solicitud aprobada — enviada a despliegue"
                    : "Solicitud rechazada");
                setReview(null);
                load();
                onChanged?.();
            } else {
                flash(data.detail || "Error al procesar la revisión", "error");
            }
        } catch { flash("Error de conexión", "error"); }
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text }}>
            {/* Topbar */}
            <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Inbox size={16} color={T.accent} />
                    <span style={{ fontSize: 15, fontWeight: 800 }}>Solicitudes de Despliegue</span>
                    {requests.length > 0 && (
                        <span style={{ background: T.red, color: "#fff", fontSize: 10, fontWeight: 800, borderRadius: 20, padding: "2px 8px" }}>
                            {requests.length}
                        </span>
                    )}
                </div>
                <div style={{ flex: 1 }} />
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            {/* Body */}
            <div style={{ flex: 1, overflowY: "auto", padding: "22px 26px" }}>
                {loading && <div style={{ color: T.textMuted, fontSize: 13 }}>Cargando solicitudes…</div>}

                {error && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.red, fontSize: 13, background: T.redLight, padding: "10px 14px", borderRadius: 8 }}>
                        <AlertTriangle size={15} /> {error}
                    </div>
                )}

                {!loading && !error && requests.length === 0 && (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted }}>
                        <Inbox size={40} color={T.textFaint} />
                        <div style={{ fontSize: 14, fontWeight: 700, marginTop: 12 }}>Bandeja vacía</div>
                        <div style={{ fontSize: 12, marginTop: 4 }}>No hay solicitudes pendientes de aprobación.</div>
                    </div>
                )}

                <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 900, margin: "0 auto" }}>
                    {requests.map(req => (
                        <div key={req.slice_id} style={{
                            background: T.surface, border: `1px solid ${T.border}`,
                            borderRadius: 12, padding: "16px 18px", boxShadow: T.shadow,
                        }}>
                            {/* Header row */}
                            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                                <div style={{ minWidth: 200 }}>
                                    <div style={{ fontSize: 14, fontWeight: 800, color: T.text }}>{req.slice_name}</div>
                                    <div style={{ fontSize: 11, color: T.textMuted, marginTop: 3 }}>
                                        Solicitante: <b style={{ color: T.text }}>{req.owner_name}</b>
                                        {req.owner_email && <span> · {req.owner_email}</span>}
                                    </div>
                                    <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>
                                        Proyecto: <b style={{ color: T.text }}>{req.project_name || "— personal —"}</b>
                                        {" "}· Zona: <b style={{ color: T.accent }}>{req.zone_name || `AZ ${req.zone_id}`}</b>
                                        {" "}· TTL: <b style={{ color: T.text }}>{req.ttl_hours ? `${req.ttl_hours}h` : "—"}</b>
                                        {req.requested_at && <span> · {req.requested_at} UTC</span>}
                                    </div>
                                </div>

                                {/* Resource summary */}
                                <div style={{ display: "flex", gap: 8 }}>
                                    {[
                                        [<Server key="s" size={12} color={T.accent} />, "VMs",  req.vm_count],
                                        [<Cpu    key="c" size={12} color={T.accent} />, "vCPU", req.total_vcpus],
                                        [null, "RAM", `${req.total_ram_mb} MB`],
                                    ].map(([icon, label, val]) => (
                                        <div key={label} style={{ textAlign: "center", background: T.surfaceElevated, borderRadius: 8, padding: "6px 12px", border: `1px solid ${T.border}` }}>
                                            <div style={{ fontSize: 13, fontWeight: 800, color: T.accent, display: "flex", alignItems: "center", gap: 4, justifyContent: "center" }}>
                                                {icon}{val}
                                            </div>
                                            <div style={{ fontSize: 8, color: T.textMuted, textTransform: "uppercase", marginTop: 2 }}>{label}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* Motivo */}
                            {req.motivo && (
                                <div style={{ marginTop: 10, fontSize: 12, color: T.text, background: T.surfaceElevated, borderRadius: 8, padding: "8px 12px", border: `1px solid ${T.border}` }}>
                                    <span style={{ fontSize: 9, fontWeight: 800, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: 2 }}>Motivo</span>
                                    {req.motivo}
                                </div>
                            )}

                            {/* Topology detail (expandable) */}
                            {expanded === req.slice_id && (
                                <div style={{ marginTop: 10, fontSize: 11, color: T.textMuted, background: T.surfaceElevated, borderRadius: 8, padding: "8px 12px", border: `1px solid ${T.border}` }}>
                                    {req.nodes.map(n => (
                                        <div key={n.id} style={{ padding: "3px 0", borderBottom: `1px dashed ${T.border}` }}>
                                            <b style={{ color: T.text }}>{n.label || n.id}</b>
                                            {" "}— {n.vcores || 1} vCPU · {n.ram || 512} MB · {n.disk || 5} GB
                                            {" "}· img: {n.image || "—"}
                                            {n.internet_access ? " · 🌐 internet" : ""}
                                            {n.external_ip ? ` · IP ${n.external_ip}` : ""}
                                        </div>
                                    ))}
                                    <div style={{ paddingTop: 4 }}>{req.edges.length} enlace(s)</div>
                                </div>
                            )}

                            {/* Actions */}
                            <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
                                <button onClick={() => setExpanded(expanded === req.slice_id ? null : req.slice_id)}
                                    style={btnBase({ padding: "7px 12px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                                    {expanded === req.slice_id ? "Ocultar topología" : "Ver topología"}
                                </button>
                                <button onClick={() => setReview({ request: req, action: "reject" })}
                                    style={btnBase({ padding: "7px 14px", fontSize: 11, fontWeight: 700, background: T.redLight, color: T.red, border: `1px solid ${T.red}44`, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                                    <X size={12} /> Rechazar
                                </button>
                                <button onClick={() => setReview({ request: req, action: "approve" })}
                                    style={btnBase({ padding: "7px 14px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none", display: "flex", alignItems: "center", gap: 5 })}>
                                    <Check size={12} /> Aprobar
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {review && (
                <ReviewModal
                    request={review.request}
                    action={review.action}
                    onConfirm={doReview}
                    onClose={() => setReview(null)}
                />
            )}
        </div>
    );
};
