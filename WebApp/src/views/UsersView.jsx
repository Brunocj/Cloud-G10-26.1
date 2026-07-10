import { useState, useEffect } from "react";
import { T, btnBase, getGlobalCss } from "../theme/tokens";
import { ThemePicker } from "../components/ui/ThemePicker";
import { UserAvatar }  from "../components/ui/UserAvatar";
import {
    ArrowLeft, Plus, User as UserIcon, X, Crown,
    Search, AlertTriangle, ShieldCheck,
} from "../components/ui/Icon";

const ROLE_LABELS = {
    usuario:      "Usuario",
    jefeProyecto: "Jefe de Proyecto",
    admin:        "Administrador",
    superAdmin:   "Super Administrador",
};

const roleBadgeColor = (role) => {
    switch (role) {
        case "superAdmin":   return { bg: "#7c3aed22", fg: "#7c3aed" };
        case "admin":        return { bg: "#2563eb22", fg: "#2563eb" };
        case "jefeProyecto": return { bg: T.accentLight, fg: T.accent };
        default:             return { bg: T.surfaceElevated, fg: T.textMuted };
    }
};

const displayName = (u) => {
    const parts = [u.fullname, u.lastname].filter(x => x && x.trim() && x.toLowerCase() !== "none");
    if (parts.length) return parts.join(" ");
    return u.username || (u.id || "").slice(0, 8);
};

// ─── CreateUserModal ──────────────────────────────────────────────────────────
const CreateUserModal = ({ onSave, onClose, currentUserRole }) => {
    const [form, setForm] = useState({
        username: "", email: "", password: "",
        fullname: "", lastname: "", role: "usuario",
    });
    const [saving, setSaving] = useState(false);

    const canCreateAdmins = currentUserRole === "superAdmin";
    const availableRoles = canCreateAdmins
        ? ["usuario", "jefeProyecto", "admin", "superAdmin"]
        : ["usuario", "jefeProyecto"];

    const submit = async () => {
        if (!form.username.trim() || !form.email.trim() || !form.password.trim()) return;
        setSaving(true);
        await onSave(form);
        setSaving(false);
    };

    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 460, border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: T.text }}>Nuevo Usuario</div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    {[
                        ["username",  "Usuario"],
                        ["email",     "Email"],
                        ["fullname",  "Nombres"],
                        ["lastname",  "Apellidos"],
                    ].map(([k, label]) => (
                        <div key={k}>
                            <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</label>
                            <input value={form[k]} onChange={e => set(k, e.target.value)}
                                style={{ width: "100%", padding: "7px 10px", fontSize: 12, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                        </div>
                    ))}
                </div>

                <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Contraseña Inicial</label>
                    <input type="password" value={form.password} onChange={e => set("password", e.target.value)}
                        style={{ width: "100%", padding: "7px 10px", fontSize: 12, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                </div>

                <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Rol Global</label>
                    <select value={form.role} onChange={e => set("role", e.target.value)}
                        style={{ width: "100%", padding: "7px 10px", fontSize: 12, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none" }}>
                        {availableRoles.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                </div>

                <div style={{ display: "flex", gap: 8, marginTop: 20, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        Cancelar
                    </button>
                    <button onClick={submit} disabled={saving}
                        style={btnBase({ padding: "8px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none" })}>
                        {saving ? "Creando..." : "Crear Usuario"}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── ChangeRoleModal (superAdmin only) ────────────────────────────────────────
const ChangeRoleModal = ({ user: target, onSave, onClose }) => {
    const [newRole, setNewRole] = useState(target.role || "usuario");
    const [saving, setSaving] = useState(false);

    const submit = async () => {
        setSaving(true);
        await onSave(target.id, newRole);
        setSaving(false);
    };

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 400, border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                    <div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: T.text }}>Cambiar Rol</div>
                        <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>{displayName(target)}</div>
                    </div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 6 }}>Rol actual: <b style={{ color: T.text }}>{ROLE_LABELS[target.role] || target.role || "—"}</b></div>

                <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>Nuevo Rol</label>
                <select value={newRole} onChange={e => setNewRole(e.target.value)}
                    style={{ width: "100%", padding: "8px 10px", fontSize: 13, marginTop: 4, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none" }}>
                    {["usuario", "jefeProyecto", "admin", "superAdmin"].map(r =>
                        <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                </select>

                <div style={{ fontSize: 11, color: T.textMuted, marginTop: 10, padding: "8px 10px", background: T.surfaceElevated, borderRadius: 6 }}>
                    El cambio se refleja en Keycloak inmediatamente. El usuario debe volver a iniciar sesión para que su token JWT tenga el nuevo rol.
                </div>

                <div style={{ display: "flex", gap: 8, marginTop: 20, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        Cancelar
                    </button>
                    <button onClick={submit} disabled={saving || newRole === target.role}
                        style={btnBase({ padding: "8px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: newRole === target.role ? 0.5 : 1 })}>
                        {saving ? "Guardando..." : "Aplicar Cambio"}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── CsvImportModal (REQ-AD-01: registro masivo) ──────────────────────────────
const CSV_TEMPLATE = "username,email,password,fullname,lastname,role\njperez,jperez@pucp.edu.pe,Temporal123,Juan,Perez,usuario\n";

const CsvImportModal = ({ onConfirm, onClose }) => {
    const [rows, setRows]   = useState([]);
    const [error, setError] = useState(null);
    const [busy, setBusy]   = useState(false);
    const [result, setResult] = useState(null);

    const parseFile = (file) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const lines = e.target.result.split(/\r?\n/).filter(l => l.trim());
                const headers = lines[0].split(",").map(h => h.trim().toLowerCase());
                const required = ["username", "email", "password"];
                if (!required.every(r => headers.includes(r)))
                    throw new Error(`El CSV debe incluir las columnas: ${required.join(", ")}`);
                const parsed = lines.slice(1).map(line => {
                    const vals = line.split(",").map(v => v.trim());
                    const obj = {};
                    headers.forEach((h, i) => obj[h] = vals[i] ?? "");
                    obj.role = obj.role || "usuario";
                    return obj;
                });
                if (parsed.length === 0) throw new Error("El CSV no tiene filas de datos.");
                setRows(parsed); setError(null); setResult(null);
            } catch (err) { setError(err.message); setRows([]); }
        };
        reader.readAsText(file);
    };

    const downloadTemplate = () => {
        const blob = new Blob([CSV_TEMPLATE], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement("a"), { href: url, download: "plantilla_usuarios.csv" });
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const submit = async () => {
        setBusy(true);
        const res = await onConfirm(rows);
        setResult(res);
        setBusy(false);
    };

    return (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
            <div style={{ background: T.surface, borderRadius: 14, padding: "24px 26px", width: 560, maxHeight: "85vh", overflowY: "auto", border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: T.text }}>⬆ Importar Usuarios (CSV)</div>
                    <button onClick={onClose} style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <div style={{ fontSize: 11.5, color: T.textMuted, marginBottom: 12, lineHeight: 1.5 }}>
                    Columnas: <code>username, email, password</code> (obligatorias) + <code>fullname, lastname, role</code>.
                    {" "}<button onClick={downloadTemplate} style={{ background: "none", border: "none", color: T.accent, cursor: "pointer", fontSize: 11.5, textDecoration: "underline", padding: 0, fontFamily: "inherit" }}>
                        Descargar plantilla CSV de ejemplo
                    </button>
                </div>

                <input type="file" accept=".csv,text/csv" onChange={e => e.target.files[0] && parseFile(e.target.files[0])}
                    style={{ fontSize: 12, marginBottom: 12, color: T.text }} />

                {error && <div style={{ fontSize: 12, color: T.red, background: T.redLight, padding: "8px 12px", borderRadius: 8, marginBottom: 12 }}>{error}</div>}

                {/* Vista previa */}
                {rows.length > 0 && !result && (
                    <>
                        <div style={{ fontSize: 11, fontWeight: 700, color: T.textMuted, marginBottom: 6 }}>Vista previa — {rows.length} cuenta(s):</div>
                        <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: "hidden", marginBottom: 14, maxHeight: 220, overflowY: "auto" }}>
                            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                                <thead><tr style={{ background: T.surfaceElevated }}>
                                    {["Usuario", "Email", "Nombre", "Rol"].map(h => <th key={h} style={{ padding: "6px 10px", textAlign: "left", color: T.textMuted, fontSize: 9, textTransform: "uppercase" }}>{h}</th>)}
                                </tr></thead>
                                <tbody>
                                    {rows.map((r, i) => (
                                        <tr key={i} style={{ borderTop: `1px solid ${T.border}` }}>
                                            <td style={{ padding: "5px 10px", fontWeight: 700 }}>{r.username}</td>
                                            <td style={{ padding: "5px 10px" }}>{r.email}</td>
                                            <td style={{ padding: "5px 10px" }}>{[r.fullname, r.lastname].filter(Boolean).join(" ")}</td>
                                            <td style={{ padding: "5px 10px", color: T.accent }}>{r.role}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}

                {/* Resultado */}
                {result && (
                    <div style={{ fontSize: 12, marginBottom: 14 }}>
                        <div style={{ fontWeight: 700, color: result.created === result.total ? "#16a34a" : T.yellow, marginBottom: 6 }}>
                            {result.created}/{result.total} cuentas creadas
                        </div>
                        {result.results.filter(r => !r.ok).map((r, i) => (
                            <div key={i} style={{ color: T.red, fontSize: 11 }}>❌ {r.username}: {r.error}</div>
                        ))}
                    </div>
                )}

                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <button onClick={onClose}
                        style={btnBase({ padding: "8px 14px", fontSize: 12, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none" })}>
                        {result ? "Cerrar" : "Cancelar"}
                    </button>
                    {!result && (
                        <button onClick={submit} disabled={busy || rows.length === 0}
                            style={btnBase({ padding: "8px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: (busy || rows.length === 0) ? 0.5 : 1 })}>
                            {busy ? "Creando…" : `Confirmar creación (${rows.length})`}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

// ─── UsersView ────────────────────────────────────────────────────────────────
export const UsersView = ({ user, logout, reTheme, themeRev, onBack, onProfile, apiFetch, flash }) => {
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [creating, setCreating] = useState(false);
    const [importing, setImporting] = useState(false);
    const [changingRole, setChangingRole] = useState(null);
    const [query, setQuery] = useState("");

    const isSuperAdmin = user?.role === "superAdmin";

    const load = async () => {
        try {
            const res = await apiFetch("/users/");
            if (res.ok) { setUsers(await res.json()); setError(null); }
            else setError(`Error ${res.status} al cargar usuarios`);
        } catch (e) {
            setError("No se pudo conectar al servidor");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const createUser = async (payload) => {
        const res = await apiFetch("/users/", { method: "POST", body: JSON.stringify(payload) });
        if (res.ok) {
            setCreating(false); load();
            flash(`Usuario "${payload.username}" creado en Keycloak`);
        } else {
            const err = await res.json().catch(() => ({}));
            flash(err.detail || "Error al crear usuario", "error");
        }
    };

    const changeRole = async (userId, role) => {
        const res = await apiFetch(`/users/${userId}/role`, {
            method: "PATCH", body: JSON.stringify({ role }),
        });
        if (res.ok) {
            setChangingRole(null); load();
            flash("Rol actualizado en Keycloak");
        } else {
            const err = await res.json().catch(() => ({}));
            flash(err.detail || "Error al cambiar rol", "error");
        }
    };

    // Aprobación / rechazo de cuentas auto-registradas (REQ-AD-01)
    const changeState = async (u, action) => {
        let reason = null;
        if (action === "reject") {
            reason = window.prompt(`Motivo del rechazo de la cuenta de "${u.username}" (se le enviará por correo):`);
            if (reason === null) return;   // canceló
        }
        const res = await apiFetch(`/users/${u.id}/state`, {
            method: "PATCH", body: JSON.stringify({ action, reason }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            flash(action === "approve"
                ? `Cuenta de "${u.username}" aprobada — correo de validación enviado`
                : `Cuenta de "${u.username}" rechazada y eliminada`);
            load();
        } else flash(data.detail || "Error al cambiar el estado", "error");
    };

    const pendingUsers = users.filter(u => u.state === "Pendiente");

    const filtered = users.filter(u => {
        if (!query.trim()) return true;
        const q = query.toLowerCase();
        return (u.username || "").toLowerCase().includes(q)
            || (u.email    || "").toLowerCase().includes(q)
            || (u.fullname || "").toLowerCase().includes(q);
    });

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text }}>
            {/* Topbar */}
            <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <ShieldCheck size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800, color: T.text }}>Gestión de Usuarios</span>
                <div style={{ flex: 1 }} />
                <button onClick={() => setImporting(true)}
                    style={btnBase({ padding: "6px 14px", fontSize: 12, fontWeight: 700, background: T.surfaceElevated, color: T.accent, border: `1px solid ${T.accent}44`, boxShadow: "none", display: "flex", alignItems: "center", gap: 5 })}>
                    ⬆ Importar CSV
                </button>
                <button onClick={() => setCreating(true)}
                    style={btnBase({ padding: "6px 14px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", display: "flex", alignItems: "center", gap: 5 })}>
                    <Plus size={13} /> Nuevo Usuario
                </button>
                <ThemePicker onThemeChange={reTheme} />
                <UserAvatar user={user} onLogout={logout} onProfile={onProfile} />
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: "auto", padding: "22px 28px" }}>
                {/* Search */}
                <div style={{ position: "relative", marginBottom: 16, maxWidth: 420 }}>
                    <Search size={13} style={{ position: "absolute", left: 12, top: 10, color: T.textMuted }} />
                    <input value={query} onChange={e => setQuery(e.target.value)}
                        placeholder="Filtrar por nombre, email o usuario"
                        style={{ width: "100%", padding: "8px 12px 8px 32px", fontSize: 12, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                </div>

                {loading ? (
                    <div style={{ textAlign: "center", padding: 60, color: T.textMuted }}>Cargando usuarios...</div>
                ) : error ? (
                    <div style={{ textAlign: "center", padding: 60 }}>
                        <AlertTriangle size={32} color={T.red} />
                        <div style={{ fontSize: 14, color: T.red, marginTop: 12 }}>{error}</div>
                    </div>
                ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {/* Bandeja de aprobaciones (REQ-AD-01) */}
                        {pendingUsers.length > 0 && (
                            <div style={{
                                background: T.yellowLight, border: `1px solid ${T.yellow}55`,
                                borderRadius: 10, padding: "10px 14px", marginBottom: 8,
                                display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 700, color: T.yellow,
                            }}>
                                <AlertTriangle size={14} />
                                {pendingUsers.length} cuenta{pendingUsers.length > 1 ? "s" : ""} pendiente{pendingUsers.length > 1 ? "s" : ""} de aprobación — revísalas abajo (marcadas en amarillo).
                            </div>
                        )}
                        <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
                            {filtered.length} usuario{filtered.length !== 1 ? "s" : ""}
                        </div>
                        {[...filtered].sort((a, b) => (a.state === "Pendiente" ? -1 : 0) - (b.state === "Pendiente" ? -1 : 0)).map(u => {
                            const badge = roleBadgeColor(u.role);
                            const isPending = u.state === "Pendiente";
                            return (
                                <div key={u.id} style={{
                                    background: isPending ? T.yellowLight : T.surface, borderRadius: 10, padding: "12px 16px",
                                    border: `1px solid ${isPending ? T.yellow + "66" : T.border}`, display: "flex", alignItems: "center", gap: 12,
                                }}>
                                    <div style={{ width: 34, height: 34, borderRadius: "50%", background: T.accentLight, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                                        {u.role === "jefeProyecto" || u.role === "admin" || u.role === "superAdmin"
                                            ? <Crown size={14} color={T.accent} />
                                            : <UserIcon size={14} color={T.accent} />
                                        }
                                    </div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: 13, fontWeight: 700, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            {displayName(u)}
                                        </div>
                                        <div style={{ fontSize: 11, color: T.textMuted }}>
                                            {u.email} · <span style={{ color: T.textFaint }}>@{u.username}</span>
                                        </div>
                                    </div>
                                    <span style={{ padding: "3px 10px", borderRadius: 6, fontSize: 10, fontWeight: 700, background: badge.bg, color: badge.fg }}>
                                        {ROLE_LABELS[u.role] || u.role || "sin rol"}
                                    </span>
                                    {isPending && (
                                        <>
                                            <span style={{ padding: "3px 10px", borderRadius: 6, fontSize: 10, fontWeight: 800, background: T.yellow + "33", color: T.yellow }}>
                                                PENDIENTE
                                            </span>
                                            <button onClick={() => changeState(u, "approve")}
                                                style={btnBase({ padding: "6px 12px", fontSize: 11, fontWeight: 700, background: T.accent, color: "#fff", border: "none" })}>
                                                ✅ Aprobar
                                            </button>
                                            <button onClick={() => changeState(u, "reject")}
                                                style={btnBase({ padding: "6px 12px", fontSize: 11, fontWeight: 700, background: T.redLight, color: T.red, border: `1px solid ${T.red}44`, boxShadow: "none" })}>
                                                ❌ Rechazar
                                            </button>
                                        </>
                                    )}
                                    {isSuperAdmin && u.id !== user.id && (
                                        <button onClick={() => setChangingRole(u)}
                                            style={btnBase({ padding: "6px 12px", fontSize: 11, background: T.surfaceElevated, color: T.accent, border: `1px solid ${T.accent}44`, boxShadow: "none" })}>
                                            Cambiar Rol
                                        </button>
                                    )}
                                </div>
                            );
                        })}
                        {filtered.length === 0 && (
                            <div style={{ textAlign: "center", padding: 40, color: T.textMuted, fontSize: 12 }}>
                                Sin resultados
                            </div>
                        )}
                    </div>
                )}
            </div>

            {creating && (
                <CreateUserModal onSave={createUser} onClose={() => setCreating(false)}
                    currentUserRole={user?.role} />
            )}

            {changingRole && (
                <ChangeRoleModal user={changingRole} onSave={changeRole}
                    onClose={() => setChangingRole(null)} />
            )}

            {importing && (
                <CsvImportModal
                    onConfirm={async (rows) => {
                        const res = await apiFetch("/users/bulk", { method: "POST", body: JSON.stringify({ users: rows }) });
                        const data = await res.json().catch(() => ({ total: rows.length, created: 0, results: [] }));
                        if (res.ok) load();
                        return data;
                    }}
                    onClose={() => setImporting(false)}
                />
            )}

            <style key={themeRev}>{getGlobalCss()}</style>
        </div>
    );
};
