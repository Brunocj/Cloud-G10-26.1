/**
 * MaintenanceView — Mantenimiento de BD de slices/VMs. Solo superAdmin.
 *
 * Consume GET/POST /api/v1/maintenance/* (SliceManager → app/services/db_maintenance.py):
 *  - GET  /maintenance/scan   reporte de solo lectura de filas huérfanas/inconsistentes.
 *  - POST /maintenance/clean  corrige las categorías seleccionadas (dry_run=true por defecto).
 *
 * No toca infraestructura física (SSH/NATS) — solo bookkeeping en MySQL. Las
 * "operaciones colgadas" (TERMINATING/PROVISIONING) se muestran solo a modo
 * informativo: ya las procesa `stuck_ops_scheduler` con su lógica segura de
 * reintentos/reversión, así que nunca se ofrecen para "limpiar" desde acá.
 */
import { useState, useEffect } from "react";
import { T, btnBase, FONT_STACK } from "../theme/tokens";
import { UserAvatar } from "../components/ui/UserAvatar";
import {
    ArrowLeft, Database, RefreshCw, AlertTriangle, CheckCircle,
    Trash2, ChevronDown, Clock, Loader,
} from "../components/ui/Icon";
import { useConfirm } from "../hooks/useConfirm";

const CATEGORY_META = {
    orphan_vms: {
        label: "VMs huérfanas",
        desc: "La VM referencia un slice que ya no existe en la BD.",
        itemLine: (it) => `#${it.id} "${it.name}" — slice_id=${it.slice_id} (${it.state})`,
    },
    orphan_vlans: {
        label: "VLANs huérfanas",
        desc: "La VLAN referencia un slice que ya no existe en la BD.",
        itemLine: (it) => `#${it.id} tipo=${it.type} vlan=${it.vlan_number ?? "—"} — slice_id=${it.slice_id}`,
    },
    stale_ip_pool: {
        label: "IPs no liberadas",
        desc: 'IP marcada como "en uso" sin una VM viva detrás.',
        itemLine: (it) => `${it.ip_address} — vm_id=${it.vm_id ?? "—"}`,
    },
    zombie_vms: {
        label: "VMs zombie",
        desc: "VM en estado vivo cuyo slice ya terminó — retiene su puerto VNC indefinidamente.",
        itemLine: (it) => `#${it.id} "${it.name}" (${it.state}) — slice #${it.slice_id} es ${it.slice_status}`,
    },
    terminated_vms: {
        label: "VMs terminadas (historial)",
        desc: "VM en estado TERMINATED — instancia ya destruida. No afecta el historial del slice.",
        itemLine: (it) => `#${it.id} "${it.name}" — slice_id=${it.slice_id}`,
    },
    terminated_slices: {
        label: "Slices terminados (historial)",
        desc: "Slice en estado TERMINATED — borra el slice completo junto con sus VMs y VLANs residuales.",
        itemLine: (it) => `#${it.id} "${it.name}"${it.date_destruction ? ` — destruido ${it.date_destruction}` : ""}`,
    },
    empty_drafts: {
        label: "Borradores vacíos",
        desc: "Slice en borrador (DRAFT) sin ninguna VM asociada.",
        itemLine: (it) => `#${it.id} "${it.name}"`,
    },
};

const STUCK_ITEM_LINE = (it) =>
    `#${it.id} "${it.name}" — ${it.status}${it.pending ? "" : " (sin operación pendiente registrada)"}`;

// ─── Card de una categoría corregible (con checkbox de selección) ─────────────
const CategoryCard = ({ meta, data, selected, onToggleSelect, expanded, onToggleExpand }) => {
    const count = data?.count ?? 0;
    return (
        <div style={{
            background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12,
            padding: "14px 16px", boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
        }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input type="checkbox" checked={selected} disabled={count === 0}
                    onChange={onToggleSelect}
                    style={{ width: 15, height: 15, accentColor: T.accent, cursor: count === 0 ? "default" : "pointer", flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: T.text }}>{meta.label}</div>
                    <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>{meta.desc}</div>
                </div>
                <div style={{ fontSize: 16, fontWeight: 800, color: count > 0 ? "#ef4444" : "#16a34a" }}>{count}</div>
                {count > 0 && (
                    <button onClick={onToggleExpand} title={expanded ? "Ocultar detalle" : "Ver detalle"}
                        style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <ChevronDown size={14} style={{ transition: "transform 0.2s", transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }} />
                    </button>
                )}
            </div>
            {expanded && count > 0 && (
                <div style={{
                    marginTop: 10, paddingTop: 10, borderTop: `1px dashed ${T.border}`,
                    maxHeight: 160, overflowY: "auto",
                    fontFamily: "'JetBrains Mono','Fira Code',Consolas,monospace", fontSize: 11,
                    display: "flex", flexDirection: "column", gap: 4,
                }}>
                    {data.items.map((it, i) => (
                        <div key={i} style={{ color: T.textMuted }}>{meta.itemLine(it)}</div>
                    ))}
                </div>
            )}
        </div>
    );
};

// ─── MaintenanceView ───────────────────────────────────────────────────────────
export const MaintenanceView = ({ user, onBack, onProfile, apiFetch, flash }) => {
    const [askConfirm, confirmDialog] = useConfirm();
    const [scan, setScan]       = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError]     = useState(null);
    const [scanning, setScanning] = useState(false);
    const [selected, setSelected] = useState(new Set());
    const [expanded, setExpanded] = useState({});
    const [preview, setPreview]   = useState(null);   // resumen del último dry-run
    const [applying, setApplying] = useState(false);
    const [lastScanAt, setLastScanAt] = useState(null);

    const runScan = async () => {
        setScanning(true);
        try {
            const res = await apiFetch("/maintenance/scan");
            if (res.ok) {
                const data = await res.json();
                setScan(data);
                setError(null);
                setLastScanAt(new Date());
                // Preseleccionar solo las categorías que sí tienen filas para tocar
                setSelected(new Set(Object.keys(CATEGORY_META).filter(k => (data[k]?.count ?? 0) > 0)));
                setPreview(null);
            } else {
                setError(`Error ${res.status} al escanear la base de datos`);
            }
        } catch {
            setError("No se pudo conectar al servidor");
        }
        setLoading(false);
        setScanning(false);
    };

    useEffect(() => { runScan(); }, []);

    const toggleSelect = (key) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });

    const toggleExpand = (key) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

    const totalSelectedCount = [...selected].reduce((s, k) => s + (scan?.[k]?.count ?? 0), 0);

    // La confirmación se pide fuera de doClean: un modal de React no bloquea la
    // ejecución como hacía window.confirm, así que el trabajo tiene que quedar
    // en una función aparte que se invoca desde el callback de aceptar.
    const runClean = (dryRun) => {
        if (selected.size === 0) return;
        if (dryRun) { doClean(true); return; }
        askConfirm({
            title: "Aplicar limpieza",
            msg: `Vas a aplicar la limpieza sobre ${totalSelectedCount} fila(s) en: `
                + [...selected].map(k => CATEGORY_META[k].label).join(", ")
                + ". Esto NO toca infraestructura física (SSH/NATS), solo filas de la base de datos.",
            confirmLabel: "Sí, aplicar",
            onOk: () => doClean(false),
        });
    };

    const doClean = async (dryRun) => {
        setApplying(true);
        try {
            const res = await apiFetch("/maintenance/clean", {
                method: "POST",
                body: JSON.stringify({ categories: [...selected], dry_run: dryRun }),
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                if (dryRun) {
                    setPreview(data.summary);
                    flash("Vista previa generada — ninguna fila fue modificada");
                } else {
                    setPreview(null);
                    const totalFixed = Object.values(data.summary).reduce((a, b) => a + b, 0);
                    flash(`Limpieza aplicada: ${totalFixed} fila(s) corregidas`);
                    runScan();
                }
            } else {
                flash(data.detail || "Error al ejecutar la limpieza", "error");
            }
        } catch {
            flash("Error de conexión con el servidor", "error");
        }
        setApplying(false);
    };

    const totalIssues = Object.keys(CATEGORY_META).reduce((s, k) => s + (scan?.[k]?.count ?? 0), 0);
    const stuck = scan?.stuck_operations;

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: FONT_STACK, color: T.text }}>
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
                <Database size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800 }}>Mantenimiento de BD</span>
                <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 20, background: "#ff820022", color: "#ff8200", border: "1px solid #ff820044" }}>
                    SUPERADMIN
                </span>
                <button onClick={runScan} title="Reescanear" aria-label="Reescanear" disabled={scanning}
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted, opacity: scanning ? 0.5 : 1 })}>
                    {scanning ? <Loader size={14} style={{ animation: "spin 0.8s linear infinite" }} /> : <RefreshCw size={14} />}
                </button>
                <div style={{ flex: 1 }} />
                {lastScanAt && (
                    <span style={{ fontSize: 10, color: T.textFaint }}>Último escaneo: {lastScanAt.toLocaleTimeString()}</span>
                )}
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px" }}>
                {loading ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted, fontSize: 13 }}>
                        Escaneando la base de datos...
                    </div>
                ) : error ? (
                    <div style={{ textAlign: "center", padding: 60 }}>
                        <AlertTriangle size={32} color="#ef4444" />
                        <div style={{ fontSize: 14, color: "#ef4444", marginTop: 12, fontWeight: 600 }}>{error}</div>
                    </div>
                ) : (
                    <>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, fontSize: 12, color: T.textMuted }}>
                            {totalIssues === 0 ? (
                                <><CheckCircle size={14} color="#16a34a" /> No se detectaron filas huérfanas ni inconsistentes.</>
                            ) : (
                                <><AlertTriangle size={14} color="#f59e0b" /> Se detectaron {totalIssues} fila(s) corregibles.</>
                            )}
                        </div>

                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 12, marginBottom: 16 }}>
                            {Object.entries(CATEGORY_META).map(([key, meta]) => (
                                <CategoryCard key={key} meta={meta} data={scan?.[key]}
                                    selected={selected.has(key)} onToggleSelect={() => toggleSelect(key)}
                                    expanded={!!expanded[key]} onToggleExpand={() => toggleExpand(key)} />
                            ))}
                        </div>

                        {/* Operaciones colgadas — informativo, sin checkbox ni acción */}
                        {stuck && (
                            <div style={{
                                background: stuck.count > 0 ? T.yellowLight : T.surface,
                                border: `1px solid ${stuck.count > 0 ? T.yellow + "55" : T.border}`,
                                borderRadius: 12, padding: "14px 16px", marginBottom: 20,
                            }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                    <Clock size={15} color={stuck.count > 0 ? T.yellow : T.textMuted} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: 13, fontWeight: 700, color: T.text }}>Operaciones colgadas (informativo)</div>
                                        <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>{stuck.note}</div>
                                    </div>
                                    <div style={{ fontSize: 16, fontWeight: 800, color: stuck.count > 0 ? T.yellow : "#16a34a" }}>{stuck.count}</div>
                                    {stuck.count > 0 && (
                                        <button onClick={() => toggleExpand("stuck_operations")} title="Ver detalle"
                                            style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                                            <ChevronDown size={14} style={{ transition: "transform 0.2s", transform: expanded.stuck_operations ? "rotate(180deg)" : "rotate(0deg)" }} />
                                        </button>
                                    )}
                                </div>
                                {expanded.stuck_operations && stuck.count > 0 && (
                                    <div style={{
                                        marginTop: 10, paddingTop: 10, borderTop: `1px dashed ${T.yellow}55`,
                                        fontFamily: "'JetBrains Mono','Fira Code',Consolas,monospace", fontSize: 11,
                                        display: "flex", flexDirection: "column", gap: 4,
                                    }}>
                                        {stuck.items.map((it, i) => (
                                            <div key={i} style={{ color: T.textMuted }}>{STUCK_ITEM_LINE(it)}</div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Vista previa del último dry-run */}
                        {preview && (
                            <div style={{
                                background: T.accentLight, border: `1px solid ${T.accent}44`, borderRadius: 12,
                                padding: "12px 16px", marginBottom: 20, fontSize: 12,
                            }}>
                                <div style={{ fontWeight: 700, color: T.accent, marginBottom: 6 }}>
                                    Vista previa — ninguna fila fue modificada todavía:
                                </div>
                                {Object.entries(preview).map(([k, v]) => (
                                    <div key={k} style={{ color: T.textMuted }}>
                                        {CATEGORY_META[k]?.label ?? k}: <b style={{ color: T.text }}>{v}</b> fila(s)
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Acciones */}
                        <div style={{ display: "flex", gap: 10 }}>
                            <button onClick={() => runClean(true)} disabled={applying || selected.size === 0}
                                style={btnBase({
                                    padding: "9px 16px", fontSize: 12, fontWeight: 700,
                                    background: T.surfaceElevated, color: T.accent, border: `1px solid ${T.accent}55`,
                                    display: "flex", alignItems: "center", gap: 6,
                                    opacity: (applying || selected.size === 0) ? 0.5 : 1,
                                })}>
                                {applying ? <Loader size={13} style={{ animation: "spin 0.8s linear infinite" }} /> : null}
                                Vista previa (dry-run)
                            </button>
                            <button onClick={() => runClean(false)} disabled={applying || selected.size === 0}
                                style={btnBase({
                                    padding: "9px 16px", fontSize: 12, fontWeight: 700,
                                    background: "#ef4444", color: "#fff", border: "none",
                                    display: "flex", alignItems: "center", gap: 6,
                                    opacity: (applying || selected.size === 0) ? 0.5 : 1,
                                })}>
                                {applying ? <Loader size={13} style={{ animation: "spin 0.8s linear infinite" }} /> : <Trash2 size={13} />}
                                {applying ? "Aplicando…" : "Aplicar limpieza"}
                            </button>
                        </div>
                    </>
                )}
            </div>
            {confirmDialog}
        </div>
    );
};
