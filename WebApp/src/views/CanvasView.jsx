import { T, btnBase } from "../theme/tokens";
import { Badge }       from "../components/ui/Badge";
import { UserAvatar }  from "../components/ui/UserAvatar";
import { ThemePicker } from "../components/ui/ThemePicker";
import {
    ArrowLeft, Save, Zap, Flame, Upload, Download, LayoutList,
} from "../components/ui/Icon";
import { AzureVm } from "../components/ui/AzureIcons";

import { Canvas }          from "../components/canvas/Canvas";
import { DeployModal }     from "../components/modals/DeployModal";
import { SaveDraftModal }  from "../components/modals/SaveDraftModal";
import { ConfirmModal }    from "../components/modals/ConfirmModal";
import { ConsoleModal }    from "../components/modals/ConsoleModal";
import { SelectZoneModal } from "../components/modals/SelectZoneModal";
import { Overlay }         from "../components/modals/Overlay";

// ─── CanvasView ───────────────────────────────────────────────────────────────
export const CanvasView = ({
    // Auth / user
    user, token, logout, onProfile, onBack, reTheme,
    // Slice state
    slices, activeId, setActiveId, activeSlice,
    // Designer state (new slice)
    nodes, setNodes, edges, setEdges,
    targetAz, setTargetAz,
    // Slice-editing proxy setters
    setSliceNodes, setSliceEdges,
    // Image data
    imageList,
    // CRUD actions
    destroySlice, deployDraft, deployFromDesigner, saveDraft,
    doDeployDraft, updateDraft,
    // Import / Export
    importarTopologia, exportarTopologia,
    // Modal & UI state
    modal, setModal, consoleVm, setConsoleVm,
    azModalOpen, setAzModalOpen,
    // Helpers
    flash, apiFetch,
    // Leave-design confirmation callbacks
    onConfirmLeaveDiscard, onConfirmLeaveSaveDraft,
    // Mode
    isDesignMode,
}) => {
    // Three states: "slice" (viewing/editing existing), "design" (new slice), "browse" (nothing selected)
    const viewState = activeSlice ? "slice" : isDesignMode ? "design" : "browse";

    return (
        <>
            {/* ── Topbar ───────────────────────────────────────────────── */}
            <div style={{
                padding: "0 20px", height: 54,
                borderBottom: `1px solid ${T.border}`, background: T.surface,
                display: "flex", alignItems: "center", gap: 12,
                flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)",
            }}>
                {viewState === "slice" ? (
                    /* ── Viewing / editing an existing slice ── */
                    <>
                        <button onClick={onBack}
                            style={btnBase({ padding: "5px 12px", fontSize: 11, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                            <ArrowLeft size={13} /> Volver
                        </button>
                        <div style={{ width: 1, height: 22, background: T.border }} />
                        <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>{activeSlice.name}</span>
                        <Badge status={activeSlice.status} />
                        <div style={{ flex: 1 }} />
                        {activeSlice.status === "DRAFT" && (
                            <>
                                <button onClick={updateDraft}
                                    style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surfaceElevated, color: T.text, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                                    <Save size={13} /> Guardar cambios
                                </button>
                                <button onClick={() => deployDraft(activeSlice.id)}
                                    style={btnBase({ fontSize: 12, padding: "6px 16px", background: T.accent, color: "#fff", border: "none", boxShadow: `0 3px 12px ${T.accent}44`, display: "flex", alignItems: "center", gap: 6 })}>
                                    <Zap size={13} /> Desplegar
                                </button>
                            </>
                        )}
                        {activeSlice.status !== "TERMINATED" && (
                            <button onClick={() => destroySlice(activeSlice.id)}
                                style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.redLight, color: T.red, border: `1px solid ${T.red}33`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                                <Flame size={13} /> Eliminar
                            </button>
                        )}
                        <div style={{ width: 1, height: 22, background: T.border }} />
                        <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
                        <ThemePicker onThemeChange={reTheme} />
                    </>
                ) : viewState === "design" ? (
                    /* ── New slice designer ── */
                    <>
                        <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>Diseñador de Topología</span>
                        <span style={{ fontSize: 11, color: T.textMuted }}>— Nuevo Slice</span>
                        <div style={{ flex: 1 }} />
                        <ThemePicker onThemeChange={reTheme} />
                        <label style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surface, color: T.text, boxShadow: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 6 })}>
                            <Upload size={13} /> Importar
                            <input type="file" accept=".json" style={{ display: "none" }} onChange={importarTopologia} />
                        </label>
                        {nodes.length > 0 && (
                            <button onClick={exportarTopologia}
                                style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surface, color: T.text, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                                <Download size={13} /> Exportar
                            </button>
                        )}
                        <div style={{ width: 1, height: 22, background: T.border, margin: "0 4px" }} />
                        {nodes.length > 0 && (
                            <button onClick={() => setModal("draft")}
                                style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surfaceElevated, color: T.textMuted, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                                <Save size={13} /> Guardar Borrador
                            </button>
                        )}
                        <button
                            onClick={() => nodes.length > 0 ? setModal("deploy") : flash("Agregue al menos una VM primero", "error")}
                            style={btnBase({ fontSize: 13, fontWeight: 700, padding: "7px 20px", background: T.accent, color: "#fff", border: "none", boxShadow: `0 4px 16px ${T.accent}44`, display: "flex", alignItems: "center", gap: 6 })}>
                            <Zap size={14} /> Desplegar Slice
                        </button>
                        <div style={{ width: 1, height: 22, background: T.border }} />
                        <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
                    </>
                ) : (
                    /* ── Browse mode — no slice selected ── */
                    <>
                        <LayoutList size={16} color={T.accent} />
                        <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>Mis Slices</span>
                        <div style={{ flex: 1 }} />
                        <ThemePicker onThemeChange={reTheme} />
                        <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
                    </>
                )}
            </div>

            {/* ── Main content area ────────────────────────────────────── */}
            <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                {viewState === "slice" ? (
                    <Canvas nodes={activeSlice.nodes} edges={activeSlice.edges} setNodes={setSliceNodes} setEdges={setSliceEdges} imageList={imageList} activeSlice={activeSlice} onOpenConsole={setConsoleVm} targetAz={targetAz} setTargetAz={setTargetAz} apiFetch={apiFetch} isDesignMode={activeSlice.status === "DRAFT"} />
                ) : viewState === "design" ? (
                    <Canvas nodes={nodes} edges={edges} setNodes={setNodes} setEdges={setEdges} imageList={imageList} activeSlice={null} onOpenConsole={setConsoleVm} targetAz={targetAz} setTargetAz={setTargetAz} apiFetch={apiFetch} onCleared={() => { setTargetAz(""); setAzModalOpen(true); }} isDesignMode={true} />
                ) : (
                    /* ── Browse empty state — prompt to select a slice ── */
                    <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, background: T.bg }}>
                        <AzureVm size={56} />
                        <div style={{ fontSize: 14, fontWeight: 600, color: T.textMuted }}>
                            Seleccione un slice de la barra lateral para visualizarlo
                        </div>
                        <div style={{ fontSize: 12, color: T.textFaint }}>
                            O cree uno nuevo con el botón "Crear Nuevo Slice"
                        </div>
                    </div>
                )}
            </div>

            {/* ── Modals ───────────────────────────────────────────────── */}
            {azModalOpen && (
                <SelectZoneModal onSelect={(azId) => { setTargetAz(azId); setAzModalOpen(false); }} />
            )}
            {modal === "deploy" && (
                <DeployModal userRole={user?.role} nodes={nodes} edges={edges} imageList={imageList} apiFetch={apiFetch} onDeploy={deployFromDesigner} onClose={() => setModal(null)} targetAz={targetAz} />
            )}
            {modal?.type === "deployDraft" && (() => {
                const draft = slices.find(s => s.id === modal.id);
                if (!draft) return null;
                return <DeployModal userRole={user?.role} defaultName={draft.name} nodes={draft.nodes} edges={draft.edges} imageList={imageList} apiFetch={apiFetch} onDeploy={(name, azId, projectId, isDirect) => doDeployDraft(modal.id, azId, projectId, isDirect)} onClose={() => setModal(null)} targetAz={targetAz} />;
            })()}
            {modal === "draft" && (
                <SaveDraftModal nodes={nodes} edges={edges} onSave={saveDraft} onClose={() => setModal(null)} />
            )}
            {modal?.type === "confirm" && (
                <ConfirmModal title={modal.title} msg={modal.msg} onOk={modal.onOk} onCancel={() => setModal(null)} />
            )}

            {/* ── Leave-design confirmation modal ──────────────────────── */}
            {modal?.type === "confirmLeave" && (
                <Overlay>
                    <div onClick={e => e.stopPropagation()} style={{
                        background: T.surface, borderRadius: 14, padding: "28px 30px",
                        width: 380, boxShadow: T.shadowLg || "0 12px 40px rgba(0,0,0,0.25)",
                        border: `1px solid ${T.border}`,
                    }}>
                        <div style={{ fontSize: 16, fontWeight: 700, color: T.text, marginBottom: 8 }}>
                            {modal.title}
                        </div>
                        <div style={{ fontSize: 13, color: T.textMuted, marginBottom: 20, lineHeight: 1.5 }}>
                            {modal.msg}
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            <button onClick={onConfirmLeaveSaveDraft}
                                style={btnBase({ width: "100%", padding: "10px", fontSize: 13, fontWeight: 700, background: T.accent, color: "#fff", border: "none", boxShadow: `0 3px 12px ${T.accent}33`, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                                <Save size={14} /> Guardar como Borrador
                            </button>
                            <button onClick={onConfirmLeaveDiscard}
                                style={btnBase({ width: "100%", padding: "10px", fontSize: 13, fontWeight: 600, background: T.redLight, color: T.red, border: `1px solid ${T.red}33`, boxShadow: "none", display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                                <Flame size={14} /> Descartar y Salir
                            </button>
                            <button onClick={() => setModal(null)}
                                style={btnBase({ width: "100%", padding: "9px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                                Cancelar
                            </button>
                        </div>
                    </div>
                </Overlay>
            )}

            {consoleVm && (
                <ConsoleModal token={token} vm={consoleVm} workerIp={consoleVm.workerIp} workerPort={consoleVm.workerPort} vncPort={consoleVm.vncPort} onClose={() => setConsoleVm(null)} />
            )}
        </>
    );
};
