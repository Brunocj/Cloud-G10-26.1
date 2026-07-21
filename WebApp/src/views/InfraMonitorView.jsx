/**
 * InfraMonitorView — Infraestructura (vista unificada: monitoreo + gestión).
 * Solo superAdmin.
 *
 * Combina:
 *  - Monitoreo en vivo (Observabilidad): CPU/RAM %, scheduler, ciclos OC. Poll 5s.
 *  - Gestión (infra-API): matricular/editar/desmatricular workers, zonas de disponibilidad.
 *
 * Diseño:
 *  - Cards de workers agrupadas por zona de disponibilidad, con selector de zona (default: todas).
 *  - Cada card integra specs nominales (vCPU / RAM / Disco) con el consumo medido.
 *  - IP y SSH ocultos por defecto; se revelan por card con el botón de ojo.
 *  - La caída de Observabilidad NO bloquea la gestión: las cards muestran specs sin métricas.
 */
import { useState, useEffect, useRef } from "react";
import { T, btnBase, inp, FONT_STACK } from "../theme/tokens";
import { useConfirm } from "../hooks/useConfirm";
import { ThemePicker }  from "../components/ui/ThemePicker";
import { UserAvatar }   from "../components/ui/UserAvatar";
import {
    ArrowLeft, Server, Cpu, MemoryStick, HardDrive,
    Activity, RefreshCw, AlertTriangle, CheckCircle,
    Globe, Plus, X, Trash2, Edit3, Eye, EyeOff,
} from "../components/ui/Icon";

const POLL_MS = 5000;

// ─── Helpers ──────────────────────────────────────────────────────────────────
const pct  = (v) => (v != null ? `${Number(v).toFixed(1)}%` : "—");
const occx = (v) => (v != null ? `${Number(v).toFixed(2)}×` : "—");
const fmtRamMb = (mb) => (mb != null ? (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`) : null);

const Bar = ({ value }) => {
    if (value == null) {
        return <div style={{ height: 6, borderRadius: 3, background: T.border, marginTop: 4, opacity: 0.5 }} />;
    }
    const ratio = Math.min(Math.max(value / 100, 0), 1);
    const color = ratio > 0.9 ? "#ef4444" : ratio > 0.7 ? "#f59e0b" : T.accent;
    return (
        <div style={{ height: 6, borderRadius: 3, background: T.border, overflow: "hidden", marginTop: 4 }}>
            <div style={{ height: "100%", width: `${ratio * 100}%`, borderRadius: 3, background: color, transition: "width 0.5s" }} />
        </div>
    );
};

// ─── Modal de matrícula / edición de worker (portado de Gestión) ──────────────
const FIELDS = [
    ["name",         "Nombre",        "text",   "worker4"],
    ["ip",           "IP",            "text",   "10.0.10.5"],
    ["ssh_port",     "Puerto SSH",    "number", "22"],
    ["ssh_user",     "Usuario SSH",   "text",   "ubuntu"],
    ["ssh_key_path", "Ruta llave SSH","text",   "/app/keys/id_ed25519"],
    ["cpu",          "vCPUs",         "number", "8"],
    ["ram",          "RAM (MB)",      "number", "16384"],
    ["disk_gb",      "Disco (GB)",    "number", "100"],
];

const WorkerModal = ({ worker, zones, onSave, onClose, onTest }) => {
    const [form, setForm] = useState(worker ? { ...worker } : { ssh_port: 22 });
    const [busy, setBusy] = useState(false);
    const [testing, setTesting]       = useState(false);
    const [testResult, setTestResult] = useState(null);   // { ok, message, cpu, ram_mb, ... }
    const set = (k, v) => { setForm(f => ({ ...f, [k]: v })); setTestResult(null); };

    const submit = async () => {
        if (!form.name?.trim() || !form.ip?.trim()) return;
        setBusy(true);
        await onSave(form);
        setBusy(false);
    };

    const runTest = async () => {
        setTesting(true);
        const result = await onTest(form);
        setTestResult(result);
        // Autocompletar recursos detectados si el test fue exitoso
        if (result?.ok) {
            setForm(f => ({
                ...f,
                cpu:     f.cpu     ?? result.cpu,
                ram:     f.ram     ?? result.ram_mb,
                disk_gb: f.disk_gb ?? result.disk_gb,
            }));
        }
        setTesting(false);
    };

    const canTest = form.ip?.trim() && form.ssh_user?.trim() && form.ssh_key_path?.trim();

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 480, border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: T.text }}>
                        {worker ? `Editar ${worker.name}` : "Matricular Servidor Físico"}
                    </div>
                    <button onClick={onClose} aria-label="Cerrar" style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    {FIELDS.map(([k, label, type, ph]) => (
                        <div key={k}>
                            <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</label>
                            <input type={type} value={form[k] ?? ""} placeholder={ph}
                                onChange={e => set(k, type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
                                style={{ width: "100%", padding: "7px 10px", fontSize: 12, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                        </div>
                    ))}
                </div>

                <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Zona de Disponibilidad</label>
                    <select value={form.availability_zones_id ?? ""} onChange={e => set("availability_zones_id", e.target.value === "" ? null : Number(e.target.value))}
                        style={{ width: "100%", padding: "7px 10px", fontSize: 12, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none" }}>
                        <option value="">— Sin asignar —</option>
                        {zones.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}
                    </select>
                </div>

                {/* Resultado del test de conexión */}
                {testResult && (
                    <div style={{
                        marginTop: 12, padding: "9px 12px", borderRadius: 8, fontSize: 11.5, lineHeight: 1.5,
                        background: testResult.ok ? "#16a34a15" : "#ff4d4d18",
                        color: testResult.ok ? "#16a34a" : "#ff4d4d",
                        border: `1px solid ${testResult.ok ? "#16a34a44" : "#ff4d4d55"}`,
                    }}>
                        {testResult.ok
                            ? <>✅ {testResult.message} — {testResult.cpu} vCPU · {testResult.ram_mb} MB RAM · {testResult.disk_gb} GB
                                 {testResult.kvm ? " · KVM disponible" : " · ⚠ sin /dev/kvm"}</>
                            : <>❌ {testResult.message}</>}
                    </div>
                )}

                <div style={{ display: "flex", gap: 8, marginTop: 20, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        Cancelar
                    </button>
                    <button onClick={runTest} disabled={testing || !canTest}
                        title={canTest ? "Verifica SSH y detecta recursos reales" : "Completa IP, usuario SSH y ruta de llave"}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, fontWeight: 700, background: T.surfaceElevated, color: canTest ? T.accent : T.textFaint, border: `1px solid ${canTest ? T.accent + "66" : T.border}`, boxShadow: "none", opacity: testing ? 0.6 : 1 })}>
                        {testing ? "Probando…" : "🔌 Testear Conexión"}
                    </button>
                    <button onClick={submit} disabled={busy || !form.name?.trim() || !form.ip?.trim()}
                        style={btnBase({ padding: "8px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: busy ? 0.6 : 1 })}>
                        {busy ? "Guardando…" : (worker ? "Guardar Cambios" : "Matricular")}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── Worker Card (specs nominales + consumo en vivo + acciones) ───────────────
const WorkerCard = ({ w, m, revealed, onToggleReveal, onEdit, onDelete }) => {
    const hasMetrics = !!m;
    const isUp   = hasMetrics && m.reachable !== false;
    const cpuPct = hasMetrics ? (m.live_cpu_usage_pct ?? 0) : null;
    const ramPct = hasMetrics ? (m.live_ram_usage_pct ?? 0) : null;
    const ramTot = fmtRamMb(w.ram) ?? (m?.ram_gb ? `${m.ram_gb} GB` : null);
    const diskGb = w.disk_gb ?? m?.disk_gb ?? null;

    const status = !hasMetrics
        ? { label: "Sin datos", color: T.textFaint, Icon: AlertTriangle }
        : isUp
            ? { label: "Online",  color: "#16a34a", Icon: CheckCircle }
            : { label: "Offline", color: "#ef4444", Icon: AlertTriangle };

    const iconBtn = (color) => btnBase({ padding: 4, background: "transparent", boxShadow: "none", color });

    return (
        <div style={{
            background: T.surface, borderRadius: 12, padding: "14px 16px",
            border: `1.5px solid ${T.border}`,
            boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
        }}>
            {/* Header: nombre + estado + acciones */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Server size={15} color={T.accent} />
                <span style={{ fontSize: 13, fontWeight: 700, color: T.text }}>{w.name}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <status.Icon size={11} color={status.color} />
                    <span style={{ fontSize: 10, color: status.color, fontWeight: 600 }}>{status.label}</span>
                </div>
                <div style={{ flex: 1 }} />
                <button onClick={onToggleReveal} title={revealed ? "Ocultar IP y SSH" : "Mostrar IP y SSH"}
                    style={iconBtn(revealed ? T.accent : T.textMuted)}>
                    {revealed ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
                <button onClick={onEdit} title="Editar" style={iconBtn(T.textMuted)}>
                    <Edit3 size={13} />
                </button>
                <button onClick={onDelete} title="Desmatricular"
                    style={iconBtn(w.active_vms > 0 ? T.textFaint : T.red)}>
                    <Trash2 size={13} />
                </button>
            </div>

            {/* CPU: nominal + consumo */}
            <div style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <Cpu size={10} /> CPU
                        {w.cpu != null && <span style={{ color: T.textFaint }}>({w.cpu} vCPU)</span>}
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>{pct(cpuPct)}</span>
                </div>
                <Bar value={cpuPct} />
            </div>

            {/* RAM: nominal + consumo */}
            <div style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <MemoryStick size={10} /> RAM
                        {ramTot && <span style={{ color: T.textFaint }}>({ramTot} total)</span>}
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>{pct(ramPct)}</span>
                </div>
                <Bar value={ramPct} />
            </div>

            {/* Disco: capacidad (el API no expone uso en vivo de disco) */}
            <div style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: T.textMuted, display: "flex", alignItems: "center", gap: 3 }}>
                        <HardDrive size={10} /> Disco
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>
                        {diskGb != null ? `${Number(diskGb).toFixed(1)} GB` : "—"}
                    </span>
                </div>
                <div style={{ height: 6, borderRadius: 3, background: T.border, marginTop: 4 }}>
                    <div style={{ height: "100%", width: "100%", borderRadius: 3, background: `${T.accent}44` }} />
                </div>
            </div>

            {/* Chips: OC + VMs vivas */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <div style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "3px 10px", borderRadius: 6,
                    background: T.accentLight, border: `1px solid ${T.accent}33`,
                }}>
                    <Cpu size={11} color={T.accent} />
                    <span style={{ fontSize: 10, color: T.textMuted, fontWeight: 600 }}>OC cpu</span>
                    <span style={{ fontSize: 12, fontWeight: 800, color: T.accent }}>
                        {w.oc_cpu ? `${occx(w.oc_cpu)}` : "por defecto"}
                    </span>
                </div>
                <div style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "3px 10px", borderRadius: 6,
                    background: T.surfaceElevated, border: `1px solid ${T.border}`,
                }}>
                    <span style={{ fontSize: 10, color: T.textMuted, fontWeight: 600 }}>VMs vivas</span>
                    <span style={{ fontSize: 12, fontWeight: 800, color: w.active_vms > 0 ? T.accent : T.textMuted }}>{w.active_vms ?? 0}</span>
                </div>
            </div>

            {/* Información adicional: IP y SSH (bajo demanda) */}
            {revealed && (
                <div style={{
                    marginTop: 12, paddingTop: 10, borderTop: `1px dashed ${T.border}`,
                    fontFamily: "'JetBrains Mono','Fira Code',Consolas,monospace", fontSize: 11,
                    display: "flex", flexDirection: "column", gap: 4,
                }}>
                    <div><span style={{ color: T.textMuted }}>IP&nbsp;&nbsp;</span><span style={{ color: T.text }}>{w.ip ?? "—"}</span></div>
                    <div><span style={{ color: T.textMuted }}>SSH&nbsp;</span><span style={{ color: T.text }}>{w.ssh_user ?? "—"}:{w.ssh_port ?? 22}</span></div>
                </div>
            )}
        </div>
    );
};

// ─── InfraMonitorView (Infraestructura unificada) ─────────────────────────────
export const InfraMonitorView = ({ user, logout, reTheme, themeRev, onBack, onProfile, apiFetch, flash }) => {
    const [askConfirm, confirmDialog] = useConfirm();
    // Métricas en vivo (Observabilidad)
    const [workersData, setWorkersData] = useState(null);
    const [status,      setStatus]      = useState(null);
    const [metricsErr,  setMetricsErr]  = useState(null);
    const [lastUpdate,  setLastUpdate]  = useState(null);
    // Inventario (infra-API)
    const [infraWorkers, setInfraWorkers] = useState([]);
    const [zones,        setZones]        = useState([]);
    const [infraErr,     setInfraErr]     = useState(null);
    const [loading,      setLoading]      = useState(true);
    // UI
    const [editing,    setEditing]    = useState(null);   // null | "new" | worker
    const [newZone,    setNewZone]    = useState("");
    const [zoneFilter, setZoneFilter] = useState("");     // "" todas | String(id) | "none"
    const [revealed,   setRevealed]   = useState({});     // { [workerId]: true }
    const intervalRef = useRef(null);

    // ── Métricas: poll cada 5s ────────────────────────────────────────────────
    const fetchMetrics = async () => {
        try {
            const [wRes, sRes] = await Promise.all([
                apiFetch("/observability/metrics/workers"),
                apiFetch("/observability/metrics/status"),
            ]);
            if (wRes.ok) { setWorkersData(await wRes.json()); setMetricsErr(null); }
            else setMetricsErr(`Error ${wRes.status} al obtener métricas de workers`);
            if (sRes.ok) setStatus(await sRes.json());
            setLastUpdate(new Date());
        } catch (e) {
            setMetricsErr("Sin conexión al módulo de Observabilidad — mostrando solo inventario");
            console.error("InfraMonitor fetch:", e);
        }
    };

    // ── Inventario: carga inicial + tras mutaciones ───────────────────────────
    const loadInfra = async () => {
        try {
            const [rw, rz] = await Promise.all([apiFetch("/infra/workers"), apiFetch("/infra/zones")]);
            if (rw.ok) setInfraWorkers(await rw.json());
            if (rz.ok) setZones(await rz.json());
            if (!rw.ok || !rz.ok) setInfraErr("Error al cargar la infraestructura");
            else setInfraErr(null);
        } catch { setInfraErr("No se pudo conectar al servidor"); }
        setLoading(false);
    };

    const refreshAll = () => { loadInfra(); fetchMetrics(); };

    useEffect(() => {
        loadInfra();
        fetchMetrics();
        intervalRef.current = setInterval(fetchMetrics, POLL_MS);
        return () => clearInterval(intervalRef.current);
    }, []);

    // ── Mutaciones (portadas de Gestión) ──────────────────────────────────────
    const saveWorker = async (form) => {
        const isNew = editing === "new";
        const res = await apiFetch(isNew ? "/infra/workers" : `/infra/workers/${form.id}`, {
            method: isNew ? "POST" : "PUT",
            body: JSON.stringify(form),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            flash(isNew ? `Worker "${form.name}" matriculado` : "Worker actualizado");
            setEditing(null); loadInfra();
        } else flash(data.detail || "Error al guardar el worker", "error");
    };

    const testWorker = async (form) => {
        try {
            const res = await apiFetch("/infra/workers/test-connection", {
                method: "POST",
                body: JSON.stringify({
                    ip: form.ip, ssh_port: form.ssh_port,
                    ssh_user: form.ssh_user, ssh_key_path: form.ssh_key_path,
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) return { ok: false, message: data.detail || `Error ${res.status}` };
            return data;
        } catch { return { ok: false, message: "Error de conexión con el servidor" }; }
    };

    const deleteWorker = (w) => askConfirm({
        title: "Desmatricular worker",
        msg: `¿Desmatricular el worker «${w.name}»? Deja de estar disponible para nuevos despliegues. Esta acción NO destruye la máquina física.`,
        confirmLabel: "Sí, desmatricular",
        onOk: async () => {
            const res = await apiFetch(`/infra/workers/${w.id}`, { method: "DELETE" });
            const data = await res.json().catch(() => ({}));
            if (res.ok) { flash(data.message); loadInfra(); }
            else flash(data.detail || "No se pudo eliminar", "error");
        },
    });

    const addZone = async () => {
        if (!newZone.trim()) return;
        const res = await apiFetch("/infra/zones", { method: "POST", body: JSON.stringify({ name: newZone.trim() }) });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { flash(`Zona "${newZone.trim()}" creada`); setNewZone(""); loadInfra(); }
        else flash(data.detail || "No se pudo crear la zona", "error");
    };

    const deleteZone = (z) => askConfirm({
        title: "Eliminar zona de disponibilidad",
        msg: `¿Eliminar la zona «${z.name}»? Los workers que tenga asignados quedarán sin zona.`,
        onOk: async () => {
            const res = await apiFetch(`/infra/zones/${z.id}`, { method: "DELETE" });
            const data = await res.json().catch(() => ({}));
            if (res.ok) { flash(data.message); loadInfra(); }
            else flash(data.detail || "No se pudo eliminar", "error");
        },
    });

    // ── Join inventario ↔ métricas (metrics.worker_id === infra.id) ───────────
    const metrics = workersData?.workers ?? [];
    const metricsById = Object.fromEntries(metrics.map(m => [m.worker_id, m]));

    const totalWorkers  = infraWorkers.length;
    const withMetrics   = infraWorkers.filter(w => metricsById[w.id]);
    const onlineWorkers = withMetrics.filter(w => metricsById[w.id].reachable !== false).length;
    const avgCpu = withMetrics.length > 0
        ? withMetrics.reduce((s, w) => s + (metricsById[w.id].live_cpu_usage_pct ?? 0), 0) / withMetrics.length : 0;
    const avgRam = withMetrics.length > 0
        ? withMetrics.reduce((s, w) => s + (metricsById[w.id].live_ram_usage_pct ?? 0), 0) / withMetrics.length : 0;

    // Datos del scheduler desde status.scheduler
    const sched      = status?.scheduler;
    const cycleCount = sched?.cycle_count ?? null;
    const lastError  = sched?.last_error ?? null;
    const targetsObj = status?.targets ?? {};
    const connObj    = status?.connectivity ?? {};
    const targetsTotal = Object.keys(targetsObj).length;
    const targetsUp    = Object.values(connObj).filter(Boolean).length;

    // ── Agrupación por zona + filtro ──────────────────────────────────────────
    const unassigned = infraWorkers.filter(w => w.availability_zones_id == null);
    const groups = [
        ...zones.map(z => ({
            key: String(z.id), zone: z,
            workers: infraWorkers.filter(w => w.availability_zones_id === z.id),
        })),
        ...(unassigned.length ? [{ key: "none", zone: null, workers: unassigned }] : []),
    ].filter(g => zoneFilter === "" || g.key === zoneFilter);

    return (
        <div style={{
            display: "flex", flexDirection: "column", height: "100vh",
            background: T.bg, fontFamily: FONT_STACK, color: T.text,
        }}>
            {/* Topbar */}
            <div className="app-topbar" style={{
                padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`,
                background: T.surface, display: "flex", alignItems: "center", gap: 12,
                flexShrink: 0, boxShadow: "0 2px 8px rgba(0,0,0,0.05)",
            }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <Server size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800, color: T.text }}>Infraestructura</span>
                <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 20, background: "#ff820022", color: "#ff8200", border: "1px solid #ff820044" }}>
                    BARE-METAL · SUPERADMIN
                </span>
                <button onClick={refreshAll} title="Refrescar" aria-label="Refrescar"
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                    <RefreshCw size={14} />
                </button>
                <div style={{ flex: 1 }} />
                {lastUpdate && (
                    <span style={{ fontSize: 10, color: T.textFaint }}>
                        Actualizado: {lastUpdate.toLocaleTimeString()}
                    </span>
                )}
                <button onClick={() => setEditing("new")}
                    style={btnBase({ padding: "7px 14px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none", display: "flex", alignItems: "center", gap: 5 })}>
                    <Plus size={12} /> Matricular Servidor
                </button>
                <ThemePicker onThemeChange={reTheme} />
                <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
                {loading ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted, fontSize: 13 }}>
                        Cargando infraestructura...
                    </div>
                ) : infraErr ? (
                    <div style={{ textAlign: "center", padding: 60 }}>
                        <AlertTriangle size={32} color="#ef4444" />
                        <div style={{ fontSize: 14, color: "#ef4444", marginTop: 12, fontWeight: 600 }}>{infraErr}</div>
                        <div style={{ fontSize: 12, color: T.textMuted, marginTop: 6 }}>
                            Verifique que el servicio de infraestructura esté corriendo
                        </div>
                    </div>
                ) : (
                    <>
                        {/* Aviso no bloqueante si Observabilidad está caída */}
                        {metricsErr && (
                            <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.yellow, fontSize: 12, background: T.yellowLight, padding: "9px 14px", borderRadius: 8, marginBottom: 16 }}>
                                <AlertTriangle size={14} /> {metricsErr}
                            </div>
                        )}

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

                        {/* Encabezado de sección + filtro de zona */}
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                            <div style={{ fontSize: 11, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                                Workers ({totalWorkers})
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <span style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Zona</span>
                                <select value={zoneFilter} onChange={e => setZoneFilter(e.target.value)}
                                    style={{ padding: "6px 10px", fontSize: 12, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none" }}>
                                    <option value="">Todas las zonas</option>
                                    {zones.map(z => <option key={z.id} value={String(z.id)}>{z.name}</option>)}
                                    {unassigned.length > 0 && <option value="none">Sin zona</option>}
                                </select>
                            </div>
                        </div>

                        {/* Grupos por zona de disponibilidad */}
                        {groups.map(g => (
                            <div key={g.key} style={{ marginBottom: 24 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                                    <Globe size={14} color={g.zone ? T.accent : T.textFaint} />
                                    <span style={{ fontSize: 13, fontWeight: 800 }}>{g.zone ? g.zone.name : "Sin zona"}</span>
                                    <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 20, background: T.accentLight, color: T.accent }}>
                                        {g.workers.length} worker(s)
                                    </span>
                                    {g.zone && (
                                        <button onClick={() => deleteZone(g.zone)} disabled={g.zone.worker_count > 0}
                                            title={g.zone.worker_count > 0 ? "No se puede eliminar: tiene workers asignados" : "Eliminar zona"}
                                            style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: g.zone.worker_count > 0 ? T.textFaint : T.red, cursor: g.zone.worker_count > 0 ? "not-allowed" : "pointer" })}>
                                            <Trash2 size={13} />
                                        </button>
                                    )}
                                </div>
                                {g.workers.length > 0 ? (
                                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
                                        {g.workers.map(w => (
                                            <WorkerCard key={w.id} w={w} m={metricsById[w.id]}
                                                revealed={!!revealed[w.id]}
                                                onToggleReveal={() => setRevealed(r => ({ ...r, [w.id]: !r[w.id] }))}
                                                onEdit={() => setEditing(w)}
                                                onDelete={() => deleteWorker(w)}
                                            />
                                        ))}
                                    </div>
                                ) : (
                                    <div style={{ fontSize: 12, color: T.textFaint, padding: "8px 2px" }}>
                                        Sin workers en esta zona.
                                    </div>
                                )}
                            </div>
                        ))}

                        {totalWorkers === 0 && (
                            <div style={{ fontSize: 13, color: T.textMuted, padding: "8px 2px", marginBottom: 20 }}>
                                No hay workers matriculados. Usa "Matricular Servidor" para agregar el primero.
                            </div>
                        )}

                        {/* Crear zona de disponibilidad */}
                        {zoneFilter === "" && (
                            <div style={{ display: "inline-flex", background: T.surfaceElevated, border: `1.5px dashed ${T.borderHover}`, borderRadius: 12, padding: "10px 14px", alignItems: "center", gap: 8 }}>
                                <Globe size={13} color={T.textMuted} />
                                <input value={newZone} onChange={e => setNewZone(e.target.value)}
                                    onKeyDown={e => e.key === "Enter" && addZone()}
                                    placeholder="Nueva zona…"
                                    style={{ ...inp, marginBottom: 0, width: 150, fontSize: 12, padding: "6px 10px" }} />
                                <button onClick={addZone} disabled={!newZone.trim()}
                                    style={btnBase({ padding: "6px 10px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: newZone.trim() ? 1 : 0.5 })}>
                                    <Plus size={12} />
                                </button>
                            </div>
                        )}
                    </>
                )}
            </div>

            {editing && (
                <WorkerModal
                    worker={editing === "new" ? null : editing}
                    zones={zones}
                    onSave={saveWorker}
                    onTest={testWorker}
                    onClose={() => setEditing(null)}
                />
            )}
            {confirmDialog}
        </div>
    );
};
