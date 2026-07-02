import { T, btnBase } from "../../theme/tokens";
import { Label }       from "../ui/Label";
import { AzureVm }     from "../ui/AzureIcons";
import {
    Cloud, Wrench,
    LayoutList, Image, Plus, ArrowLeft,
} from "../ui/Icon";

import { TemplatePicker } from "./TemplatePicker";
import { SliceCard }      from "./SliceCard";
import { ImagePanel }     from "./ImagePanel";

// ─── Sidebar ──────────────────────────────────────────────────────────────────
// Two modes:
//   "browse"  — default: slice list + buttons to enter designer or image manager
//   "design"  — active when designing a new slice or editing a DRAFT
//   "images"  — active when managing images (admin/superAdmin/jefeProyecto only)
export const Sidebar = ({
    // Mode
    sidebarMode, setSidebarMode,
    // Slice list
    slices, activeId, setActiveId,
    onNewSlice, onDestroySlice, onDeployDraft,
    // Active slice (for viewing existing)
    activeSlice,
    // Images
    fullImages, fetchFullImages, fetchImageList,
    // Shared
    apiFetch, user, flash,
}) => {

    const isAdmin = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";

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

            {/* ───────────────────────────────────────────────────────────── */}
            {/* MODE: DESIGN — tools for building topologies               */}
            {/* ───────────────────────────────────────────────────────────── */}
            {sidebarMode === "design" && (
                <>
                    {/* Back button */}
                    <button
                        onClick={() => setSidebarMode("browse")}
                        style={{
                            width: "100%", padding: "10px 16px",
                            display: "flex", alignItems: "center", gap: 6,
                            background: "transparent", border: "none", borderBottom: `1px solid ${T.border}`,
                            cursor: "pointer", fontFamily: "inherit",
                            color: T.textMuted, fontSize: 11, fontWeight: 700,
                            transition: "background 0.15s",
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = T.accentLight}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                        <ArrowLeft size={12} /> Volver a Slices
                    </button>

                    {/* Section header */}
                    <div style={{ padding: "12px 16px 6px", display: "flex", alignItems: "center", gap: 6 }}>
                        <Wrench size={12} color={T.accent} />
                        <span style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: "uppercase", letterSpacing: "0.06em" }}>Herramientas de Diseño</span>
                    </div>

                    {/* VM Drag */}
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

                    {/* Templates */}
                    <div style={{ flex: 1, overflowY: "auto", borderTop: `1px solid ${T.border}` }}>
                        <TemplatePicker />
                    </div>
                </>
            )}

            {/* ───────────────────────────────────────────────────────────── */}
            {/* MODE: IMAGES — image management (admin roles only)          */}
            {/* ───────────────────────────────────────────────────────────── */}
            {sidebarMode === "images" && (
                <>
                    {/* Back button */}
                    <button
                        onClick={() => setSidebarMode("browse")}
                        style={{
                            width: "100%", padding: "10px 16px",
                            display: "flex", alignItems: "center", gap: 6,
                            background: "transparent", border: "none", borderBottom: `1px solid ${T.border}`,
                            cursor: "pointer", fontFamily: "inherit",
                            color: T.textMuted, fontSize: 11, fontWeight: 700,
                            transition: "background 0.15s",
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = T.accentLight}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                        <ArrowLeft size={12} /> Volver a Slices
                    </button>

                    <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                        <ImagePanel fullImages={fullImages} onRefresh={fetchFullImages} flash={flash} refreshImageList={fetchImageList} apiFetch={apiFetch} user={user} />
                    </div>
                </>
            )}

            {/* ───────────────────────────────────────────────────────────── */}
            {/* MODE: BROWSE — default: navigate slices + action buttons    */}
            {/* ───────────────────────────────────────────────────────────── */}
            {sidebarMode === "browse" && (
                <>
                    {/* Action buttons */}
                    <div style={{ padding: "12px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
                        <button onClick={onNewSlice}
                            style={btnBase({
                                width: "100%", padding: "9px 12px", fontSize: 12,
                                background: T.accent, color: "#fff", border: "none",
                                boxShadow: `0 3px 12px ${T.accent}33`,
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                            })}>
                            <Plus size={13} /> Crear Nuevo Slice
                        </button>

                        {isAdmin && (
                            <button onClick={() => setSidebarMode("images")}
                                style={btnBase({
                                    width: "100%", padding: "8px 12px", fontSize: 11,
                                    background: T.surfaceElevated, color: T.textMuted,
                                    border: `1px solid ${T.border}`, boxShadow: "none",
                                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                                })}>
                                <Image size={12} /> Gestionar Imágenes
                            </button>
                        )}
                    </div>

                    {/* Slice list */}
                    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                        <div style={{ padding: "10px 16px 6px", display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                            <LayoutList size={12} color={T.accent} />
                            <Label style={{ marginBottom: 0 }}>Mis Slices <span style={{ color: T.accent, marginLeft: 4 }}>{slices.length}</span></Label>
                        </div>
                        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                            {[...slices].sort((a, b) => {
                                const p = { ACTIVE: 0, PROVISIONING: 1, PENDING_APPROVAL: 2, DRAFT: 3, FAILED: 4, TERMINATED: 5 };
                                return (p[a.status] ?? 6) - (p[b.status] ?? 6);
                            }).map(sl => (
                                <SliceCard key={sl.id} slice={sl} active={activeId === sl.id}
                                    onClick={() => setActiveId(sl.id)}
                                    onDestroy={onDestroySlice}
                                    onDeploy={onDeployDraft} />
                            ))}
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
