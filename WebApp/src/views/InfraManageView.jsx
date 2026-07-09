/**
 * InfraManageView — Gestión de infraestructura física (REQ-SA-02 / REQ-SA-03).
 * Solo superAdmin: CRUD de workers (nodos de cómputo) y zonas de disponibilidad.
 */
import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../theme/tokens";
import { UserAvatar } from "../components/ui/UserAvatar";
import { ArrowLeft, Server, Plus, X, Trash2, Edit3, AlertTriangle, Globe, RefreshCw } from "../components/ui/Icon";

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
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
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

export const InfraManageView = ({ user, onBack, onProfile, apiFetch, flash }) => {
    const [workers, setWorkers] = useState([]);
    const [zones, setZones]     = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState(null);
    const [editing, setEditing] = useState(null);   // null | "new" | worker
    const [newZone, setNewZone] = useState("");

    const load = async () => {
        try {
            const [rw, rz] = await Promise.all([apiFetch("/infra/workers"), apiFetch("/infra/zones")]);
            if (rw.ok) setWorkers(await rw.json());
            if (rz.ok) setZones(await rz.json());
            if (!rw.ok || !rz.ok) setError("Error al cargar la infraestructura");
            else setError(null);
        } catch { setError("No se pudo conectar al servidor"); }
        setLoading(false);
    };

    useEffect(() => { load(); }, []);

    const saveWorker = async (form) => {
        const isNew = editing === "new";
        const res = await apiFetch(isNew ? "/infra/workers" : `/infra/workers/${form.id}`, {
            method: isNew ? "POST" : "PUT",
            body: JSON.stringify(form),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            flash(isNew ? `Worker "${form.name}" matriculado` : "Worker actualizado");
            setEditing(null); load();
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

    const deleteWorker = async (w) => {
        if (!window.confirm(`¿Desmatricular el worker "${w.name}"? Esta acción no destruye la máquina física.`)) return;
        const res = await apiFetch(`/infra/workers/${w.id}`, { method: "DELETE" });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { flash(data.message); load(); }
        else flash(data.detail || "No se pudo eliminar", "error");
    };

    const addZone = async () => {
        if (!newZone.trim()) return;
        const res = await apiFetch("/infra/zones", { method: "POST", body: JSON.stringify({ name: newZone.trim() }) });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { flash(`Zona "${newZone.trim()}" creada`); setNewZone(""); load(); }
        else flash(data.detail || "No se pudo crear la zona", "error");
    };

    const deleteZone = async (z) => {
        if (!window.confirm(`¿Eliminar la zona "${z.name}"?`)) return;
        const res = await apiFetch(`/infra/zones/${z.id}`, { method: "DELETE" });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { flash(data.message); load(); }
        else flash(data.detail || "No se pudo eliminar", "error");
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text }}>
            <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <Server size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800 }}>Gestión de Infraestructura</span>
                <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 20, background: "#ff820022", color: "#ff8200", border: "1px solid #ff820044" }}>
                    BARE-METAL · SUPERADMIN
                </span>
                <button onClick={load} title="Refrescar"
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                    <RefreshCw size={14} />
                </button>
                <div style={{ flex: 1 }} />
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "22px 26px" }}>
                {loading && <div style={{ color: T.textMuted, fontSize: 13 }}>Cargando…</div>}
                {error && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.red, fontSize: 13, background: T.redLight, padding: "10px 14px", borderRadius: 8, marginBottom: 14 }}>
                        <AlertTriangle size={15} /> {error}
                    </div>
                )}

                {!loading && (
                    <div style={{ maxWidth: 1000, margin: "0 auto", display: "flex", flexDirection: "column", gap: 22 }}>
                        {/* ── Workers ── */}
                        <div>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                                <div style={{ fontSize: 13, fontWeight: 800, display: "flex", alignItems: "center", gap: 6 }}>
                                    <Server size={14} color={T.accent} /> Nodos de Cómputo ({workers.length})
                                </div>
                                <button onClick={() => setEditing("new")}
                                    style={btnBase({ padding: "7px 14px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none", display: "flex", alignItems: "center", gap: 5 })}>
                                    <Plus size={12} /> Matricular Servidor
                                </button>
                            </div>
                            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden", boxShadow: T.shadow }}>
                                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                                    <thead>
                                        <tr style={{ background: T.surfaceElevated }}>
                                            {["Nombre", "IP", "SSH", "vCPU", "RAM", "Disco", "OC (cpu/ram)", "Zona", "VMs vivas", ""].map(h => (
                                                <th key={h} style={{ padding: "9px 12px", textAlign: "left", fontSize: 10, fontWeight: 800, color: T.textMuted, textTransform: "uppercase", borderBottom: `1px solid ${T.border}` }}>{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {workers.map(w => (
                                            <tr key={w.id} style={{ borderBottom: `1px solid ${T.border}` }}>
                                                <td style={{ padding: "9px 12px", fontWeight: 700 }}>{w.name}</td>
                                                <td style={{ padding: "9px 12px", fontFamily: "monospace", fontSize: 11 }}>{w.ip}</td>
                                                <td style={{ padding: "9px 12px", color: T.textMuted, fontSize: 11 }}>{w.ssh_user ?? "—"}:{w.ssh_port ?? 22}</td>
                                                <td style={{ padding: "9px 12px" }}>{w.cpu ?? "—"}</td>
                                                <td style={{ padding: "9px 12px" }}>{w.ram ? `${Math.round(w.ram)} MB` : "—"}</td>
                                                <td style={{ padding: "9px 12px" }}>{w.disk_gb ? `${w.disk_gb} GB` : "—"}</td>
                                                <td style={{ padding: "9px 12px", color: T.textMuted, fontSize: 11 }}>
                                                    {w.oc_cpu ? `${w.oc_cpu.toFixed(2)} / ${w.oc_ram?.toFixed(2) ?? "—"}` : "por defecto"}
                                                </td>
                                                <td style={{ padding: "9px 12px" }}>
                                                    <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 20, background: T.accentLight, color: T.accent }}>
                                                        {w.zone_name ?? "sin zona"}
                                                    </span>
                                                </td>
                                                <td style={{ padding: "9px 12px", fontWeight: 700, color: w.active_vms > 0 ? T.accent : T.textMuted }}>{w.active_vms}</td>
                                                <td style={{ padding: "9px 12px", whiteSpace: "nowrap" }}>
                                                    <button onClick={() => setEditing(w)} title="Editar"
                                                        style={btnBase({ padding: 5, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                                                        <Edit3 size={13} />
                                                    </button>
                                                    <button onClick={() => deleteWorker(w)} title="Desmatricular"
                                                        style={btnBase({ padding: 5, background: "transparent", boxShadow: "none", color: w.active_vms > 0 ? T.textFaint : T.red })}>
                                                        <Trash2 size={13} />
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                        {workers.length === 0 && (
                                            <tr><td colSpan={10} style={{ padding: 26, textAlign: "center", color: T.textMuted }}>No hay workers matriculados.</td></tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        {/* ── Zonas ── */}
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 800, display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                                <Globe size={14} color={T.accent} /> Zonas de Disponibilidad ({zones.length})
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
                                {zones.map(z => (
                                    <div key={z.id} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, padding: "12px 16px", display: "flex", alignItems: "center", gap: 12, boxShadow: T.shadow }}>
                                        <div>
                                            <div style={{ fontSize: 13, fontWeight: 800 }}>{z.name}</div>
                                            <div style={{ fontSize: 10, color: T.textMuted }}>{z.worker_count} worker(s)</div>
                                        </div>
                                        <button onClick={() => deleteZone(z)} title="Eliminar zona" disabled={z.worker_count > 0}
                                            style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: z.worker_count > 0 ? T.textFaint : T.red })}>
                                            <Trash2 size={13} />
                                        </button>
                                    </div>
                                ))}
                                {/* Añadir zona */}
                                <div style={{ background: T.surfaceElevated, border: `1.5px dashed ${T.borderHover}`, borderRadius: 12, padding: "10px 14px", display: "flex", alignItems: "center", gap: 8 }}>
                                    <input value={newZone} onChange={e => setNewZone(e.target.value)}
                                        onKeyDown={e => e.key === "Enter" && addZone()}
                                        placeholder="Nueva zona…"
                                        style={{ ...inp, marginBottom: 0, width: 140, fontSize: 12, padding: "6px 10px" }} />
                                    <button onClick={addZone} disabled={!newZone.trim()}
                                        style={btnBase({ padding: "6px 10px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: newZone.trim() ? 1 : 0.5 })}>
                                        <Plus size={12} />
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
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
        </div>
    );
};
