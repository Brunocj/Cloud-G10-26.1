import { useState, useEffect, useMemo } from "react";

// Theme
import { T, btnBase, getGlobalCss } from "./theme/tokens";

// UI
import { Badge }       from "./components/ui/Badge";
import { Label }       from "./components/ui/Label";
import { Toast }       from "./components/ui/Toast";
import { UserAvatar }  from "./components/ui/UserAvatar";
import { ThemePicker } from "./components/ui/ThemePicker";
import { AzureVm }     from "./components/ui/AzureIcons";
import {
    Cloud, Monitor, Wrench, ChevronDown,
    LayoutList, Image,
    ArrowLeft, Plus, Save, Zap, Flame, Upload, Download,
    BarChart2,
} from "./components/ui/Icon";

// Auth
import { useAuth }   from "./hooks/useAuth";
import { LoginPage } from "./components/auth/LoginPage";

// Canvas
import { Canvas } from "./components/canvas/Canvas";

// Sidebar
import { TemplatePicker } from "./components/sidebar/TemplatePicker";
import { SliceCard }      from "./components/sidebar/SliceCard";
import { ImagePanel }     from "./components/sidebar/ImagePanel";

// Modals
import { DeployModal }    from "./components/modals/DeployModal";
import { SaveDraftModal } from "./components/modals/SaveDraftModal";
import { ConfirmModal }   from "./components/modals/ConfirmModal";
import { ConsoleModal }   from "./components/modals/ConsoleModal";

// Utilities
import { mkSlice, refreshMeta } from "./utils/topology";
import { createApiFetch }       from "./utils/api";

// Profile
import { ProfilePage } from "./components/profile/ProfilePage";

// ─── ROOT ─────────────────────────────────────────────────────────────────────
export default function App() {

    // ── Auth ─────────────────────────────────────────────────────────────────
    // IMPORTANT: useAuth must be called first, but ALL other hooks below must
    // also be declared unconditionally (Rules of Hooks).
    // The early return for unauthenticated users happens AFTER all hooks.
    const { isAuthenticated, user, token, login, logout, isDemoMode } = useAuth();

    // ── apiFetch — fetch autenticado (añade Bearer token automáticamente) ─────
    const apiFetch = useMemo(() => createApiFetch(token), [token]);

    // ── View — "canvas" | "profile" ───────────────────────────────────────────
    const [view, setView] = useState("canvas");

    // ── Theme re-render trigger ───────────────────────────────────────────────
    const [themeRev, setThemeRev] = useState(0);
    const reTheme = () => setThemeRev(v => v + 1);

    // Sidebar tools accordion
    const [showTools, setShowTools] = useState(false);

    // ── App state — always declared regardless of auth status ─────────────────
    const [slices,     setSlices]     = useState([]);
    const [imageList,  setImageList]  = useState([]);
    const [fullImages, setFullImages] = useState([]);
    const [activeId,   setActiveId]   = useState(null);
    const [sidebarTab, setSidebarTab] = useState("slices");

    // Designer canvas (new slice in progress)
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);
    const [targetAz, setTargetAz] = useState("");

    // UI / overlay state
    const [modal,     setModal]     = useState(null);
    const [toast,     setToast]     = useState(null);
    const [consoleVm, setConsoleVm] = useState(null);

    // ── Helpers ───────────────────────────────────────────────────────────────
    const flash = (msg, type = "success") => {
        setToast({ msg, type });
        setTimeout(() => setToast(null), 3500);
    };

    // ── Data fetching — skip when not authenticated ───────────────────────────
    const fetchSlices = async () => {
        try {
            const res = await apiFetch("/slices");
            if (res.ok) setSlices(await res.json());
            else if (res.status === 401) logout();
        } catch (e) { console.error("fetchSlices:", e); }
    };

    const fetchImageList = async () => {
        try {
            const res = await apiFetch("/slices/utils/images");
            if (res.ok) setImageList(await res.json());
        } catch (e) { console.error("fetchImageList:", e); }
    };

    const fetchFullImages = async () => {
        try {
            const res = await apiFetch("/slices/utils/images/");
            if (res.ok) setFullImages(await res.json());
        } catch (e) { console.error("fetchFullImages:", e); }
    };

    // useEffect is always called; the guard inside prevents fetching when logged out
    useEffect(() => {
        if (!isAuthenticated) return;
        fetchSlices();
        fetchImageList();
        fetchFullImages();
        const interval = setInterval(fetchSlices, 8000);
        return () => clearInterval(interval);
    }, [isAuthenticated]); // re-run when auth state changes

    // ── Derived ───────────────────────────────────────────────────────────────
    const activeSlice = slices.find(s => s.id === activeId) ?? null;

    // ── Slice CRUD ────────────────────────────────────────────────────────────
    const updateSlice = (id, patch) =>
        setSlices(p => p.map(s => s.id === id ? refreshMeta({ ...s, ...patch }) : s));

    const destroySlice = (id) => {
        const sl = slices.find(s => s.id === id);
        setModal({
            type: "confirm",
            title: "Eliminar Slice",
            msg: `¿Está seguro de que desea eliminar "${sl?.name}"?`,
            onOk: async () => {
                try {
                    const res  = await apiFetch(`/slices/${id}`, { method: "DELETE" });
                    if (!res.ok) throw new Error();
                    const data = await res.json();
                    if (data.status === "DELETED") {
                        setSlices(prev => prev.filter(s => s.id !== id));
                        if (activeId === id) setActiveId(null);
                    } else {
                        updateSlice(id, { status: "TERMINATED" });
                    }
                    setModal(null);
                    flash(data.message);
                } catch { flash("Error al eliminar", "error"); }
            },
        });
    };

    const deployFromDesigner = async (name, azId = 1) => {
        try {
            const azImageList = imageList.filter(img => img.availability_zone_id == azId || img.availability_zone_id == null);
            const defaultImg = azImageList[0] ?? imageList[0] ?? { id: 1, name: "Cirros" };
            const processedNodes = nodes.map(n => ({
                ...n,
                image_id: n.image_id || defaultImg.id,
                image:    n.image    || defaultImg.name,
            }));

            const resDraft = await apiFetch("/slices/draft", {
                method: "POST",
                body: JSON.stringify({ name, slice_json: { nodes: processedNodes, edges } }),
            });
            if (!resDraft.ok) throw new Error();
            const { slice_id: newSliceId } = await resDraft.json();

            const resDeploy = await apiFetch(`/slices/${newSliceId}/deploy`, {
                method: "POST",
                body: JSON.stringify({ availability_zone_id: azId, ttl_hours: 4, motivo: "Despliegue directo desde Canvas" }),
            });
            if (!resDeploy.ok) {
                const errData = await resDeploy.json().catch(() => ({}));
                flash(errData.detail || "Error al desplegar", "error");
                return;
            }

            const sl = mkSlice(name, "PENDING_APPROVAL", [...nodes], [...edges]);
            sl.id = newSliceId;
            setSlices(p => [sl, ...p]);
            setNodes([]); setEdges([]);
            setModal(null);
            flash(`"${name}" enviado a validación de recursos!`);
        } catch { flash("Error de conexión con el servidor", "error"); }
    };

    const saveDraft = async (name) => {
        try {
            const defaultImg     = imageList[0] ?? { id: 1, name: "Cirros" };
            const processedNodes = nodes.map(n => ({
                ...n,
                image_id: n.image_id || defaultImg.id,
                image:    n.image    || defaultImg.name,
            }));
            const res = await apiFetch("/slices/draft", {
                method: "POST",
                body: JSON.stringify({ name, slice_json: { nodes: processedNodes, edges } }),
            });
            if (!res.ok) throw new Error();
            const { slice_id } = await res.json();
            const sl = mkSlice(name, "Draft", [...nodes], [...edges]);
            sl.id = slice_id;
            setSlices(p => [sl, ...p]);
            setNodes([]); setEdges([]);
            setModal(null);
            flash(`"${name}" guardado como borrador`);
        } catch { flash("Error de conexión con el servidor", "error"); }
    };

    const deployDraft = (id) => {
        setModal({ type: "deployDraft", id });
    };

    const doDeployDraft = async (id, azId) => {
        try {
            const res = await apiFetch(`/slices/${id}/deploy`, {
                method: "POST",
                body: JSON.stringify({ availability_zone_id: azId, ttl_hours: 4, motivo: "Despliegue desde la UI" }),
            });
            if (!res.ok) throw new Error();
            updateSlice(id, { status: "PENDING_APPROVAL" });
            setModal(null);
            flash("Solicitud de despliegue encolada con éxito");
        } catch { flash("Error al solicitar despliegue", "error"); }
    };

    // ── Import / Export ───────────────────────────────────────────────────────
    const exportarTopologia = () => {
        if (nodes.length === 0) { flash("No hay nodos para exportar", "error"); return; }
        const blob = new Blob([JSON.stringify({ vms: nodes, edges }, null, 2)], { type: "application/json" });
        const url  = URL.createObjectURL(blob);
        const a    = Object.assign(document.createElement("a"), { href: url, download: `topologia_pucp_${Date.now()}.json` });
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
        flash("Topología exportada exitosamente");
    };

    const importarTopologia = (event) => {
        const file = event.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const json = JSON.parse(e.target.result);
                if (!json.vms || !Array.isArray(json.vms)) throw new Error();
                const suffix = `_imp_${Date.now().toString().slice(-4)}`;
                const idMap  = {};
                const newNodes = json.vms.map(n => {
                    const newId = `${n.id}${suffix}`;
                    idMap[n.id] = newId;
                    return { ...n, id: newId, x: (n.x ?? 0) + 50, y: (n.y ?? 0) + 50 };
                });
                const newEdges = (json.edges ?? []).map(ed => ({
                    ...ed, id: `${ed.id}${suffix}`,
                    from: idMap[ed.from] ?? ed.from,
                    to:   idMap[ed.to]   ?? ed.to,
                }));
                setNodes(prev => [...prev, ...newNodes]);
                setEdges(prev => [...prev, ...newEdges]);
                flash("Topología importada y añadida al lienzo actual");
            } catch { flash("El archivo no es una topología válida.", "error"); }
        };
        event.target.value = null;
        reader.readAsText(file);
    };

    // Proxy setters for slice-view mode
    const setSliceNodes = fn => setSlices(p => p.map(s =>
        s.id === activeId ? refreshMeta({ ...s, nodes: typeof fn === "function" ? fn(s.nodes) : fn }) : s
    ));
    const setSliceEdges = fn => setSlices(p => p.map(s =>
        s.id === activeId ? refreshMeta({ ...s, edges: typeof fn === "function" ? fn(s.edges) : fn }) : s
    ));

    // ── AUTH GATE — must come AFTER all hooks ─────────────────────────────────
    if (!isAuthenticated) {
        return <LoginPage onLogin={login} isDemoMode={isDemoMode} />;
    }

    // ── Profile view ──────────────────────────────────────────────────────────
    if (view === "profile") {
        return (
            <div style={{ display: "flex", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text, overflow: "hidden" }}>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
                    {/* Slim topbar */}
                    <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                        <div style={{ width: 38, height: 38, borderRadius: 10, background: T.accentLight, border: `1.5px solid ${T.accent}44`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                            <Cloud size={20} color={T.accent} />
                        </div>
                        <span style={{ fontSize: 14, fontWeight: 800, color: T.text }}>PUCP Cloud</span>
                        <div style={{ flex: 1 }} />
                        <ThemePicker onThemeChange={reTheme} />
                        <UserAvatar user={user} onLogout={logout} onProfile={() => setView("profile")} />
                    </div>
                    <ProfilePage user={user} onBack={() => setView("canvas")} />
                </div>
                <style key={themeRev}>{getGlobalCss()}</style>
            </div>
        );
    }

    // ── Render ────────────────────────────────────────────────────────────────
    return (
        <div style={{ display: "flex", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text, overflow: "hidden" }}>

            {/* ── SIDEBAR ──────────────────────────────────────────────────── */}
            <div style={{ width: 268, background: T.surface, borderRight: `1px solid ${T.border}`, display: "flex", flexDirection: "column", flexShrink: 0, boxShadow: "2px 0 10px rgba(20,50,22,0.07)" }}>

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

                {/* ── Design Tools accordion (designer mode only) ────────────── */}
                {!activeSlice && (
                    <div style={{ flexShrink: 0, borderBottom: `1px solid ${T.border}` }}>
                        {/* Accordion toggle */}
                        <button
                            onClick={() => setShowTools(v => !v)}
                            style={{
                                width: "100%", padding: "9px 16px",
                                display: "flex", alignItems: "center", justifyContent: "space-between",
                                background: showTools ? T.accentLight : "transparent",
                                border: "none", cursor: "pointer", fontFamily: "inherit",
                                color: showTools ? T.accent : T.textMuted,
                                fontSize: 11, fontWeight: 700,
                                transition: "background 0.15s, color 0.15s",
                            }}
                        >
                            <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                <Wrench size={12} /> Herramientas de Diseño
                            </span>
                            <ChevronDown size={13} style={{ transition: "transform 0.2s", transform: showTools ? "rotate(180deg)" : "rotate(0deg)" }} />
                        </button>

                        {/* Collapsible + scrollable body */}
                        {showTools && (
                            <div style={{ maxHeight: 300, overflowY: "auto", borderTop: `1px solid ${T.border}` }}>
                                {/* VM Drag */}
                                <div style={{ padding: "12px 14px 10px" }}>
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
                                <TemplatePicker />
                            </div>
                        )}
                    </div>
                )}

                {/* Slices / Images tab bar */}
                <div style={{ display: "flex", borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
                    {[
                        ["slices", <LayoutList size={11} />, "Slices"],
                        ["images", <Image size={11} />,      "Imágenes"],
                    ].map(([id, icon, label]) => (
                        <button key={id} onClick={() => setSidebarTab(id)}
                            style={{ flex: 1, padding: "9px 4px", fontSize: 11, fontWeight: 700, fontFamily: "inherit",
                                background: sidebarTab === id ? T.accentLight : "transparent",
                                color: sidebarTab === id ? T.accent : T.textMuted,
                                border: "none", borderBottom: sidebarTab === id ? `2px solid ${T.accent}` : "2px solid transparent",
                                cursor: "pointer", transition: "all 0.15s",
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 5 }}>
                            {icon} {label}
                        </button>
                    ))}
                </div>

                {/* Slices tab */}
                {sidebarTab === "slices" && (
                    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                        <div style={{ padding: "12px 16px 8px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
                            <Label style={{ marginBottom: 0 }}>Mis Slices <span style={{ color: T.accent, marginLeft: 5 }}>{slices.length}</span></Label>
                            <button onClick={() => setActiveId(null)}
                                style={btnBase({ padding: "4px 10px", fontSize: 10, background: T.accentLight, color: T.accent, border: `1px solid ${T.accent}44`, boxShadow: "none",
                                    display: "flex", alignItems: "center", gap: 4 })}>
                                <Plus size={11} /> Nuevo
                            </button>
                        </div>
                        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                            {slices.map(sl => (
                                <SliceCard key={sl.id} slice={sl} active={activeId === sl.id}
                                    onClick={() => setActiveId(sl.id)}
                                    onDestroy={destroySlice}
                                    onDeploy={deployDraft} />
                            ))}
                            {slices.length === 0 && <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>Aún no hay slices</div>}
                        </div>
                    </div>
                )}

                {/* Images tab */}
                {sidebarTab === "images" && (
                    <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                        <ImagePanel fullImages={fullImages} onRefresh={fetchFullImages} flash={flash} refreshImageList={fetchImageList} apiFetch={apiFetch} user={user} />
                    </div>
                )}
            </div>

            {/* ── MAIN ─────────────────────────────────────────────────────── */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

                {/* Topbar */}
                <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                    {activeSlice ? (
                        <>
                            <button onClick={() => setActiveId(null)}
                                style={btnBase({ padding: "5px 12px", fontSize: 11, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                                <ArrowLeft size={13} /> Volver
                            </button>
                            <div style={{ width: 1, height: 22, background: T.border }} />
                            <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>{activeSlice.name}</span>
                            <Badge status={activeSlice.status} />
                            <div style={{ flex: 1 }} />
                            {activeSlice.status === "DRAFT" && (
                                <button onClick={() => deployDraft(activeSlice.id)}
                                    style={btnBase({ fontSize: 12, padding: "6px 16px", background: T.accent, color: "#fff", border: "none", boxShadow: `0 3px 12px ${T.accent}44`, display: "flex", alignItems: "center", gap: 6 })}>
                                    <Zap size={13} /> Desplegar
                                </button>
                            )}
                            {activeSlice.status !== "TERMINATED" && (
                                <button onClick={() => destroySlice(activeSlice.id)}
                                    style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.redLight, color: T.red, border: `1px solid ${T.red}33`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                                    <Flame size={13} /> Eliminar
                                </button>
                            )}
                            <div style={{ width: 1, height: 22, background: T.border }} />
                            <UserAvatar user={user} onLogout={logout} onProfile={() => setView("profile")} />
                            <ThemePicker onThemeChange={reTheme} />
                        </>
                    ) : (
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
                            <UserAvatar user={user} onLogout={logout} onProfile={() => setView("profile")} />
                        </>
                    )}
                </div>

                {/* Canvas */}
                <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                    {activeSlice
                        ? <Canvas nodes={activeSlice.nodes} edges={activeSlice.edges} setNodes={setSliceNodes} setEdges={setSliceEdges} imageList={imageList} activeSlice={activeSlice} onOpenConsole={setConsoleVm} targetAz={targetAz} setTargetAz={setTargetAz} />
                        : <Canvas nodes={nodes}             edges={edges}             setNodes={setNodes}      setEdges={setEdges}      imageList={imageList} activeSlice={null}        onOpenConsole={setConsoleVm} targetAz={targetAz} setTargetAz={setTargetAz} />
                    }
                </div>
            </div>

            {/* ── MODALS ───────────────────────────────────────────────────── */}
            {modal === "deploy"        && <DeployModal    nodes={nodes} edges={edges} imageList={imageList} apiFetch={apiFetch} onDeploy={deployFromDesigner} onClose={() => setModal(null)} targetAz={targetAz} />}
            {modal?.type === "deployDraft" && (() => {
                const draft = slices.find(s => s.id === modal.id);
                if (!draft) return null;
                return <DeployModal defaultName={draft.name} nodes={draft.nodes} edges={draft.edges} imageList={imageList} apiFetch={apiFetch} onDeploy={(name, azId) => doDeployDraft(modal.id, azId)} onClose={() => setModal(null)} targetAz={targetAz} />;
            })()}
            {modal === "draft"         && <SaveDraftModal nodes={nodes} edges={edges} onSave={saveDraft}            onClose={() => setModal(null)} />}
            {modal?.type === "confirm" && <ConfirmModal title={modal.title} msg={modal.msg} onOk={modal.onOk} onCancel={() => setModal(null)} />}
            {consoleVm                 && <ConsoleModal vm={consoleVm} workerIp={consoleVm.workerIp} workerPort={consoleVm.workerPort} vncPort={consoleVm.vncPort} onClose={() => setConsoleVm(null)} />}

            {toast && <Toast {...toast} />}
            <style key={themeRev}>{getGlobalCss()}</style>
        </div>
    );
}
