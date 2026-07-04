import { useState, useEffect, useRef } from "react";
import { T, btnBase, getGlobalCss } from "../theme/tokens";
import { ThemePicker }  from "../components/ui/ThemePicker";
import { UserAvatar }   from "../components/ui/UserAvatar";
import {
    ArrowLeft, Server, Cpu, MemoryStick, HardDrive,
    Activity, RefreshCw, AlertTriangle, CheckCircle,
} from "../components/ui/Icon";

const POLL_MS = 5000;

// ─── Helpers ──────────────────────────────────────────────────────────────────
const pct  = (v) => (v != null ? `${Number(v).toFixed(1)}%` : "—");
const occx = (v) => (v != null ? `${Number(v).toFixed(2)}×` : "—");

const Bar = ({ value }) => {
    const ratio = Math.min(Math.max(value / 100, 0), 1);
    const color = ratio > 0.9 ? "#ef4444" : ratio > 0.7 ? "#f59e0b" : T.accent;
    return (
        <div style={{ height: 6, borderRadius: 3, background: T.border, overflow: "hidden", marginTop: 4 }}>
            <div style={{ height: "100%", width: `${ratio * 100}%`, borderRadius: 3, background: color, transition: "width 0.5s" }} />
        </div>
    );
};

// ─── Worker Card ──────────────────────────────────────────────────────────────
const WorkerCard = ({ w }) => {
    const cpu     = w.live_cpu_usage_pct ?? 0;
    const ram     = w.live_ram_usage_pct ?? 0;
    // Disco: el API no expone live_disk_usage_pct, solo disk_gb (capacidad total)
    // Mostramos la capacidad total como referencia
    const diskGb  = w.disk_gb ?? null;
    const oc_cpu  = w.oc_cpu ?? null;
    const isUp    = w.reachable !== false;

    return (
        <div style={{
            background: T.surface, borderRadius: 12, padding: "16px 18px",
            border: `1.5px solid ${T.border}`,
            boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
        }}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Server size={15} color={T.accent} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: T.text }}>Worker {w.worker_id}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                    {isUp
                        ? <><CheckCircle size={11} color="#16a34a" /><span style={{ fontSize: 10, color: "#16a34a", fontWeight: 600 }}>Online</span></>
                        : <><AlertTriangle size={11} color="#ef4444" /><span style={{ fontSize: 10, color: "#ef4444", fontWeight: 600 }}>Offline</span></>
                    }
                </div>
            </div>

            {/* CPU */}
            <div style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <Cpu size={10} /> CPU
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>{pct(cpu)}</span>
                </div>
                <Bar value={cpu} />
            </div>

            {/* RAM */}
            <div style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <MemoryStick size={10} /> RAM
                        {w.ram_gb ? <span style={{ color: T.textFaint }}>({w.ram_gb} GB total)</span> : null}
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>{pct(ram)}</span>
                </div>
                <Bar value={ram} />
            </div>

            {/* Disco */}
            <div style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <HardDrive size={10} /> Disco
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>
                        {diskGb != null ? `${diskGb.toFixed(1)} GB` : "—"}
                    </span>
                </div>
                {/* Disco no tiene uso en vivo — mostramos barra neutral */}
                <div style={{ height: 6, borderRadius: 3, background: T.border, marginTop: 4 }}>
                    <div style={{ height: "100%", width: "100%", borderRadius: 3, background: `${T.accent}44` }} />
                </div>
            </div>

            {/* OC CPU */}
            <div style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "4px 10px", borderRadius: 6,
                background: T.accentLight, border: `1px solid ${T.accent}33`,
            }}>
                <Cpu size={11} color={T.accent} />
                <span style={{ fontSize: 10, color: T.textMuted, fontWeight: 600 }}>OC CPU</span>
                <span style={{ fontSize: 13, fontWeight: 800, color: T.accent }}>{occx(oc_cpu)}</span>
            </div>
        </div>
    );
};

// ─── InfraMonitorView ─────────────────────────────────────────────────────────
export const InfraMonitorView = ({ user, logout, reTheme, themeRev, onBack, onProfile, apiFetch }) => {
    const [workersData, setWorkersData] = useState(null);
    const [status,      setStatus]      = useState(null);
    const [loading,     setLoading]     = useState(true);
    const [error,       setError]       = useState(null);
    const [lastUpdate,  setLastUpdate]  = useState(null);
    const intervalRef = useRef(null);

    const fetchAll = async () => {
        try {
            const [wRes, sRes] = await Promise.all([
                apiFetch("/observability/metrics/workers"),
                apiFetch("/observability/metrics/status"),
            ]);
            if (wRes.ok) {
                setWorkersData(await wRes.json());
                setError(null);
            } else {
                setError(`Error ${wRes.status} al obtener métricas de workers`);
            }
            if (sRes.ok) setStatus(await sRes.json());
            setLastUpdate(new Date());
        } catch (e) {
            setError("No se pudo conectar al módulo de Observabilidad");
            console.error("InfraMonitor fetch:", e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchAll();
        intervalRef.current = setInterval(fetchAll, POLL_MS);
        return () => clearInterval(intervalRef.current);
    }, []);

    // Extraer array de workers del response { workers: [...] }
    const workers = workersData?.workers ?? [];
    const totalWorkers   = workers.length;
    const onlineWorkers  = workers.filter(w => w.reachable !== false).length;
    const avgCpu = totalWorkers > 0
        ? workers.reduce((s, w) => s + (w.live_cpu_usage_pct ?? 0), 0) / totalWorkers : 0;
    const avgRam = totalWorkers > 0
        ? workers.reduce((s, w) => s + (w.live_ram_usage_pct ?? 0), 0) / totalWorkers : 0;

    // Datos del scheduler desde status.scheduler
    const sched      = status?.scheduler;
    const cycleCount = sched?.cycle_count ?? null;
    const lastError  = sched?.last_error ?? null;
    const targetsObj = status?.targets ?? {};
    const connObj    = status?.connectivity ?? {};
    const targetsTotal = Object.keys(targetsObj).length;
    const targetsUp    = Object.values(connObj).filter(Boolean).length;

    return (
        <div style={{
            display: "flex", flexDirection: "column", height: "100vh",
            background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text,
        }}>
            {/* Topbar */}
            <div style={{
                padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`,
                background: T.surface, display: "flex", alignItems: "center", gap: 12,
                flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)",
            }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "5px 12px", fontSize: 11, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <div style={{ width: 1, height: 22, background: T.border }} />
                <Activity size={16} color={T.accent} />
                <span style={{ fontSize: 14, fontWeight: 800, color: T.text }}>Monitoreo de Infraestructura</span>
                <div style={{ flex: 1 }} />
                {lastUpdate && (
                    <span style={{ fontSize: 10, color: T.textFaint }}>
                        Actualizado: {lastUpdate.toLocaleTimeString()}
                    </span>
                )}
                <button onClick={fetchAll}
                    style={btnBase({ padding: "5px 10px", fontSize: 10, boxShadow: "none", display: "flex", alignItems: "center", gap: 4, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}` })}>
                    <RefreshCw size={11} /> Actualizar
                </button>
                <ThemePicker onThemeChange={reTheme} />
                <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
                {loading ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted, fontSize: 13 }}>
                        Cargando métricas...
                    </div>
                ) : error ? (
                    <div style={{ textAlign: "center", padding: 60 }}>
                        <AlertTriangle size={32} color="#ef4444" />
                        <div style={{ fontSize: 14, color: "#ef4444", marginTop: 12, fontWeight: 600 }}>{error}</div>
                        <div style={{ fontSize: 12, color: T.textMuted, marginTop: 6 }}>
                            Verifique que el servicio de Observabilidad esté corriendo
                        </div>
                    </div>
                ) : (
                    <>
                        {/* Summary cards */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 20 }}>
                            {[
                                ["Workers Online",  `${onlineWorkers} / ${totalWorkers}`, Server,      onlineWorkers === totalWorkers ? "#16a34a" : "#f59e0b"],
                                ["CPU Promedio",    pct(avgCpu),                          Cpu,         avgCpu > 80 ? "#ef4444" : T.accent],
                                ["RAM Promedio",    pct(avgRam),                          MemoryStick, avgRam > 80 ? "#ef4444" : T.accent],
                                ["Ciclos OC",       cycleCount ?? "—",                    Activity,    T.accent],
                            ].map(([label, val, Icon, color]) => (
                                <div key={label} style={{
                                    padding: "16px 18px", borderRadius: 12,
                                    background: T.surface, border: `1px solid ${T.border}`,
                                    boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
                                }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                                        <Icon size={13} color={color} />
                                        <span style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</span>
                                    </div>
                                    <div style={{ fontSize: 22, fontWeight: 800, color }}>{val}</div>
                                </div>
                            ))}
                        </div>

                        {/* Scheduler bar */}
                        {status && (
                            <div style={{
                                padding: "10px 16px", borderRadius: 10, marginBottom: 20,
                                background: T.surface, border: `1px solid ${T.border}`,
                                display: "flex", gap: 24, flexWrap: "wrap", fontSize: 11, alignItems: "center",
                            }}>
                                {[
                                    ["Targets",       `${targetsUp} / ${targetsTotal}`],
                                    ["Ciclos",        cycleCount ?? "—"],
                                    ["Intervalo",     sched?.interval_s ? `${sched.interval_s}s` : "—"],
                                    ["Último error",  lastError ?? "Ninguno"],
                                    ["Último ciclo",  sched?.last_cycle_at ? new Date(sched.last_cycle_at).toLocaleTimeString() : "—"],
                                ].map(([k, v]) => (
                                    <div key={k} style={{ display: "flex", gap: 5, alignItems: "center" }}>
                                        <span style={{ color: T.textMuted }}>{k}:</span>
                                        <span style={{ fontWeight: 700, color: T.text }}>{v}</span>
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Worker grid */}
                        <div style={{ fontSize: 11, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 12 }}>
                            Workers ({totalWorkers})
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
                            {workers.map(w => (
                                <WorkerCard key={w.worker_id} w={w} />
                            ))}
                        </div>
                    </>
                )}
            </div>

            <style key={themeRev}>{getGlobalCss()}</style>
        </div>
    );
};
