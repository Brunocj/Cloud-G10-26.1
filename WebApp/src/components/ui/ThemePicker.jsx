import { useState, useRef, useEffect } from "react";
import { THEMES, T, applyTheme, getCurrentTheme } from "../../theme/tokens";
import { Palette } from "../ui/Icon";

/**
 * ThemePicker — floating popover with 5 theme swatches.
 * Calls onThemeChange() after switching so App re-renders with new T values.
 */
export const ThemePicker = ({ onThemeChange }) => {
    const [open,    setOpen]    = useState(false);
    // El id viene de tokens.js, que es quien realmente aplicó el tema al cargar.
    // Leerlo aquí de localStorage con un default propio hacía que en el primer
    // arranque (sin preferencia guardada) el picker marcara "Forest Light"
    // mientras la app se veía en Azure Light.
    const [current, setCurrent] = useState(getCurrentTheme);
    const ref = useRef();

    // Close on outside click
    useEffect(() => {
        if (!open) return;
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener("mousedown", handler);
        return () => document.removeEventListener("mousedown", handler);
    }, [open]);

    const select = (id) => {
        applyTheme(id);
        setCurrent(id);
        setOpen(false);
        onThemeChange?.();
    };

    const activeDot = THEMES.find(t => t.id === current)?.dot ?? T.accent;

    return (
        <div ref={ref} style={{ position: "relative" }}>
            {/* Trigger button */}
            <button
                onClick={() => setOpen(v => !v)}
                title="Cambiar tema" aria-label="Cambiar tema"
                style={{
                    background: "none", border: `1px solid ${T.border}`,
                    borderRadius: 8, cursor: "pointer",
                    padding: "5px 10px", display: "flex", alignItems: "center", gap: 6,
                    color: T.textMuted, transition: "all 0.15s",
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.color = T.accent; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = T.border;  e.currentTarget.style.color  = T.textMuted; }}
            >
                <Palette size={14} />
                {/* Color dot showing active theme */}
                <span style={{
                    width: 10, height: 10, borderRadius: "50%",
                    background: activeDot, display: "inline-block",
                    border: `1.5px solid ${T.border}`,
                }} />
            </button>

            {/* Popover */}
            {open && (
                <div style={{
                    position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 2000,
                    background: T.surface, border: `1px solid ${T.border}`,
                    borderRadius: 14, boxShadow: T.shadowMd,
                    padding: "14px 16px", minWidth: 200,
                    animation: "fadeDown 0.18s ease",
                }}>
                    <div style={{ fontSize: 10, fontWeight: 800, color: T.textMuted,
                        textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 10 }}>
                        Tema de la plataforma
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {THEMES.map(theme => {
                            const isActive = theme.id === current;
                            return (
                                <button key={theme.id} onClick={() => select(theme.id)}
                                    style={{
                                        display: "flex", alignItems: "center", gap: 10,
                                        padding: "8px 10px", borderRadius: 9,
                                        border: isActive ? `1.5px solid ${theme.dot}` : `1px solid ${T.border}`,
                                        background: isActive ? `${theme.dot}18` : T.surfaceElevated,
                                        cursor: "pointer", textAlign: "left",
                                        transition: "all 0.12s",
                                    }}>
                                    {/* Preview swatch */}
                                    <span style={{
                                        width: 24, height: 24, borderRadius: 7,
                                        background: theme.dot,
                                        boxShadow: isActive ? `0 0 0 3px ${theme.dot}44` : "none",
                                        flexShrink: 0,
                                        display: "flex", alignItems: "center", justifyContent: "center",
                                    }}>
                                        {/* Dark/light indicator dot */}
                                        <span style={{
                                            width: 8, height: 8, borderRadius: "50%",
                                            background: theme.dark ? "#000" : "#fff",
                                            opacity: 0.5,
                                        }} />
                                    </span>
                                    <div>
                                        <div style={{ fontSize: 12, fontWeight: 700,
                                            color: isActive ? theme.dot : T.text }}>
                                            {theme.label}
                                        </div>
                                        <div style={{ fontSize: 9, color: T.textMuted }}>
                                            {theme.dark ? "Modo oscuro" : "Modo claro"}
                                        </div>
                                    </div>
                                    {isActive && (
                                        <span style={{ marginLeft: "auto", width: 6, height: 6,
                                            borderRadius: "50%", background: theme.dot,
                                            boxShadow: `0 0 6px ${theme.dot}` }} />
                                    )}
                                </button>
                            );
                        })}
                    </div>
                    <style>{`
                        @keyframes fadeDown {
                            from { opacity:0; transform:translateY(-6px); }
                            to   { opacity:1; transform:translateY(0); }
                        }
                    `}</style>
                </div>
            )}
        </div>
    );
};
