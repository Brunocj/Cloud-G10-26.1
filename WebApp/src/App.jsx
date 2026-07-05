import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { Routes, Route, Navigate, useNavigate, useLocation } from "react-router-dom";

// Theme
import { T, getGlobalCss } from "./theme/tokens";

// Auth
import { useAuth }   from "./hooks/useAuth";
import { LoginPage } from "./components/auth/LoginPage";

// Layout
import { AppLayout } from "./layouts/AppLayout";

// Views
import { CanvasView }       from "./views/CanvasView";
import { ProfileView }      from "./views/ProfileView";
import { InfraMonitorView } from "./views/InfraMonitorView";
import { ProjectsView }     from "./views/ProjectsView";
import { UsersView }        from "./views/UsersView";

// Sidebar
import { Sidebar } from "./components/sidebar/Sidebar";

// Utilities
import { mkSlice, refreshMeta } from "./utils/topology";
import { createApiFetch }       from "./utils/api";

// ─── ROOT ─────────────────────────────────────────────────────────────────────
export default function App() {

    const navigate = useNavigate();
    const location = useLocation();

    // ── Auth ─────────────────────────────────────────────────────────────────
    const { isAuthenticated, user, token, login, logout, refreshToken, isDemoMode } = useAuth();

    // ── apiFetch ──────────────────────────────────────────────────────────────
    const apiFetch = useMemo(
        () => createApiFetch(token, refreshToken, logout),
        [token, refreshToken, logout]
    );

    // ── Theme ─────────────────────────────────────────────────────────────────
    const [themeRev, setThemeRev] = useState(0);
    const reTheme = () => setThemeRev(v => v + 1);

    // ── App state ─────────────────────────────────────────────────────────────
    const [slices,     setSlices]     = useState([]);
    const [imageList,  setImageList]  = useState([]);
    const [fullImages, setFullImages] = useState([]);
    const [activeId,   setActiveId]   = useState(null);
    const activeIdRef = useRef(null);
    useEffect(() => { activeIdRef.current = activeId; }, [activeId]);

    // Sync activeId from URL on initial load / page refresh
    useEffect(() => {
        const match = location.pathname.match(/^\/slice\/(.+)$/);
        if (match && match[1] !== "new" && !activeId) {
            setActiveId(match[1]);
        }
    }, []);

    // Designer canvas (new slice in progress)
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);
    const [targetAz, setTargetAz] = useState("");

    // UI / overlay state
    const [modal,      setModal]      = useState(null);
    const [toast,      setToast]      = useState(null);
    const [consoleVm,  setConsoleVm]  = useState(null);
    const [azModalOpen, setAzModalOpen] = useState(false);

    // ── Helpers ───────────────────────────────────────────────────────────────
    const flash = (msg, type = "success") => {
        setToast({ msg, type });
        setTimeout(() => setToast(null), 3500);
    };

    // ── (sidebarMode derived below, after activeSlice) ──────────────────────

    // ── Helper: check if designer has unsaved work ──────────────────────────
    const hasUnsavedDesign = () => nodes.length > 0 && !activeId;

    const confirmOrLeave = (destination) => {
        if (hasUnsavedDesign()) {
            setModal({
                type: "confirmLeave",
                title: "¿Salir del diseñador?",
                msg: "Tienes una topología en progreso. Si sales sin guardar, perderás el progreso.",
                destination,
            });
        } else {
            doLeave(destination);
        }
    };

    const doLeave = (destination) => {
        setNodes([]); setEdges([]); setTargetAz("");
        setModal(null);
        // If navigating to a specific slice, extract its ID
        const sliceMatch = destination?.match(/^\/slice\/(.+)$/);
        if (sliceMatch && sliceMatch[1] !== "new") {
            setActiveId(sliceMatch[1]);
        } else {
            setActiveId(null);
        }
        navigate(destination ?? "/");
    };

    const handleConfirmLeaveDiscard = () => {
        const dest = modal?.destination ?? "/";
        doLeave(dest);
    };

    const handleConfirmLeaveSaveDraft = () => {
        setModal("draft"); // opens SaveDraftModal — after save it navigates to /
    };

    const setSidebarModeNav = useCallback((mode) => {
        if (mode === "browse") {
            confirmOrLeave("/");
        }
        else if (mode === "design") navigate("/slice/new");
        else if (mode === "images") confirmOrLeave("/images");
    }, [nodes, activeId, navigate]);

    // ── Navigation handlers ───────────────────────────────────────────────────
    const handleNewSlice = () => {
        setActiveId(null);
        setNodes([]);
        setEdges([]);
        setTargetAz("");
        setAzModalOpen(true);
        navigate("/slice/new");
    };

    const handleSliceClick = (id) => {
        if (hasUnsavedDesign()) {
            setModal({
                type: "confirmLeave",
                title: "¿Salir del diseñador?",
                msg: "Tienes una topología en progreso. Si sales sin guardar, perderás el progreso.",
                destination: `/slice/${id}`,
            });
            return;
        }
        setNodes([]); setEdges([]);
        setActiveId(id);
        navigate(`/slice/${id}`);
    };

    const handleBackFromSlice = () => {
        if (hasUnsavedDesign()) {
            confirmOrLeave("/");
            return;
        }
        setActiveId(null);
        setNodes([]); setEdges([]);
        navigate("/");
    };

    // ── Data fetching ─────────────────────────────────────────────────────────
    const fetchSlices = async () => {
        try {
            const res = await apiFetch("/slices");
            if (res.ok) {
                const fresh = await res.json();
                setSlices(prev => fresh.map(srv => {
                    if (srv.id === activeIdRef.current && srv.status === "DRAFT") {
                        return prev.find(s => s.id === srv.id) ?? srv;
                    }
                    return srv;
                }));
            }
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
            const res = await apiFetch("/slices/utils/images");
            if (res.ok) setFullImages(await res.json());
        } catch (e) { console.error("fetchFullImages:", e); }
    };

    useEffect(() => {
        if (!isAuthenticated) return;
        fetchSlices();
        fetchImageList();
        fetchFullImages();
        // Sync current user to local cache (needed for project membership)
        apiFetch("/users/sync", {
            method: "POST",
            body: JSON.stringify({
                username: user?.username,
                email:    user?.email,
                fullname: user?.name,
            }),
        }).catch(e => console.error("user sync:", e));
        const interval = setInterval(fetchSlices, 8000);
        return () => clearInterval(interval);
    }, [isAuthenticated]);

    // ── Derived ───────────────────────────────────────────────────────────────
    const activeSlice = slices.find(s => s.id === activeId) ?? null;

    // ── Derive sidebarMode from current route + active slice status ──────────
    const sidebarMode = useMemo(() => {
        if (location.pathname === "/slice/new") return "design";
        if (location.pathname === "/images") return "images";
        if (location.pathname.startsWith("/slice/") && activeSlice?.status === "DRAFT") return "design";
        return "browse";
    }, [location.pathname, activeSlice?.status]);

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
                        if (activeId === id) { setActiveId(null); navigate("/"); }
                    } else {
                        updateSlice(id, { status: "TERMINATED" });
                    }
                    setModal(null);
                    flash(data.message);
                } catch { flash("Error al eliminar", "error"); }
            },
        });
    };

    const deployFromDesigner = async (name, azId = 1, projectId = null, isDirect = false) => {
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
                body: JSON.stringify({
                    availability_zone_id: azId,
                    ttl_hours: 4,
                    motivo: "Despliegue directo desde Canvas",
                    project_id: projectId,
                }),
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
            navigate("/");
            flash(isDirect
                ? `"${name}" enviado a validación de recursos`
                : `Solicitud de "${name}" enviada — pendiente de aprobación`);
        } catch { flash("Error de conexión con el servidor", "error"); }
    };

    const bulkDeploy = async (namePrefix, azId, projectId) => {
        try {
            const defaultImg = imageList.filter(img => img.availability_zone_id == azId || img.availability_zone_id == null)[0]
                ?? imageList[0] ?? { id: 1, name: "Cirros" };
            const processedNodes = nodes.map(n => ({
                ...n,
                image_id: n.image_id || defaultImg.id,
                image:    n.image    || defaultImg.name,
            }));

            const res = await apiFetch("/slices/bulk-deploy", {
                method: "POST",
                body: JSON.stringify({
                    name_prefix: namePrefix,
                    slice_json: { nodes: processedNodes, edges },
                    project_id: projectId,
                    availability_zone_id: azId,
                    ttl_hours: 4,
                }),
            });

            const data = await res.json();
            if (!res.ok) {
                flash(data.detail || "Error en despliegue masivo", "error");
                return;
            }

            setNodes([]); setEdges([]);
            setModal(null);
            navigate("/");
            fetchSlices();
            flash(`Despliegue masivo: ${data.success} slices creados para "${data.project_name}"`);
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
            const sl = mkSlice(name, "DRAFT", [...nodes], [...edges]);
            sl.id = slice_id;
            setSlices(p => [sl, ...p]);
            setNodes([]); setEdges([]);
            setModal(null);
            navigate("/");
            flash(`"${name}" guardado como borrador`);
        } catch { flash("Error de conexión con el servidor", "error"); }
    };

    const deployDraft = (id) => {
        setModal({ type: "deployDraft", id });
    };

    const doDeployDraft = async (id, azId, projectId = null, isDirect = false) => {
        try {
            const res = await apiFetch(`/slices/${id}/deploy`, {
                method: "POST",
                body: JSON.stringify({
                    availability_zone_id: azId,
                    ttl_hours: 4,
                    motivo: "Despliegue desde la UI",
                    project_id: projectId,
                }),
            });
            if (!res.ok) throw new Error();
            updateSlice(id, { status: "PENDING_APPROVAL" });
            setModal(null);
            flash(isDirect
                ? "Solicitud de despliegue encolada"
                : "Solicitud enviada — pendiente de aprobación");
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

    // ── Guardar cambios de un borrador (PUT /{id}/draft) ──────────────────────
    const updateDraft = async () => {
        const sl = slices.find(s => s.id === activeId);
        if (!sl || sl.status !== "DRAFT") return;
        try {
            const res = await apiFetch(`/slices/${sl.id}/draft`, {
                method: "PUT",
                body: JSON.stringify({
                    name:       sl.name,
                    slice_json: { nodes: sl.nodes, edges: sl.edges },
                }),
            });
            if (res.ok) flash("Borrador actualizado correctamente");
            else {
                const err = await res.json().catch(() => ({}));
                flash(err.detail || "Error al guardar el borrador", "error");
            }
        } catch { flash("Error de conexión al guardar", "error"); }
    };

    // ── AUTH GATE ─────────────────────────────────────────────────────────────
    if (!isAuthenticated) {
        // If not on /login, redirect
        if (location.pathname !== "/login") {
            return <Navigate to="/login" replace />;
        }
        return (
            <Routes>
                <Route path="/login" element={<LoginPage onLogin={login} isDemoMode={isDemoMode} />} />
                <Route path="*" element={<Navigate to="/login" replace />} />
            </Routes>
        );
    }

    // If authenticated and on /login, redirect to /
    if (location.pathname === "/login") {
        return <Navigate to="/" replace />;
    }

    // ── RBAC guards ──────────────────────────────────────────────────────────
    const isAdmin      = user?.role === "admin" || user?.role === "superAdmin" || user?.role === "jefeProyecto";
    const isSuperAdmin = user?.role === "superAdmin";

    // ── Shared sidebar (for all routes except profile) ────────────────────────
    const sidebar = (
        <Sidebar
            sidebarMode={sidebarMode}
            setSidebarMode={setSidebarModeNav}
            slices={slices}
            activeId={activeId}
            setActiveId={handleSliceClick}
            onNewSlice={handleNewSlice}
            onDestroySlice={destroySlice}
            onDeployDraft={deployDraft}
            activeSlice={activeSlice}
            fullImages={fullImages}
            fetchFullImages={fetchFullImages}
            fetchImageList={fetchImageList}
            apiFetch={apiFetch}
            user={user}
            flash={flash}
            isSuperAdmin={isSuperAdmin}
            onInfraMonitor={() => navigate("/admin/infra")}
            onProjects={() => navigate("/projects")}
            onUsersManage={() => navigate("/admin/users")}
        />
    );

    // ── Shared canvas props ───────────────────────────────────────────────────
    const canvasProps = {
        user, token, logout,
        onProfile: () => navigate("/profile"),
        onBack: handleBackFromSlice,
        reTheme,
        slices,
        activeId, setActiveId: handleSliceClick,
        activeSlice,
        nodes, setNodes,
        edges, setEdges,
        targetAz, setTargetAz,
        setSliceNodes, setSliceEdges,
        imageList,
        destroySlice,
        deployDraft,
        deployFromDesigner,
        bulkDeploy,
        saveDraft,
        doDeployDraft,
        updateDraft,
        importarTopologia,
        exportarTopologia,
        modal, setModal,
        consoleVm, setConsoleVm,
        azModalOpen, setAzModalOpen,
        flash, apiFetch,
        // Leave-design confirmation callbacks
        onConfirmLeaveDiscard: handleConfirmLeaveDiscard,
        onConfirmLeaveSaveDraft: handleConfirmLeaveSaveDraft,
        isDesignMode: sidebarMode === "design",
    };

    return (
        <Routes>
            {/* Profile — full screen, no sidebar */}
            <Route path="/profile" element={
                <ProfileView
                    user={user} logout={logout} reTheme={reTheme}
                    themeRev={themeRev} onBack={() => navigate("/")}
                />
            } />

            {/* Infrastructure monitor — superAdmin only, full screen */}
            <Route path="/admin/infra" element={
                isSuperAdmin
                    ? <InfraMonitorView
                          user={user} logout={logout} reTheme={reTheme}
                          themeRev={themeRev} onBack={() => navigate("/")}
                          onProfile={() => navigate("/profile")}
                          apiFetch={apiFetch}
                      />
                    : <Navigate to="/" replace />
            } />

            {/* Projects management — admin/superAdmin/jefeProyecto, full screen */}
            <Route path="/projects" element={
                (isAdmin || user?.role === "jefeProyecto")
                    ? <ProjectsView
                          user={user} logout={logout} reTheme={reTheme}
                          themeRev={themeRev} onBack={() => navigate("/")}
                          onProfile={() => navigate("/profile")}
                          apiFetch={apiFetch} flash={flash}
                      />
                    : <Navigate to="/" replace />
            } />

            {/* Users management — admin/superAdmin, full screen */}
            <Route path="/admin/users" element={
                (user?.role === "admin" || user?.role === "superAdmin")
                    ? <UsersView
                          user={user} logout={logout} reTheme={reTheme}
                          themeRev={themeRev} onBack={() => navigate("/")}
                          onProfile={() => navigate("/profile")}
                          apiFetch={apiFetch} flash={flash}
                      />
                    : <Navigate to="/" replace />
            } />

            {/* Images — RBAC guarded */}
            <Route path="/images" element={
                isAdmin
                    ? <AppLayout sidebar={sidebar} toast={toast} themeRev={themeRev}>
                          <CanvasView {...canvasProps} />
                      </AppLayout>
                    : <Navigate to="/" replace />
            } />

            {/* New slice designer */}
            <Route path="/slice/new" element={
                <AppLayout sidebar={sidebar} toast={toast} themeRev={themeRev}>
                    <CanvasView {...canvasProps} />
                </AppLayout>
            } />

            {/* View/edit existing slice */}
            <Route path="/slice/:id" element={
                <AppLayout sidebar={sidebar} toast={toast} themeRev={themeRev}>
                    <CanvasView {...canvasProps} />
                </AppLayout>
            } />

            {/* Browse slices — home */}
            <Route path="/" element={
                <AppLayout sidebar={sidebar} toast={toast} themeRev={themeRev}>
                    <CanvasView {...canvasProps} />
                </AppLayout>
            } />

            {/* Catch-all */}
            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    );
}
