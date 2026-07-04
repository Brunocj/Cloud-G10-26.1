import { useState, useEffect } from "react";
import { T, btnBase, getGlobalCss } from "../theme/tokens";
import { ThemePicker } from "../components/ui/ThemePicker";
import { UserAvatar }  from "../components/ui/UserAvatar";
import {
    ArrowLeft, Plus, Users, Trash2, Edit3, X,
    Crown, User as UserIcon, Search, AlertTriangle, Save, CheckCircle,
} from "../components/ui/Icon";

// ─── Display helper for fullnames ─────────────────────────────────────────────
const displayName = (m) => {
    if (m.fullname && m.fullname.trim() && m.fullname.toLowerCase() !== "none none")
        return m.fullname;
    return m.username || (m.user_id || "").slice(0, 8);
};

const canBeJefe = (globalRole) =>
    globalRole === "jefeProyecto" || globalRole === "admin" || globalRole === "superAdmin";

// ─── ProjectCard ──────────────────────────────────────────────────────────────
const ProjectCard = ({ p, isAdmin, onOpen, onEdit, onDelete }) => (
    <div style={{
        background: T.surface, borderRadius: 12, padding: "16px 18px",
        border: `1.5px solid ${T.border}`, boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
        display: "flex", flexDirection: "column", gap: 10,
    }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 3 }}>{p.name}</div>
                {p.description && (
                    <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 6 }}>{p.description}</div>
                )}
            </div>
            {isAdmin && (
                <div style={{ display: "flex", gap: 4 }}>
                    <button onClick={() => onEdit(p)} title="Editar"
                        style={btnBase({ padding: "4px 6px", fontSize: 10, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        <Edit3 size={11} />
                    </button>
                    <button onClick={() => onDelete(p)} title="Eliminar"
                        style={btnBase({ padding: "4px 6px", fontSize: 10, background: T.redLight, color: T.red, border: `1px solid ${T.red}33`, boxShadow: "none" })}>
                        <Trash2 size={11} />
                    </button>
                </div>
            )}
        </div>
        <div style={{ display: "flex", gap: 12, fontSize: 11, color: T.textMuted }}>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <Users size={11} /> {p.member_count ?? 0} miembros
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <Crown size={11} color={T.accent} /> {p.leader_count ?? 0} jefe{p.leader_count !== 1 ? "s" : ""}
            </span>
        </div>
        <button onClick={() => onOpen(p)}
            style={btnBase({
                width: "100%", padding: "7px", fontSize: 11, marginTop: 4,
                background: T.accentLight, color: T.accent, border: `1px solid ${T.accent}44`,
                boxShadow: "none",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
            })}>
            <Users size={12} /> Gestionar Miembros
        </button>
    </div>
);

// ─── ProjectEditModal ─────────────────────────────────────────────────────────
const ProjectEditModal = ({ project, onSave, onClose }) => {
    const [name, setName] = useState(project?.name ?? "");
    const [description, setDescription] = useState(project?.description ?? "");
    const isNew = !project;

    const submit = () => {
        if (!name.trim()) return;
        onSave({ name: name.trim(), description: description.trim() || null });
    };

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 420, border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: T.text }}>
                        {isNew ? "Nuevo Proyecto" : "Editar Proyecto"}
                    </div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    <div>
                        <label style={{ fontSize: 11, fontWeight: 600, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Nombre</label>
                        <input value={name} onChange={e => setName(e.target.value)} autoFocus
                            style={{ width: "100%", padding: "8px 10px", fontSize: 13, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                    </div>
                    <div>
                        <label style={{ fontSize: 11, fontWeight: 600, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Descripción</label>
                        <input value={description} onChange={e => setDescription(e.target.value)}
                            style={{ width: "100%", padding: "8px 10px", fontSize: 13, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                    </div>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 20, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        Cancelar
                    </button>
                    <button onClick={submit}
                        style={btnBase({ padding: "8px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none" })}>
                        {isNew ? "Crear" : "Guardar"}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── MembersPanel — con staging + guardar cambios ─────────────────────────────
const MembersPanel = ({ project, apiFetch, currentUser, onClose, flash }) => {
    const [members, setMembers] = useState([]);         // committed state
    const [loading, setLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState([]);
    const [addingRole, setAddingRole] = useState("usuario");

    // Staging: pending additions and removals
    const [pendingAdds, setPendingAdds] = useState([]);  // [{user, project_role_name}]
    const [pendingRemoves, setPendingRemoves] = useState(new Set());  // user_ids
    const [saving, setSaving] = useState(false);

    const isAdminUser = currentUser?.role === "admin" || currentUser?.role === "superAdmin";
    const isJefe      = currentUser?.role === "jefeProyecto";

    const load = async () => {
        try {
            const res = await apiFetch(`/projects/${project.id}/members`);
            if (res.ok) setMembers(await res.json());
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, [project.id]);

    // Search
    useEffect(() => {
        if (!searchQuery.trim()) { setSearchResults([]); return; }
        const t = setTimeout(async () => {
            const res = await apiFetch(`/users/search?q=${encodeURIComponent(searchQuery)}&limit=10`);
            if (res.ok) {
                const data = await res.json();
                const memberIds = new Set(members.map(m => m.user_id));
                const pendingIds = new Set(pendingAdds.map(p => p.user.id));
                setSearchResults(data.filter(u => !memberIds.has(u.id) && !pendingIds.has(u.id)));
            }
        }, 300);
        return () => clearTimeout(t);
    }, [searchQuery, members, pendingAdds]);

    const stageAdd = (u) => {
        // Validation: if role is jefeProyecto, user must have jefeProyecto global role or above
        if (addingRole === "jefeProyecto" && !canBeJefe(u.role)) {
            flash("Este usuario no tiene el rol global 'jefeProyecto'. Un superAdmin debe promocionarlo primero.", "error");
            return;
        }
        setPendingAdds(p => [...p, { user: u, project_role_name: addingRole }]);
        setSearchQuery("");
        setSearchResults([]);
    };

    const cancelAdd = (userId) => {
        setPendingAdds(p => p.filter(x => x.user.id !== userId));
    };

    const stageRemove = (m) => {
        setPendingRemoves(prev => {
            const next = new Set(prev);
            if (next.has(m.user_id)) next.delete(m.user_id);
            else                     next.add(m.user_id);
            return next;
        });
    };

    // Can current user remove this member?
    const canRemove = (m) => {
        if (isAdminUser) return true;
        if (!isJefe)     return false;
        // jefe rules: not self, not other jefes
        if (m.user_id === currentUser.id)  return false;
        if (m.project_role === "jefeProyecto") return false;
        return true;
    };

    const save = async () => {
        setSaving(true);
        let errors = 0;

        // Removes
        for (const userId of pendingRemoves) {
            const res = await apiFetch(`/projects/${project.id}/members/${userId}`, { method: "DELETE" });
            if (!res.ok) errors++;
        }

        // Adds
        for (const { user, project_role_name } of pendingAdds) {
            const res = await apiFetch(`/projects/${project.id}/members`, {
                method: "POST",
                body: JSON.stringify({ user_id: user.id, project_role_name }),
            });
            if (!res.ok) errors++;
        }

        setPendingAdds([]);
        setPendingRemoves(new Set());
        await load();
        setSaving(false);
        if (errors === 0) flash("Cambios guardados correctamente");
        else              flash(`Guardado con ${errors} error(es)`, "error");
    };

    const hasChanges = pendingAdds.length > 0 || pendingRemoves.size > 0;
    const jefeSelectorDisabled = false; // logic is per-search-result

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "22px 24px", width: 560, maxHeight: "85vh", border: `1px solid ${T.border}`, display: "flex", flexDirection: "column" }}>
                {/* Header */}
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: T.text }}>{project.name}</div>
                        <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>Miembros del proyecto</div>
                    </div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                {/* Add member section */}
                <div style={{ padding: "12px 14px", background: T.accentLight, borderRadius: 10, border: `1px solid ${T.accent}22`, marginBottom: 14 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: T.accent, textTransform: "uppercase", marginBottom: 8, letterSpacing: "0.04em" }}>Agregar Miembro</div>
                    <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                        <div style={{ flex: 1, position: "relative" }}>
                            <Search size={12} style={{ position: "absolute", left: 10, top: 10, color: T.textMuted }} />
                            <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                                placeholder="Buscar por nombre, email o usuario"
                                style={{ width: "100%", padding: "8px 10px 8px 28px", fontSize: 12, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                        </div>
                        <select value={addingRole} onChange={e => setAddingRole(e.target.value)}
                            style={{ padding: "8px 10px", fontSize: 11, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none" }}>
                            <option value="usuario">Usuario</option>
                            <option value="jefeProyecto">Jefe</option>
                        </select>
                    </div>
                    {searchResults.length > 0 && (
                        <div style={{ maxHeight: 160, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
                            {searchResults.map(u => {
                                const eligible = addingRole !== "jefeProyecto" || canBeJefe(u.role);
                                return (
                                    <div key={u.id}
                                        onClick={() => eligible && stageAdd(u)}
                                        title={eligible ? "" : "Este usuario no tiene rol global jefeProyecto. Debe ser promocionado por un superAdmin."}
                                        style={{
                                            padding: "6px 10px", background: T.surface, borderRadius: 6,
                                            cursor: eligible ? "pointer" : "not-allowed",
                                            opacity: eligible ? 1 : 0.5,
                                            display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11,
                                        }}
                                        onMouseEnter={e => { if (eligible) e.currentTarget.style.background = T.surfaceElevated; }}
                                        onMouseLeave={e => e.currentTarget.style.background = T.surface}>
                                        <div>
                                            <div style={{ fontWeight: 600, color: T.text }}>
                                                {u.fullname && u.fullname.toLowerCase() !== "none none" ? u.fullname : u.username}
                                            </div>
                                            <div style={{ color: T.textMuted, fontSize: 10 }}>
                                                {u.email} · rol global: <b>{u.role || "usuario"}</b>
                                            </div>
                                        </div>
                                        {eligible
                                            ? <Plus size={12} color={T.accent} />
                                            : <AlertTriangle size={11} color={T.textFaint} />
                                        }
                                    </div>
                                );
                            })}
                        </div>
                    )}
                    {searchQuery.trim() && searchResults.length === 0 && (
                        <div style={{ fontSize: 11, color: T.textMuted, textAlign: "center", padding: 8 }}>
                            Sin resultados. El usuario debe haber iniciado sesión al menos una vez.
                        </div>
                    )}
                </div>

                {/* Members list */}
                <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6, minHeight: 100 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
                        {loading ? "Cargando..." : `${members.length + pendingAdds.length - pendingRemoves.size} miembros`}
                    </div>

                    {/* Committed members */}
                    {members.map(m => {
                        const isRemoved = pendingRemoves.has(m.user_id);
                        const removable = canRemove(m);
                        return (
                            <div key={m.user_id} style={{
                                display: "flex", justifyContent: "space-between", alignItems: "center",
                                padding: "8px 12px",
                                background: isRemoved ? T.redLight : T.surfaceElevated,
                                borderRadius: 8,
                                opacity: isRemoved ? 0.6 : 1,
                                textDecoration: isRemoved ? "line-through" : "none",
                            }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
                                    {m.project_role === "jefeProyecto"
                                        ? <Crown size={13} color={T.accent} />
                                        : <UserIcon size={13} color={T.textMuted} />
                                    }
                                    <div style={{ minWidth: 0 }}>
                                        <div style={{ fontSize: 12, fontWeight: 600, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            {displayName(m)}
                                        </div>
                                        <div style={{ fontSize: 10, color: T.textMuted }}>
                                            {m.email} · <span style={{ color: m.project_role === "jefeProyecto" ? T.accent : T.textMuted }}>{m.project_role}</span>
                                        </div>
                                    </div>
                                </div>
                                {removable && (
                                    <button onClick={() => stageRemove(m)} title={isRemoved ? "Cancelar eliminación" : "Marcar para eliminar"}
                                        style={btnBase({ padding: "4px 8px", fontSize: 10, background: isRemoved ? T.surfaceElevated : T.redLight, color: isRemoved ? T.textMuted : T.red, border: `1px solid ${isRemoved ? T.border : T.red + "33"}`, boxShadow: "none" })}>
                                        {isRemoved ? <X size={11} /> : <Trash2 size={11} />}
                                    </button>
                                )}
                            </div>
                        );
                    })}

                    {/* Pending adds (staged) */}
                    {pendingAdds.map(({ user: u, project_role_name }) => (
                        <div key={"pending-" + u.id} style={{
                            display: "flex", justifyContent: "space-between", alignItems: "center",
                            padding: "8px 12px",
                            background: T.accentLight,
                            border: `1px dashed ${T.accent}66`,
                            borderRadius: 8,
                        }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
                                {project_role_name === "jefeProyecto"
                                    ? <Crown size={13} color={T.accent} />
                                    : <UserIcon size={13} color={T.accent} />
                                }
                                <div style={{ minWidth: 0 }}>
                                    <div style={{ fontSize: 12, fontWeight: 600, color: T.text }}>
                                        {u.fullname && u.fullname.toLowerCase() !== "none none" ? u.fullname : u.username}
                                        <span style={{ fontSize: 9, color: T.accent, marginLeft: 6, fontWeight: 700, textTransform: "uppercase" }}>Nuevo</span>
                                    </div>
                                    <div style={{ fontSize: 10, color: T.textMuted }}>
                                        {u.email} · <span style={{ color: T.accent }}>{project_role_name}</span>
                                    </div>
                                </div>
                            </div>
                            <button onClick={() => cancelAdd(u.id)} title="Cancelar"
                                style={btnBase({ padding: "4px 8px", fontSize: 10, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                                <X size={11} />
                            </button>
                        </div>
                    ))}
                </div>

                {/* Save bar */}
                <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 14, marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <div style={{ fontSize: 11, color: T.textMuted }}>
                        {hasChanges
                            ? `${pendingAdds.length} agregar · ${pendingRemoves.size} eliminar`
                            : "Sin cambios pendientes"}
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                        <button onClick={onClose}
                            style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                            Cerrar
                        </button>
                        <button onClick={save} disabled={!hasChanges || saving}
                            style={btnBase({
                                padding: "8px 16px", fontSize: 12, fontWeight: 700,
                                background: hasChanges && !saving ? T.accent : T.surfaceElevated,
                                color: hasChanges && !saving ? "#fff" : T.textFaint,
                                border: "none",
                                cursor: hasChanges && !saving ? "pointer" : "not-allowed",
                                display: "flex", alignItems: "center", gap: 6,
                            })}>
                            {saving ? "Guardando..." : <><Save size={13} /> Guardar Cambios</>}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

// ─── ProjectsView ─────────────────────────────────────────────────────────────
export const ProjectsView = ({ user, logout, reTheme, themeRev, onBack, onProfile, apiFetch, flash }) => {
    const [projects, setProjects] = useState([]);
    const [loading,  setLoading]  = useState(true);
    const [error,    setError]    = useState(null);
    const [editing,  setEditing]  = useState(null);
    const [openedProject, setOpenedProject] = useState(null);
    const [confirming, setConfirming] = useState(null);

    const isAdmin = user?.role === "admin" || user?.role === "superAdmin";

    const load = async () => {
        try {
            const res = await apiFetch("/projects/");
            if (res.ok) { setProjects(await res.json()); setError(null); }
            else setError(`Error ${res.status} al cargar proyectos`);
        } catch (e) {
            setError("No se pudo conectar al servidor");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const saveProject = async (payload) => {
        const isNew = !editing?.id;
        const url    = isNew ? "/projects/" : `/projects/${editing.id}`;
        const method = isNew ? "POST" : "PUT";
        const res = await apiFetch(url, { method, body: JSON.stringify(payload) });
        if (res.ok) {
            setEditing(null); load();
            flash(isNew ? "Proyecto creado" : "Proyecto actualizado");
        } else {
            const err = await res.json().catch(() => ({}));
            flash(err.detail || "Error al guardar", "error");
        }
    };

    const deleteProject = async (p) => {
        const res = await apiFetch(`/projects/${p.id}`, { method: "DELETE" });
        if (res.ok) { load(); flash(`Proyecto "${p.name}" eliminado`); }
        else flash("Error al eliminar", "error");
        setConfirming(null);
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text }}>
            {/* Topbar */}
            <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "5px 12px", fontSize: 11, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <div style={{ width: 1, height: 22, background: T.border }} />
                <Users size={16} color={T.accent} />
                <span style={{ fontSize: 14, fontWeight: 800, color: T.text }}>Gestión de Proyectos</span>
                <div style={{ flex: 1 }} />
                {isAdmin && (
                    <button onClick={() => setEditing({})}
                        style={btnBase({ padding: "6px 14px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", display: "flex", alignItems: "center", gap: 5 })}>
                        <Plus size={13} /> Nuevo Proyecto
                    </button>
                )}
                <ThemePicker onThemeChange={reTheme} />
                <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: "auto", padding: "22px 28px" }}>
                {loading ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted }}>Cargando proyectos...</div>
                ) : error ? (
                    <div style={{ textAlign: "center", padding: 60 }}>
                        <AlertTriangle size={32} color={T.red} />
                        <div style={{ fontSize: 14, color: T.red, marginTop: 12 }}>{error}</div>
                    </div>
                ) : projects.length === 0 ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted }}>
                        <Users size={40} color={T.textFaint} />
                        <div style={{ marginTop: 12, fontSize: 14, fontWeight: 600 }}>Aún no hay proyectos</div>
                        {isAdmin && <div style={{ marginTop: 6, fontSize: 12, color: T.textFaint }}>Crea el primero con el botón superior</div>}
                    </div>
                ) : (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 14 }}>
                        {projects.map(p => (
                            <ProjectCard key={p.id} p={p} isAdmin={isAdmin}
                                onOpen={setOpenedProject}
                                onEdit={setEditing}
                                onDelete={setConfirming} />
                        ))}
                    </div>
                )}
            </div>

            {editing && (
                <ProjectEditModal project={editing.id ? editing : null}
                    onSave={saveProject}
                    onClose={() => setEditing(null)} />
            )}

            {openedProject && (
                <MembersPanel project={openedProject} apiFetch={apiFetch}
                    currentUser={user}
                    onClose={() => setOpenedProject(null)}
                    flash={flash} />
            )}

            {confirming && (
                <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
                    <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 400, border: `1px solid ${T.border}` }}>
                        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8, color: T.text }}>Eliminar Proyecto</div>
                        <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 20 }}>
                            ¿Seguro que deseas eliminar "{confirming.name}"? Todos los miembros serán desvinculados.
                        </div>
                        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                            <button onClick={() => setConfirming(null)}
                                style={btnBase({ padding: "7px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                                Cancelar
                            </button>
                            <button onClick={() => deleteProject(confirming)}
                                style={btnBase({ padding: "7px 14px", fontSize: 12, fontWeight: 700, background: T.red, color: "#fff", border: "none" })}>
                                Eliminar
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <style key={themeRev}>{getGlobalCss()}</style>
        </div>
    );
};
