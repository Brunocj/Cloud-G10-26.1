import { useState } from "react";
import { T, btnBase } from "../../theme/tokens";
import { Label }       from "../ui/Label";
import { AzureVm }     from "../ui/AzureIcons";
import {
    Cloud, Wrench,
    LayoutList, Image, Plus, ArrowLeft, Activity, ChevronDown, Users, ShieldCheck, Inbox,
    BarChart2, ClipboardList, Server,
} from "../ui/Icon";

import { TemplatePicker } from "./TemplatePicker";
import { SliceCard }      from "./SliceCard";
import { ImagePanel }     from "./ImagePanel";

// Statuses considered "active" — shown by default
const ACTIVE_STATUSES = new Set(["ACTIVE", "PROVISIONING", "PENDING_APPROVAL", "REJECTED", "DRAFT"]);

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
    onConsumption, onAudit, onInfraManage,
    onLoadTemplate, onPublishTemplate,
}) => {
    const isAdmin        = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";
    const isStrictAdmin  = user?.role === "admin" || user?.role === "superAdmin";
    const canSeeProjects = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";
    const [showArchived, setShowArchived] = useState(false);

    const sorted = [...slices].sort((a, b) => {
        const p = { ACTIVE: 0, PROVISIONING: 1, PENDING_APPROVAL: 2, DRAFT: 3, FAILED: 4, TERMINATED: 5 };
        return (p[a.status] ?? 6) - (p[b.status] ?? 6);
    });

    const visibleSlices  = sorted.filter(s => ACTIVE_STATUSES.has(s.status));
    const archivedSlices = sorted.filter(s => !ACTIVE_STATUSES.has(s.status));

    return (
        <div style={{
            width: 268, background: T.surface, borderRight: `1px solid ${T.border}`,
            display: "flex", flexDirection: "column", flexShrink: 0,
            boxShadow: "2px 0 10px rgba(20,50,22,0.07)",
        }}>
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

            {/* ── BROWSE MODE ─────────────────────────────────────────────── */}
            {sidebarMode === "browse" && (
                <>
                    {/* Action buttons */}
                    <div style={{ padding: "12px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
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
                            <SecondaryBtn onClick={onConsumption} icon={BarChart2} label="Consumo por Proyecto" />
                        )}
                        {isAdmin && (
                            <SecondaryBtn onClick={onAudit} icon={ClipboardList} label="Bitácora de Eventos" />
                        )}
                        {isSuperAdmin && (
                            <SecondaryBtn onClick={onInfraMonitor} icon={Activity} label="Monitoreo de Infraestructura" />
                        )}
                        {isSuperAdmin && (
                            <SecondaryBtn onClick={onInfraManage} icon={Server} label="Gestión de Infraestructura" />
                        )}
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
