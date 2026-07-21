import { useState, useEffect, useRef, useCallback } from "react";
import { T, btnBase } from "../../theme/tokens";
import { Label }       from "../ui/Label";
import { AzureVm }     from "../ui/AzureIcons";
import {
    Cloud, Wrench,
    Terminal, LayoutList, Image, Plus, ArrowLeft, Activity, ChevronDown, Users, ShieldCheck, Inbox,
    BarChart2, ClipboardList, Server, Cpu,
} from "../ui/Icon";

import { TemplatePicker } from "./TemplatePicker";
import { SliceCard }      from "./SliceCard";
import { ImagePanel }     from "./ImagePanel";
import { FlavorPanel }    from "./FlavorPanel";

// Statuses considered "active" — shown by default
const ACTIVE_STATUSES = new Set(["ACTIVE", "PROVISIONING", "PENDING_APPROVAL", "REJECTED", "DRAFT"]);

// ─── Medidas ajustables por arrastre ──────────────────────────────────────────
// Ninguna de las dos se persiste a propósito: al recargar la barra vuelve a su
// tamaño por defecto. Son ajustes de momento ("ahora quiero ver más slices"),
// no una preferencia que el usuario quiera arrastrar entre sesiones.
const SIDEBAR_MIN = 200;   // por debajo, los nombres de slice se truncan demasiado
const SIDEBAR_MAX = 480;

const NAV_MIN = 120;       // deja ver al menos "Crear Nuevo Slice" + un par más
const NAV_MAX = 560;

/**
 * Ancho por defecto según el viewport. Vive en JS y no en una media query
 * porque el usuario puede arrastrar el borde: una regla CSS con !important
 * le ganaría al width inline y el arrastre no tendría efecto.
 */
const defaultSidebarWidth = () => {
    const w = typeof window !== "undefined" ? window.innerWidth : 1920;
    if (w <= 1100) return 200;
    if (w <= 1366) return 232;
    return 268;
};

/**
 * Alto por defecto del bloque de navegación: como mucho el 45% de la barra.
 * Con rol superAdmin son 10 botones que ocupan ~500px y empujaban "Mis Slices"
 * fuera de la pantalla — se veía una sola tarjeta por muchos slices que hubiera.
 * Ahora el bloque se recorta y hace scroll propio.
 */
const defaultNavHeight = () => {
    const h = typeof window !== "undefined" ? window.innerHeight : 900;
    return Math.round(Math.min(NAV_MAX, Math.max(NAV_MIN, h * 0.45)));
};

// Secondary action button — accent-colored border/text/icon, matches the cloud icon
const SecondaryBtn = ({ onClick, icon: Icon, label }) => (
    <button onClick={onClick}
        style={btnBase({
            width: "100%", padding: "8px 12px", fontSize: 11,
            background: T.accentLight,
            color: T.accent,
            border: `1px solid ${T.accent}44`,
            boxShadow: "none",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
        })}>
        <Icon size={12} color={T.accent} /> {label}
    </button>
);

export const Sidebar = ({
    sidebarMode, setSidebarMode,
    slices, activeId, setActiveId,
    onNewSlice, onDestroySlice, onDeployDraft,
    activeSlice,
    fullImages, fetchFullImages, fetchImageList,
    apiFetch, user, flash,
    isSuperAdmin, onInfraMonitor, onProjects, onUsersManage,
    onRequests, pendingCount = 0,
    onConsumption, onAudit,
    onLoadTemplate, onPublishTemplate, onLogs,
}) => {
    const isAdmin        = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";
    const isStrictAdmin  = user?.role === "admin" || user?.role === "superAdmin";
    const canSeeProjects = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";
    const [showArchived, setShowArchived] = useState(false);

    // ── Ancho y alto arrastrables ────────────────────────────────────────────
    // Ambos viven en un ref durante el arrastre (se escriben directo en el DOM)
    // y solo se vuelcan a estado al soltar. Así arrastrar no dispara un
    // re-render de toda la lista de slices en cada pixel.
    const [width,     setWidth]     = useState(defaultSidebarWidth);
    const [navHeight, setNavHeight] = useState(defaultNavHeight);

    const asideRef = useRef(null);
    const navRef   = useRef(null);
    const widthRef = useRef(width);
    const navRefPx = useRef(navHeight);
    const dragRef  = useRef(null);   // { axis, start, startValue }
    const [dragging, setDragging] = useState(null);   // "x" | "y" | null

    const applyWidth = (w) => {
        widthRef.current = w;
        if (asideRef.current) asideRef.current.style.width = `${w}px`;
    };
    const applyNav = (h) => {
        navRefPx.current = h;
        if (navRef.current) navRef.current.style.height = `${h}px`;
    };

    // El eje se lee del data-axis del elemento en lugar de currificar el
    // handler: una forma currificada se evaluaría en cada render, y el lint de
    // react-hooks lo marca como acceso a refs durante el render.
    const onHandleDown = (e) => {
        e.preventDefault();
        const axis = e.currentTarget.dataset.axis;
        dragRef.current = axis === "x"
            ? { axis, start: e.clientX, startValue: widthRef.current }
            : { axis, start: e.clientY, startValue: navRefPx.current };
        setDragging(axis);
        e.currentTarget.setPointerCapture(e.pointerId);
    };

    const onHandleMove = (e) => {
        const d = dragRef.current;
        if (!d) return;
        if (d.axis === "x") {
            applyWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN,
                d.startValue + e.clientX - d.start)));
        } else {
            // Techo dinámico: nunca dejar la lista de slices con menos de 120px
            const room = (asideRef.current?.clientHeight ?? window.innerHeight) - 200;
            applyNav(Math.min(Math.min(NAV_MAX, room), Math.max(NAV_MIN,
                d.startValue + e.clientY - d.start)));
        }
    };

    const onHandleUp = () => {
        const d = dragRef.current;
        if (!d) return;
        dragRef.current = null;
        setDragging(null);
        if (d.axis === "x") setWidth(widthRef.current);
        else                setNavHeight(navRefPx.current);
    };

    // Doble clic en un asa → volver al valor por defecto de esta pantalla
    const resetWidth = useCallback(() => {
        const d = defaultSidebarWidth(); applyWidth(d); setWidth(d);
    }, []);
    const resetNav = useCallback(() => {
        const d = defaultNavHeight(); applyNav(d); setNavHeight(d);
    }, []);

    // Si la ventana se encoge, recortar para que la barra no se coma la
    // pantalla ni el bloque de navegación deje sin sitio a los slices.
    useEffect(() => {
        const onResize = () => {
            const capW = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, window.innerWidth * 0.4));
            if (widthRef.current > capW) { applyWidth(capW); setWidth(capW); }
            const capH = Math.max(NAV_MIN, window.innerHeight - 200);
            if (navRefPx.current > capH) { applyNav(capH); setNavHeight(capH); }
        };
        window.addEventListener("resize", onResize);
        return () => window.removeEventListener("resize", onResize);
    }, []);

    const sorted = [...slices].sort((a, b) => {
        const p = { ACTIVE: 0, PROVISIONING: 1, PENDING_APPROVAL: 2, DRAFT: 3, FAILED: 4, TERMINATED: 5 };
        return (p[a.status] ?? 6) - (p[b.status] ?? 6);
    });

    const visibleSlices  = sorted.filter(s => ACTIVE_STATUSES.has(s.status));
    const archivedSlices = sorted.filter(s => !ACTIVE_STATUSES.has(s.status));

    return (
        <div ref={asideRef} className="app-sidebar" style={{
            width, background: T.surface, borderRight: `1px solid ${T.border}`,
            display: "flex", flexDirection: "column", flexShrink: 0,
            boxShadow: "2px 0 10px rgba(0,0,0,0.07)",
            position: "relative",
        }}>
            {/* Asa de redimensionado — franja sobre el borde derecho.
                Se pinta por encima del contenido (zIndex) y ocupa 7px para que
                sea agarrable sin tener que apuntar al borde de 1px. */}
            <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Redimensionar barra lateral (doble clic para restablecer)"
                title="Arrastra para redimensionar · doble clic para restablecer"
                data-axis="x"
                onPointerDown={onHandleDown}
                onPointerMove={onHandleMove}
                onPointerUp={onHandleUp}
                onPointerCancel={onHandleUp}
                onDoubleClick={resetWidth}
                style={{
                    position: "absolute", top: 0, right: -3, bottom: 0, width: 7,
                    cursor: "col-resize", zIndex: 30,
                    background: dragging === "x" ? T.accent : "transparent",
                    opacity: dragging === "x" ? 0.35 : 1,
                    transition: "background 0.15s",
                }}
                onMouseEnter={e => { if (!dragging) e.currentTarget.style.background = `${T.accent}55`; }}
                onMouseLeave={e => { if (!dragging) e.currentTarget.style.background = "transparent"; }}
            />
            {/* Logo */}
            <div style={{ padding: "16px 16px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ width: 38, height: 38, borderRadius: 10, background: T.accentLight, border: `1.5px solid ${T.accent}44`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Cloud size={22} color={T.accent} />
                </div>
                <div>
                    <div style={{ fontSize: 15, fontWeight: 800, color: T.text, letterSpacing: "-0.02em" }}>PUCP Cloud</div>
                    <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: "0.08em", textTransform: "uppercase" }}>Orchestrator</div>
                </div>
            </div>

            {/* ── DESIGN MODE ──────────────────────────────────────────────── */}
            {sidebarMode === "design" && (
                <>
                    <button onClick={() => setSidebarMode("browse")} style={{
                        width: "100%", padding: "10px 16px",
                        display: "flex", alignItems: "center", gap: 6,
                        background: "transparent", border: "none", borderBottom: `1px solid ${T.border}`,
                        cursor: "pointer", fontFamily: "inherit",
                        color: T.textMuted, fontSize: 11, fontWeight: 700, transition: "background 0.15s",
                    }}
                        onMouseEnter={e => e.currentTarget.style.background = T.accentLight}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                        <ArrowLeft size={12} /> Volver a Slices
                    </button>

                    <div style={{ padding: "12px 16px 6px", display: "flex", alignItems: "center", gap: 6 }}>
                        <Wrench size={12} color={T.accent} />
                        <span style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: "uppercase", letterSpacing: "0.06em" }}>Herramientas de Diseño</span>
                    </div>

                    <div style={{ padding: "8px 14px 10px" }}>
                        <Label>Arrastra una VM al lienzo</Label>
                        <div draggable onDragStart={e => e.dataTransfer.setData("nodeType", "vm")}
                            style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", background: T.surfaceElevated, border: `1.5px dashed ${T.borderHover}`, borderRadius: 10, cursor: "grab" }}>
                            <AzureVm size={24} />
                            <div>
                                <div style={{ fontSize: 12, fontWeight: 700, color: T.text }}>Máquina Virtual</div>
                                <div style={{ fontSize: 10, color: T.textMuted }}>Nodo configurable</div>
                            </div>
                        </div>
                    </div>

                    <div style={{ flex: 1, overflowY: "auto", borderTop: `1px solid ${T.border}` }}>
                        <TemplatePicker apiFetch={apiFetch} onLoadTemplate={onLoadTemplate} flash={flash} />
                    </div>
                </>
            )}

            {/* ── IMAGES MODE ─────────────────────────────────────────────── */}
            {sidebarMode === "images" && (
                <>
                    <button onClick={() => setSidebarMode("browse")} style={{
                        width: "100%", padding: "10px 16px",
                        display: "flex", alignItems: "center", gap: 6,
                        background: "transparent", border: "none", borderBottom: `1px solid ${T.border}`,
                        cursor: "pointer", fontFamily: "inherit",
                        color: T.textMuted, fontSize: 11, fontWeight: 700, transition: "background 0.15s",
                    }}
                        onMouseEnter={e => e.currentTarget.style.background = T.accentLight}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                        <ArrowLeft size={12} /> Volver a Slices
                    </button>
                    <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                        <ImagePanel fullImages={fullImages} onRefresh={fetchFullImages} flash={flash} refreshImageList={fetchImageList} apiFetch={apiFetch} user={user} />
                    </div>
                </>
            )}

            {/* ── FLAVORS MODE ────────────────────────────────────────────── */}
            {sidebarMode === "flavors" && (
                <>
                    <button onClick={() => setSidebarMode("browse")} style={{
                        width: "100%", padding: "10px 16px",
                        display: "flex", alignItems: "center", gap: 6,
                        background: "transparent", border: "none", borderBottom: `1px solid ${T.border}`,
                        cursor: "pointer", fontFamily: "inherit",
                        color: T.textMuted, fontSize: 11, fontWeight: 700, transition: "background 0.15s",
                    }}
                        onMouseEnter={e => e.currentTarget.style.background = T.accentLight}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                        <ArrowLeft size={12} /> Volver a Slices
                    </button>
                    <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                        <FlavorPanel apiFetch={apiFetch} user={user} flash={flash} />
                    </div>
                </>
            )}

            {/* ── BROWSE MODE ─────────────────────────────────────────────── */}
            {sidebarMode === "browse" && (
                <>
                    {/* Action buttons — alto acotado y con scroll propio.
                        Con superAdmin son 10 botones (~500px) que antes se
                        quedaban fijos y empujaban "Mis Slices" fuera de la
                        pantalla. El divisor de abajo permite repartir el
                        espacio vertical entre los dos bloques. */}
                    <div ref={navRef} style={{
                        height: navHeight, overflowY: "auto", flexShrink: 0,
                        padding: "12px 14px", display: "flex", flexDirection: "column", gap: 6,
                    }}>
                        {/* Primary — Crear Nuevo Slice */}
                        <button onClick={onNewSlice}
                            style={btnBase({
                                width: "100%", padding: "9px 12px", fontSize: 12,
                                background: T.accent, color: "#fff", border: "none",
                                boxShadow: `0 3px 12px ${T.accent}33`,
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                            })}>
                            <Plus size={13} /> Crear Nuevo Slice
                        </button>

                        {/* Secondary buttons — accent-themed */}
                        {isAdmin && (
                            <button onClick={onRequests}
                                style={btnBase({
                                    width: "100%", padding: "8px 12px", fontSize: 11,
                                    background: pendingCount > 0 ? T.yellowLight : T.accentLight,
                                    color: pendingCount > 0 ? T.yellow : T.accent,
                                    border: `1px solid ${(pendingCount > 0 ? T.yellow : T.accent)}44`,
                                    boxShadow: "none",
                                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                                })}>
                                <Inbox size={12} color={pendingCount > 0 ? T.yellow : T.accent} /> Solicitudes
                                {pendingCount > 0 && (
                                    <span style={{
                                        background: T.red, color: "#fff", fontSize: 9, fontWeight: 800,
                                        borderRadius: 20, padding: "1px 6px", marginLeft: 2,
                                    }}>{pendingCount}</span>
                                )}
                            </button>
                        )}
                        {canSeeProjects && (
                            <SecondaryBtn onClick={onProjects} icon={Users} label="Gestión de Proyectos" />
                        )}
                        {isStrictAdmin && (
                            <SecondaryBtn onClick={onUsersManage} icon={ShieldCheck} label="Gestión de Usuarios" />
                        )}
                        {isAdmin && (
                            <SecondaryBtn onClick={() => setSidebarMode("images")} icon={Image} label="Gestionar Imágenes" />
                        )}
                        {isAdmin && (
                            <SecondaryBtn onClick={() => setSidebarMode("flavors")} icon={Cpu} label="Gestionar Flavors" />
                        )}
                        {isAdmin && (
                            <SecondaryBtn onClick={onConsumption} icon={BarChart2} label="Consumo por Proyecto" />
                        )}
                        {isAdmin && (
                            <SecondaryBtn onClick={onAudit} icon={ClipboardList} label="Bitácora de Eventos" />
                        )}
                        {isSuperAdmin && (
                            <SecondaryBtn onClick={onInfraMonitor} icon={Server} label="Infraestructura" />
                        )}
                        {isSuperAdmin && (
                            <SecondaryBtn onClick={onLogs} icon={Terminal} label="Logs de contenedores" />
                        )}
                    </div>

                    {/* Divisor arrastrable entre navegación y lista de slices.
                        Sustituye al borde fijo que separaba los dos bloques. */}
                    <div
                        role="separator"
                        aria-orientation="horizontal"
                        aria-label="Ajustar el alto del menú de opciones (doble clic para restablecer)"
                        title="Arrastra para repartir el espacio · doble clic para restablecer"
                        data-axis="y"
                        onPointerDown={onHandleDown}
                        onPointerMove={onHandleMove}
                        onPointerUp={onHandleUp}
                        onPointerCancel={onHandleUp}
                        onDoubleClick={resetNav}
                        style={{
                            height: 9, flexShrink: 0, cursor: "row-resize",
                            borderTop: `1px solid ${T.border}`,
                            borderBottom: `1px solid ${T.border}`,
                            background: dragging === "y" ? `${T.accent}55` : T.surfaceElevated,
                            display: "flex", alignItems: "center", justifyContent: "center",
                            transition: "background 0.15s",
                        }}
                        onMouseEnter={e => { if (!dragging) e.currentTarget.style.background = `${T.accent}33`; }}
                        onMouseLeave={e => { if (!dragging) e.currentTarget.style.background = T.surfaceElevated; }}
                    >
                        {/* Agarradera visual: tres puntos, para que se vea que se arrastra */}
                        <svg width="22" height="3" aria-hidden="true">
                            {[3, 11, 19].map(cx => (
                                <circle key={cx} cx={cx} cy="1.5" r="1.5" fill={T.textFaint} />
                            ))}
                        </svg>
                    </div>

                    {/* Slice list */}
                    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                        <div style={{ padding: "10px 16px 6px", display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                            <LayoutList size={12} color={T.accent} />
                            <Label style={{ marginBottom: 0 }}>
                                Mis Slices <span style={{ color: T.accent, marginLeft: 4 }}>{slices.length}</span>
                            </Label>
                        </div>

                        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                            {/* Active/provisioning/draft slices */}
                            {visibleSlices.map(sl => (
                                <SliceCard key={sl.id} slice={sl} active={activeId === sl.id}
                                    onClick={() => setActiveId(sl.id)}
                                    onDestroy={onDestroySlice}
                                    onDeploy={onDeployDraft}
                                    onPublish={isAdmin ? onPublishTemplate : undefined}
                                    showOwner={isAdmin} />
                            ))}

                            {/* Archived slices (FAILED, TERMINATED) */}
                            {archivedSlices.length > 0 && (
                                <>
                                    <button onClick={() => setShowArchived(v => !v)} style={{
                                        display: "flex", alignItems: "center", justifyContent: "space-between",
                                        width: "100%", padding: "6px 8px", marginTop: 4,
                                        background: "transparent", border: "none", borderRadius: 6,
                                        cursor: "pointer", fontFamily: "inherit",
                                        fontSize: 10, fontWeight: 700, color: T.textMuted,
                                        letterSpacing: "0.04em", textTransform: "uppercase",
                                    }}>
                                        <span>{showArchived ? "Ocultar" : "Ver"} terminados / fallidos ({archivedSlices.length})</span>
                                        <ChevronDown size={12} style={{ transition: "transform 0.2s", transform: showArchived ? "rotate(180deg)" : "rotate(0deg)" }} />
                                    </button>

                                    {showArchived && archivedSlices.map(sl => (
                                        <SliceCard key={sl.id} slice={sl} active={activeId === sl.id}
                                            onClick={() => setActiveId(sl.id)}
                                            onDestroy={onDestroySlice}
                                            onDeploy={onDeployDraft}
                                            showOwner={isAdmin} />
                                    ))}
                                </>
                            )}

                            {slices.length === 0 && (
                                <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 24 }}>
                                    Aún no hay slices
                                </div>
                            )}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};
