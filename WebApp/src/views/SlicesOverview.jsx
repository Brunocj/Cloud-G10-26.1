/**
 * SlicesOverview — rejilla con TODOS los slices visibles para el usuario.
 *
 * Ocupa el sitio que antes tenía el estado vacío de "browse" ("Seleccione un
 * slice de la barra lateral"), que desperdiciaba la pantalla completa: la
 * barra lateral solo da una lista estrecha y truncada, y con muchos slices
 * obliga a desplazarse a ciegas.
 *
 * No hace peticiones propias — consume el mismo array `slices` que ya tiene
 * CanvasView, así que no añade carga al SliceManager.
 */
import { useState, useMemo } from "react";
import { T, btnBase, inp } from "../theme/tokens";
import { Badge } from "../components/ui/Badge";
import { AzureVm } from "../components/ui/AzureIcons";
import { Search, Server, Cpu, MemoryStick, Clock, Globe } from "../components/ui/Icon";

const ZONES = { 1: "Linux Cluster", 2: "OpenStack" };

// Mismo orden de prioridad que usa la barra lateral, para que la rejilla no
// contradiga el orden que el usuario ya vio ahí.
const STATUS_ORDER = {
    ACTIVE: 0, PROVISIONING: 1, PENDING_APPROVAL: 2, DRAFT: 3,
    ROLLING_BACK: 4, TERMINATING: 5, FAILED: 6, REJECTED: 7, TERMINATED: 8,
};

const FILTERS = [
    ["",             "Todos"],
    ["ACTIVE",       "Activos"],
    ["PROVISIONING", "En despliegue"],
    ["DRAFT",        "Borradores"],
    ["TERMINATED",   "Terminados"],
];

const Metric = ({ icon: Icon, value, label }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}>
        <Icon size={12} color={T.textMuted} />
        <span style={{ fontSize: 12, fontWeight: 700, color: T.text }}>{value}</span>
        <span style={{ fontSize: 10, color: T.textMuted }}>{label}</span>
    </div>
);

export const SlicesOverview = ({ slices, onOpen, onNewSlice, canSeeOwner }) => {
    const [query, setQuery]   = useState("");
    const [filter, setFilter] = useState("");

    const shown = useMemo(() => {
        const q = query.trim().toLowerCase();
        return [...slices]
            .filter(s => !filter || s.status === filter)
            .filter(s => !q
                || (s.name || "").toLowerCase().includes(q)
                || (s.project_name || "").toLowerCase().includes(q)
                || (s.owner_name || "").toLowerCase().includes(q))
            .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
                || (b.id - a.id));
    }, [slices, query, filter]);

    // Recuento por estado para las pastillas de filtro
    const counts = useMemo(() => {
        const c = {};
        for (const s of slices) c[s.status] = (c[s.status] || 0) + 1;
        return c;
    }, [slices]);

    return (
        <div style={{ flex: 1, overflowY: "auto", background: T.bg, padding: "20px 24px" }}>

            {/* Cabecera: buscador + filtros */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 18 }}>
                <div style={{ position: "relative", width: 260 }}>
                    <Search size={13} color={T.textMuted}
                        style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
                    <input value={query} onChange={e => setQuery(e.target.value)}
                        aria-label="Buscar slices"
                        placeholder="Buscar por nombre, proyecto o dueño…"
                        style={{ ...inp, padding: "7px 10px 7px 30px", fontSize: 12 }} />
                </div>

                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                    {FILTERS.map(([value, label]) => {
                        const n = value ? (counts[value] || 0) : slices.length;
                        const on = filter === value;
                        return (
                            <button key={value || "all"} onClick={() => setFilter(value)}
                                aria-pressed={on}
                                style={btnBase({
                                    padding: "4px 11px", fontSize: 11, fontWeight: 700, boxShadow: "none",
                                    background: on ? T.accent : T.surface,
                                    color:      on ? "#fff"   : T.textMuted,
                                    border: `1px solid ${on ? T.accent : T.border}`,
                                })}>
                                {label} <span style={{ opacity: 0.75 }}>({n})</span>
                            </button>
                        );
                    })}
                </div>

                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 11, color: T.textMuted }}>
                    {shown.length} de {slices.length}
                </span>
            </div>

            {/* Rejilla — auto-fill para que se adapte al ancho disponible, que
                cambia cuando el usuario redimensiona la barra lateral. */}
            {shown.length > 0 ? (
                <div style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(268px, 1fr))",
                    gap: 14,
                }}>
                    {shown.map(s => (
                        <button key={s.id} onClick={() => onOpen(s.id)}
                            style={{
                                textAlign: "left", cursor: "pointer", fontFamily: "inherit",
                                background: T.surface, border: `1px solid ${T.border}`,
                                borderRadius: 12, padding: "14px 16px",
                                display: "flex", flexDirection: "column", gap: 10,
                                boxShadow: T.shadow, transition: "border-color 0.15s, transform 0.15s",
                            }}
                            onMouseEnter={e => {
                                e.currentTarget.style.borderColor = T.accent;
                                e.currentTarget.style.transform = "translateY(-2px)";
                            }}
                            onMouseLeave={e => {
                                e.currentTarget.style.borderColor = T.border;
                                e.currentTarget.style.transform = "none";
                            }}>

                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <AzureVm size={20} />
                                <span style={{
                                    fontSize: 13.5, fontWeight: 700, color: T.text,
                                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                }}>
                                    {s.name || `Slice ${s.id}`}
                                </span>
                                <div style={{ flex: 1 }} />
                                <Badge status={s.status} />
                            </div>

                            <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
                                <Metric icon={Server}      value={s.nodeCount ?? 0} label="VMs" />
                                <Metric icon={Cpu}         value={s.vcpus ?? 0}     label="vCPU" />
                                <Metric icon={MemoryStick} value={s.ramLabel ?? "—"} label="" />
                            </div>

                            <div style={{
                                display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
                                fontSize: 10.5, color: T.textMuted,
                                borderTop: `1px solid ${T.border}`, paddingTop: 9,
                            }}>
                                <Globe size={11} color={T.textMuted} />
                                {ZONES[s.availability_zone_id] || "Zona —"}
                                {s.project_name && <>· {s.project_name}</>}
                                {canSeeOwner && s.owner_name && <>· {s.owner_name}</>}
                                {s.date_deployed && (
                                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, marginLeft: "auto" }}>
                                        <Clock size={11} /> {String(s.date_deployed).slice(0, 16)}
                                    </span>
                                )}
                            </div>
                        </button>
                    ))}
                </div>
            ) : (
                <div style={{
                    display: "flex", flexDirection: "column", alignItems: "center",
                    justifyContent: "center", gap: 10, padding: "70px 0",
                }}>
                    <AzureVm size={48} />
                    <div style={{ fontSize: 13.5, fontWeight: 600, color: T.textMuted }}>
                        {slices.length === 0
                            ? "Todavía no tienes slices"
                            : "Ningún slice coincide con el filtro"}
                    </div>
                    {slices.length === 0 && onNewSlice && (
                        <button onClick={onNewSlice}
                            style={btnBase({
                                marginTop: 4, fontSize: 12, fontWeight: 700, padding: "8px 18px",
                                background: T.accent, color: "#fff", border: "none",
                            })}>
                            Crear tu primer slice
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};
