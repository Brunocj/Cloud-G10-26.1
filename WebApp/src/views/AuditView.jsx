/**
 * AuditView — Bitácora de Eventos (REQ-JP-09 / REQ-AD-08).
 * admin/superAdmin ven todo; jefeProyecto lo de sus proyectos.
 * Filtros por nivel, módulo y búsqueda de texto. Refresco manual + auto (15s).
 */
import { useState, useEffect, useRef } from "react";
import { T, btnBase, inp, FONT_STACK } from "../theme/tokens";
import { UserAvatar } from "../components/ui/UserAvatar";
import { ArrowLeft, ClipboardList, RefreshCw, AlertTriangle, Search } from "../components/ui/Icon";

// Función (no constante de módulo) para que lea los valores vivos de T: los
// colores eran hex fijos de tema claro y en los temas oscuros quedaban badges
// pálidos sobre superficie oscura.
const getLevelStyle = () => ({
    INFO:    { fg: T.accent, bg: T.accentLight },
    WARNING: { fg: T.yellow, bg: T.yellowLight },
    ERROR:   { fg: T.red,    bg: T.redLight    },
});

const ACTION_LABELS = {
    deploy_requested: "Solicitud",
    deploy_direct:    "Despliegue directo",
    request_approved: "Aprobación",
    request_rejected: "Rechazo",
    slice_active:     "Slice activo",
    deploy_failed:    "Fallo de despliegue",
    slice_destroyed:  "Destrucción",
    slice_expired:    "Destrucción por TTL",
    force_destroy:    "Kill Switch",
    user_created:     "Usuario creado",
    role_changed:     "Cambio de rol",
    password_changed: "Cambio de contraseña",
    project_created:  "Proyecto creado",
    project_deleted:  "Proyecto eliminado",
    worker_created:   "Worker matriculado",
    worker_updated:   "Worker actualizado",
    worker_deleted:   "Worker eliminado",
    zone_created:     "Zona creada",
    zone_deleted:     "Zona eliminada",
};

export const AuditView = ({ user, onBack, onProfile, apiFetch }) => {
    const [rows, setRows]       = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState(null);
    const [level, setLevel]     = useState("");
    const [query, setQuery]     = useState("");
    const queryRef = useRef("");
    queryRef.current = query;
    const levelStyle = getLevelStyle();

    const load = async (lvl = level) => {
        try {
            const params = new URLSearchParams({ limit: "300" });
            if (lvl) params.set("level", lvl);
            if (queryRef.current.trim()) params.set("q", queryRef.current.trim());
            const res = await apiFetch(`/audit/?${params.toString()}`);
            if (res.ok) { setRows(await res.json()); setError(null); }
            else setError(`Error ${res.status} al cargar la bitácora`);
        } catch { setError("No se pudo conectar al servidor"); }
        setLoading(false);
    };

    useEffect(() => {
        load();
        const t = setInterval(() => load(), 15000);
        return () => clearInterval(t);
    }, [level]);

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: FONT_STACK, color: T.text }}>
            <div className="app-topbar" style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <ClipboardList size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800 }}>Bitácora de Eventos</span>

                {/* Filtro de nivel */}
                <div style={{ display: "flex", gap: 4, marginLeft: 10 }}>
                    {["", "INFO", "WARNING", "ERROR"].map(lvl => (
                        <button key={lvl || "ALL"} onClick={() => { setLevel(lvl); }}
                            style={btnBase({
                                padding: "4px 10px", fontSize: 10, fontWeight: 700, boxShadow: "none",
                                background: level === lvl ? T.accent : T.surfaceElevated,
                                color: level === lvl ? "#fff" : T.textMuted,
                                border: `1px solid ${level === lvl ? T.accent : T.border}`,
                            })}>
                            {lvl || "TODOS"}
                        </button>
                    ))}
                </div>

                {/* Búsqueda */}
                <div style={{ position: "relative", width: 220 }}>
                    <Search size={13} color={T.textMuted} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
                    <input value={query} onChange={e => setQuery(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && load()}
                        placeholder="Buscar (Enter)…"
                        style={{ ...inp, marginBottom: 0, padding: "6px 10px 6px 30px", fontSize: 11 }} />
                </div>

                <button onClick={() => load()} title="Refrescar" aria-label="Refrescar"
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                    <RefreshCw size={14} />
                </button>
                <div style={{ flex: 1 }} />
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "18px 24px" }}>
                {loading && <div style={{ color: T.textMuted, fontSize: 13 }}>Cargando…</div>}
                {error && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.red, fontSize: 13, background: T.redLight, padding: "10px 14px", borderRadius: 8 }}>
                        <AlertTriangle size={15} /> {error}
                    </div>
                )}

                {!loading && !error && (
                    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden", boxShadow: T.shadow }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                            <thead>
                                <tr style={{ background: T.surfaceElevated }}>
                                    {["Fecha (UTC)", "Nivel", "Actor", "Módulo", "Acción", "Detalle", "Slice"].map(h => (
                                        <th key={h} style={{ padding: "9px 12px", textAlign: "left", fontSize: 10, fontWeight: 800, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", borderBottom: `1px solid ${T.border}` }}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(r => {
                                    const lv = levelStyle[r.level] || levelStyle.INFO;
                                    const isErr = r.level === "ERROR" || r.action === "force_destroy";
                                    return (
                                        <tr key={r.id} style={{ borderBottom: `1px solid ${T.border}`, background: isErr ? T.redLight : "transparent" }}>
                                            <td style={{ padding: "8px 12px", fontFamily: "monospace", fontSize: 11, color: T.textMuted, whiteSpace: "nowrap" }}>{r.timestamp}</td>
                                            <td style={{ padding: "8px 12px" }}>
                                                <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 20, color: lv.fg, background: lv.bg }}>{r.level}</span>
                                            </td>
                                            <td style={{ padding: "8px 12px", fontWeight: 700, color: T.text, whiteSpace: "nowrap" }}>
                                                {r.actor_name}
                                                <span style={{ fontWeight: 400, color: T.textFaint, fontSize: 10 }}> ({r.actor_role})</span>
                                            </td>
                                            <td style={{ padding: "8px 12px", color: T.textMuted }}>{r.module}</td>
                                            <td style={{ padding: "8px 12px", fontWeight: 700, color: isErr ? T.red : T.accent, whiteSpace: "nowrap" }}>
                                                {ACTION_LABELS[r.action] || r.action}
                                            </td>
                                            <td style={{ padding: "8px 12px", color: isErr ? T.red : T.text, lineHeight: 1.45 }}>{r.detail}</td>
                                            <td style={{ padding: "8px 12px", color: T.textMuted, fontFamily: "monospace", fontSize: 11 }}>{r.slice_id ?? "—"}</td>
                                        </tr>
                                    );
                                })}
                                {rows.length === 0 && (
                                    <tr><td colSpan={7} style={{ padding: 30, textAlign: "center", color: T.textMuted }}>Sin eventos registrados aún.</td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};
