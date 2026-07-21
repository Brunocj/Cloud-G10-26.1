/**
 * ConsumptionView — Consumo de recursos por proyecto (REQ-JP-10 / REQ-AD-09).
 * admin/superAdmin ven todos los proyectos; jefeProyecto los suyos.
 */
import { useState, useEffect } from "react";
import { T, btnBase, FONT_STACK } from "../theme/tokens";
import { UserAvatar } from "../components/ui/UserAvatar";
import { ArrowLeft, BarChart2, RefreshCw, AlertTriangle, Users } from "../components/ui/Icon";

const fmtRam = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;

export const ConsumptionView = ({ user, onBack, onProfile, apiFetch }) => {
    const [rows, setRows]       = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const res = await apiFetch("/projects/consumption");
            if (res.ok) { setRows(await res.json()); setError(null); }
            else setError(`Error ${res.status} al cargar el consumo`);
        } catch { setError("No se pudo conectar al servidor"); }
        setLoading(false);
    };

    useEffect(() => { load(); }, []);

    // Totales de plataforma (fila superior)
    const totals = rows.reduce((acc, r) => ({
        vcpus:  acc.vcpus  + (r.vcpus  || 0),
        ram_mb: acc.ram_mb + (r.ram_mb || 0),
        disk_gb: acc.disk_gb + (r.disk_gb || 0),
        active_vms: acc.active_vms + (r.active_vms || 0),
        active_slices: acc.active_slices + (r.active_slices || 0),
    }), { vcpus: 0, ram_mb: 0, disk_gb: 0, active_vms: 0, active_slices: 0 });

    const maxVcpu = Math.max(1, ...rows.map(r => r.vcpus || 0));

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: FONT_STACK, color: T.text }}>
            <div className="app-topbar" style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <BarChart2 size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800 }}>Consumo por Proyecto</span>
                <button onClick={load} title="Refrescar" aria-label="Refrescar"
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                    <RefreshCw size={14} />
                </button>
                <div style={{ flex: 1 }} />
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "22px 26px" }}>
                {loading && <div style={{ color: T.textMuted, fontSize: 13 }}>Cargando…</div>}
                {error && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.red, fontSize: 13, background: T.redLight, padding: "10px 14px", borderRadius: 8 }}>
                        <AlertTriangle size={15} /> {error}
                    </div>
                )}

                {!loading && !error && (
                    <div style={{ maxWidth: 980, margin: "0 auto" }}>
                        {/* KPIs de plataforma */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 12, marginBottom: 22 }}>
                            {[
                                ["Slices activos", totals.active_slices],
                                ["VMs activas",    totals.active_vms],
                                ["vCPUs en uso",   totals.vcpus],
                                ["RAM reservada",  fmtRam(totals.ram_mb)],
                                ["Disco",          `${totals.disk_gb.toFixed(0)} GB`],
                            ].map(([l, v]) => (
                                <div key={l} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, padding: "14px 16px", textAlign: "center", boxShadow: T.shadow }}>
                                    <div style={{ fontSize: 22, fontWeight: 900, color: T.accent }}>{v}</div>
                                    <div style={{ fontSize: 9, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginTop: 3 }}>{l}</div>
                                </div>
                            ))}
                        </div>

                        {/* Tabla por proyecto */}
                        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden", boxShadow: T.shadow }}>
                            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                                <thead>
                                    <tr style={{ background: T.surfaceElevated }}>
                                        {["Proyecto", "Miembros", "Slices (activos/total)", "VMs", "vCPU", "RAM", "Disco", "Carga vCPU"].map(h => (
                                            <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 10, fontWeight: 800, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", borderBottom: `1px solid ${T.border}` }}>{h}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map(r => (
                                        <tr key={r.project_id ?? "none"} style={{ borderBottom: `1px solid ${T.border}` }}>
                                            <td style={{ padding: "10px 14px", fontWeight: 700, color: T.text }}>{r.project_name}</td>
                                            <td style={{ padding: "10px 14px", color: T.textMuted }}>
                                                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                                                    <Users size={11} /> {r.member_count}
                                                </span>
                                            </td>
                                            <td style={{ padding: "10px 14px" }}>
                                                <b style={{ color: T.accent }}>{r.active_slices}</b>
                                                <span style={{ color: T.textMuted }}> / {r.total_slices}</span>
                                            </td>
                                            <td style={{ padding: "10px 14px" }}>{r.active_vms}</td>
                                            <td style={{ padding: "10px 14px", fontWeight: 700 }}>{r.vcpus}</td>
                                            <td style={{ padding: "10px 14px" }}>{fmtRam(r.ram_mb)}</td>
                                            <td style={{ padding: "10px 14px" }}>{r.disk_gb.toFixed(0)} GB</td>
                                            <td style={{ padding: "10px 14px", width: 160 }}>
                                                <div style={{ background: T.surfaceElevated, borderRadius: 6, height: 8, overflow: "hidden", border: `1px solid ${T.border}` }}>
                                                    <div style={{
                                                        width: `${Math.round((r.vcpus / maxVcpu) * 100)}%`,
                                                        height: "100%", background: T.accent, transition: "width 0.3s",
                                                    }} />
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                    {rows.length === 0 && (
                                        <tr><td colSpan={8} style={{ padding: 30, textAlign: "center", color: T.textMuted }}>Sin proyectos que mostrar.</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
