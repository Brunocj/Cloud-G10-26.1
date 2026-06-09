import { useState } from "react";
import { T, inp } from "../../theme/tokens";
import { Toast } from "../ui/Toast";
import { RegisterPage } from "./RegisterPage";
import { Mail, Lock, Eye, EyeOff, AlertTriangle, Cloud, Loader, FlaskConical, ArrowRight } from "../ui/Icon";

// ─── Field ────────────────────────────────────────────────────────────────────
const Field = ({ id, label, type = "text", value, onChange, placeholder, disabled, IconComp, rightSlot }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <label htmlFor={id} style={{
            fontSize: 11, fontWeight: 700, color: T.textMuted,
            textTransform: "uppercase", letterSpacing: "0.07em",
        }}>
            {label}
        </label>
        <div style={{ position: "relative" }}>
            {IconComp && (
                <span style={{
                    position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)",
                    opacity: 0.45, pointerEvents: "none", display: "flex",
                }}>
                    <IconComp size={15} />
                </span>
            )}
            <input
                id={id} type={type} value={value} placeholder={placeholder}
                disabled={disabled} onChange={onChange}
                autoComplete={type === "password" ? "current-password" : "email"}
                style={{
                    ...inp,
                    paddingLeft: IconComp ? 36 : 12,
                    paddingRight: rightSlot ? 44 : 12,
                    fontSize: 14, height: 44,
                    border: `1.5px solid ${T.border}`,
                    borderRadius: 10,
                    transition: "border-color 0.2s, box-shadow 0.2s",
                }}
                onFocus={e => { e.target.style.borderColor = T.accent; e.target.style.boxShadow = `0 0 0 3px ${T.accent}18`; }}
                onBlur={e  => { e.target.style.borderColor = T.border;  e.target.style.boxShadow = "none"; }}
            />
            {rightSlot && (
                <div style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)" }}>
                    {rightSlot}
                </div>
            )}
        </div>
    </div>
);

// ─── LoginPage ────────────────────────────────────────────────────────────────
export const LoginPage = ({ onLogin, isDemoMode = false }) => {
    const [view,     setView]     = useState("login"); // "login" | "register"
    const [email,    setEmail]    = useState("");
    const [password, setPassword] = useState("");
    const [showPass, setShowPass] = useState(false);
    const [loading,  setLoading]  = useState(false);
    const [error,    setError]    = useState(null);
    const [toast,    setToast]    = useState(null);

    const flash = (msg, type = "warning") => {
        setToast({ msg, type });
        setTimeout(() => setToast(null), 5000);
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!email.trim() || !password) return;
        setError(null);
        setLoading(true);
        const result = await onLogin({ email: email.trim(), password });
        setLoading(false);
        if (result.success) return;
        switch (result.reason) {
            case "pending":
                flash("Tu cuenta aún está pendiente de aprobación por el administrador.", "warning");
                break;
            case "network":
                flash("No se pudo conectar con el servidor. Intenta de nuevo.", "error");
                break;
            default:
                setError("Credenciales inválidas. Verifica tu correo y contraseña.");
        }
    };

    // ── Background wrapper shared by both views ───────────────────────────────
    return (
        <div style={{
            minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
            background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif",
            position: "relative", overflow: "hidden",
        }}>
            {/* Dot-grid background */}
            <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
                <defs>
                    <pattern id="login-dots" width="28" height="28" patternUnits="userSpaceOnUse">
                        <circle cx="1" cy="1" r="1" fill={T.border} />
                    </pattern>
                </defs>
                <rect width="100%" height="100%" fill="url(#login-dots)" />
            </svg>

            {/* Decorative blobs */}
            <div style={{ position: "absolute", top: -120, left: -120, width: 420, height: 420, borderRadius: "50%", background: `radial-gradient(circle, ${T.accentLight} 0%, transparent 70%)`, opacity: 0.7, pointerEvents: "none" }} />
            <div style={{ position: "absolute", bottom: -100, right: -80, width: 360, height: 360, borderRadius: "50%", background: `radial-gradient(circle, ${T.accentLight} 0%, transparent 70%)`, opacity: 0.5, pointerEvents: "none" }} />

            {/* Card */}
            <div style={{
                position: "relative", zIndex: 1,
                width: "100%",
                maxWidth: view === "register" ? 540 : 420,
                margin: "20px",
                background: T.surface, borderRadius: 20,
                border: `1px solid ${T.border}`,
                boxShadow: "0 20px 60px rgba(20,60,22,0.12), 0 4px 16px rgba(20,60,22,0.08)",
                overflow: "hidden",
                animation: "fadeIn 0.35s ease",
                transition: "max-width 0.3s ease",
            }}>
                {/* Header */}
                <div style={{
                    background: `linear-gradient(135deg, ${T.accent} 0%, #388e3c 100%)`,
                    padding: "22px 32px 18px",
                    display: "flex", alignItems: "center", gap: 14,
                }}>
                <div style={{
                        width: 46, height: 46, borderRadius: 14, flexShrink: 0,
                        background: "rgba(255,255,255,0.15)",
                        border: "1.5px solid rgba(255,255,255,0.25)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                        <Cloud size={24} color="#fff" />
                    </div>
                    <div>
                        <div style={{ fontSize: 18, fontWeight: 800, color: "#fff", letterSpacing: "-0.02em" }}>PUCP Cloud</div>
                        <div style={{ fontSize: 10, color: "rgba(255,255,255,0.75)", letterSpacing: "0.1em", textTransform: "uppercase" }}>
                            {view === "register" ? "Nueva Cuenta" : "Orchestrator"}
                        </div>
                    </div>
                </div>

                {/* Body */}
                <div style={{ padding: "24px 32px 28px" }}>

                    {/* ── REGISTER view ── */}
                    {view === "register" && (
                        <RegisterPage onBack={() => setView("login")} />
                    )}

                    {/* ── LOGIN view ── */}
                    {view === "login" && (
                        <>
                            <div style={{ marginBottom: 20 }}>
                                <div style={{ fontSize: 18, fontWeight: 800, color: T.text, marginBottom: 4 }}>Iniciar sesión</div>
                                <div style={{ fontSize: 13, color: T.textMuted }}>Ingresa tus credenciales para continuar</div>

                                {isDemoMode && (
                                    <div style={{
                                        marginTop: 12, padding: "9px 14px", borderRadius: 9,
                                        background: "#fff8e1", border: "1px solid #f59f0033",
                                        fontSize: 12, color: "#92400e", lineHeight: 1.5,
                                        display: "flex", alignItems: "flex-start", gap: 8,
                                    }}>
                                        <FlaskConical size={14} color="#92400e" style={{ flexShrink: 0, marginTop: 1 }} />
                                        <span>
                                            <span style={{ fontWeight: 700 }}>Modo Demo.</span>{" "}
                                            Usa <code style={{ background: "#fef3c7", padding: "1px 5px", borderRadius: 4, fontFamily: "monospace" }}>demo@pucp.edu.pe</code>
                                            {" / "}
                                            <code style={{ background: "#fef3c7", padding: "1px 5px", borderRadius: 4, fontFamily: "monospace" }}>pucp2026</code>
                                        </span>
                                    </div>
                                )}
                            </div>

                            <form onSubmit={handleSubmit} noValidate style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                                <Field
                                    id="login-email" label="Correo Electrónico / Usuario"
                                    type="email" value={email} onChange={e => { setEmail(e.target.value); setError(null); }}
                                    placeholder="usuario@pucp.edu.pe" disabled={loading} IconComp={Mail}
                                />
                                <Field
                                    id="login-password" label="Contraseña"
                                    type={showPass ? "text" : "password"}
                                    value={password} onChange={e => { setPassword(e.target.value); setError(null); }}
                                    placeholder="••••••••" disabled={loading} IconComp={Lock}
                                    rightSlot={
                                        <button type="button" onClick={() => setShowPass(v => !v)}
                                            style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 2, display: "flex" }}>
                                            {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                                        </button>
                                    }
                                />

                                {error && (
                                    <div style={{
                                        display: "flex", alignItems: "center", gap: 8,
                                        background: T.redLight, border: `1px solid ${T.red}33`,
                                        borderRadius: 9, padding: "10px 14px",
                                        fontSize: 13, color: T.red, fontWeight: 600,
                                        animation: "shake 0.4s ease",
                                    }}>
                                        <AlertTriangle size={15} color={T.red} style={{ flexShrink: 0 }} /> {error}
                                    </div>
                                )}

                                <button type="submit"
                                    disabled={loading || !email.trim() || !password}
                                    style={{
                                        marginTop: 4, height: 48, borderRadius: 12, border: "none",
                                        background: (loading || !email.trim() || !password)
                                            ? T.border
                                            : `linear-gradient(135deg, ${T.accent} 0%, #388e3c 100%)`,
                                        color: (loading || !email.trim() || !password) ? T.textMuted : "#fff",
                                        fontSize: 15, fontWeight: 700, fontFamily: "inherit",
                                        cursor: (loading || !email.trim() || !password) ? "not-allowed" : "pointer",
                                        display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                                        transition: "all 0.2s",
                                        boxShadow: loading ? "none" : `0 4px 14px ${T.accent}44`,
                                    }}>
                                    {loading
                                        ? <><Loader size={15} style={{ animation: "spin 0.8s linear infinite" }} /> Verificando…</>
                                        : <><ArrowRight size={15} /> Ingresar</>}
                                </button>
                            </form>

                            {/* Register link */}
                            <div style={{ marginTop: 20, textAlign: "center", borderTop: `1px solid ${T.border}`, paddingTop: 16 }}>
                                <span style={{ fontSize: 13, color: T.textMuted }}>¿No tienes cuenta? </span>
                                <button onClick={() => setView("register")}
                                    style={{
                                        background: "none", border: "none", cursor: "pointer",
                                        fontSize: 13, color: T.accent, fontWeight: 700,
                                        fontFamily: "inherit", textDecoration: "underline", padding: 0,
                                    }}>
                                    Regístrate aquí
                                </button>
                            </div>
                        </>
                    )}
                </div>
            </div>

            {toast && <Toast msg={toast.msg} type={toast.type} />}

            <style>{`
                @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
                * { box-sizing: border-box; }
                @keyframes fadeIn { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }
                @keyframes shake  { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-5px)} 40%,80%{transform:translateX(5px)} }
                @keyframes spin   { to { transform:rotate(360deg); } }
                input:focus, select:focus { outline: none; }
            `}</style>
        </div>
    );
};
