import { useState, useEffect, useRef } from "react";
import { T } from "../../theme/tokens";
import { User, Settings, LogOut } from "../ui/Icon";

const getInitials = (user) => {
    if (!user) return "?";
    const src = user.name || user.email || "";
    const parts = src.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return src.slice(0, 2).toUpperCase();
};

const avatarColor = (user) => {
    const COLORS = ["#2e7d32", "#1565c0", "#6a1b9a", "#e65100", "#c62828", "#00695c"];
    const src    = user?.email || user?.name || "?";
    const idx    = src.charCodeAt(0) % COLORS.length;
    return COLORS[idx];
};

// ── MenuItem with Lucide icon component ──────────────────────────────────────
const MenuItem = ({ IconComp, label, onClick, danger }) => (
    <button onClick={onClick} style={{
        width: "100%", display: "flex", alignItems: "center", gap: 10,
        padding: "9px 12px", borderRadius: 8, border: "none",
        background: "none", cursor: "pointer", fontFamily: "inherit",
        fontSize: 13, fontWeight: 600,
        color: danger ? T.red : T.text,
        transition: "background 0.12s", textAlign: "left",
    }}
        onMouseEnter={e => e.currentTarget.style.background = danger ? T.redLight : T.surfaceElevated}
        onMouseLeave={e => e.currentTarget.style.background = "none"}
    >
        <IconComp size={16} color={danger ? T.red : T.textMuted} />
        {label}
    </button>
);

export const UserAvatar = ({ user, onLogout, onProfile }) => {
    const [open, setOpen] = useState(false);
    const ref = useRef();

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener("mousedown", handler);
        return () => document.removeEventListener("mousedown", handler);
    }, []);

    const color    = avatarColor(user);
    const initials = getInitials(user);
    const name     = user?.name  || user?.email || "Usuario";
    const email    = user?.email || "";
    const role     = user?.role  || "Investigador";

    return (
        <div ref={ref} style={{ position: "relative" }}>

            {/* Avatar button */}
            <button onClick={() => setOpen(v => !v)} title={name} style={{
                width: 34, height: 34, borderRadius: "50%",
                background: color, color: "#fff",
                border: open ? `2px solid ${color}` : `2px solid transparent`,
                boxShadow: open ? `0 0 0 3px ${color}22` : "none",
                cursor: "pointer", fontSize: 12, fontWeight: 800,
                fontFamily: "inherit", display: "flex",
                alignItems: "center", justifyContent: "center",
                transition: "box-shadow 0.15s, border-color 0.15s", flexShrink: 0,
            }}>
                {initials}
            </button>

            {/* Dropdown */}
            {open && (
                <div style={{
                    position: "absolute", top: "calc(100% + 10px)", right: 0,
                    minWidth: 220, background: T.surface,
                    border: `1px solid ${T.border}`, borderRadius: 14,
                    boxShadow: "0 12px 36px rgba(0,0,0,0.15), 0 4px 12px rgba(0,0,0,0.08)",
                    zIndex: 5000, overflow: "hidden",
                    animation: "dropIn 0.18s ease",
                }}>
                    {/* User info header */}
                    <div style={{ padding: "14px 16px 12px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <div style={{
                                width: 40, height: 40, borderRadius: "50%",
                                background: color, color: "#fff",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                fontSize: 14, fontWeight: 800, flexShrink: 0,
                            }}>
                                {initials}
                            </div>
                            <div style={{ minWidth: 0 }}>
                                <div style={{ fontSize: 13, fontWeight: 700, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</div>
                                <div style={{ fontSize: 10, color: T.textMuted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{email}</div>
                                <div style={{ marginTop: 3, display: "inline-block", fontSize: 9, fontWeight: 700, color: T.accent, background: T.accentLight, padding: "1px 7px", borderRadius: 20, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                                    {role}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Menu items */}
                    <div style={{ padding: "6px 6px" }}>
                        <MenuItem IconComp={User}     label="Mi Perfil"      onClick={() => { setOpen(false); onProfile?.(); }} />
                        <MenuItem IconComp={Settings} label="Configuración"  onClick={() => setOpen(false)} />
                    </div>

                    <div style={{ height: 1, background: T.border, margin: "0 10px" }} />

                    <div style={{ padding: "6px 6px 8px" }}>
                        <MenuItem IconComp={LogOut} label="Cerrar Sesión" onClick={() => { setOpen(false); onLogout(); }} danger />
                    </div>
                </div>
            )}

            <style>{`
                @keyframes dropIn {
                    from { opacity: 0; transform: translateY(-8px) scale(0.97); }
                    to   { opacity: 1; transform: translateY(0)   scale(1); }
                }
            `}</style>
        </div>
    );
};
