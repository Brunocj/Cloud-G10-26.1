import { useState } from "react";
import { T, inp } from "../../theme/tokens";
import { Send, Loader, ArrowLeft, CheckCircle, Clock, AlertTriangle } from "../ui/Icon";

const CARRERAS = [
    "Ingeniería Informática", "Ingeniería de Sistemas",
    "Ingeniería Electrónica", "Ingeniería Mecatrónica",
    "Ingeniería Industrial", "Ingeniería Civil",
    "Ciencias de la Computación", "Matemáticas", "Física", "Otra",
];

const Field = ({ id, label, type = "text", value, onChange, placeholder, disabled, as: As = "input", children }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <label htmlFor={id} style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.07em" }}>
            {label}
        </label>
        {As === "select" ? (
            <select id={id} value={value} onChange={onChange} disabled={disabled}
                style={{ ...inp, height: 40, fontSize: 13, border: `1.5px solid ${T.border}`, borderRadius: 9, cursor: "pointer" }}
                onFocus={e => { e.target.style.borderColor = T.accent; e.target.style.boxShadow = `0 0 0 3px ${T.accent}18`; }}
                onBlur={e  => { e.target.style.borderColor = T.border;  e.target.style.boxShadow = "none"; }}>
                <option value="">Selecciona tu carrera…</option>
                {CARRERAS.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
        ) : (
            <input id={id} type={type} value={value} onChange={onChange} placeholder={placeholder} disabled={disabled}
                style={{ ...inp, height: 40, fontSize: 13, border: `1.5px solid ${T.border}`, borderRadius: 9, paddingLeft: 12 }}
                onFocus={e => { e.target.style.borderColor = T.accent; e.target.style.boxShadow = `0 0 0 3px ${T.accent}18`; }}
                onBlur={e  => { e.target.style.borderColor = T.border;  e.target.style.boxShadow = "none"; }} />
        )}
    </div>
);

// ── Success screen ────────────────────────────────────────────────────────────
const SuccessScreen = ({ nombre, onBack }) => (
    <div style={{ textAlign: "center", padding: "8px 0 4px" }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 14 }}>
            <div style={{ width: 72, height: 72, borderRadius: "50%", background: T.accentLight, border: `2px solid ${T.accent}33`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <CheckCircle size={36} color={T.accent} />
            </div>
        </div>
        <div style={{ fontSize: 18, fontWeight: 800, color: T.text, marginBottom: 8 }}>
            ¡Solicitud enviada!
        </div>
        <div style={{
            background: T.accentLight, border: `1px solid ${T.accent}33`,
            borderRadius: 12, padding: "14px 18px", marginBottom: 18,
            fontSize: 13, color: T.accent, lineHeight: 1.6, textAlign: "left",
        }}>
            <strong>Hola, {nombre}.</strong><br />
            Tu cuenta está en estado <strong>"Pendiente"</strong>.<br />
            Un administrador revisará tu solicitud pronto y recibirás
            una notificación a tu correo institucional.
        </div>
        <div style={{
            background: "#fff8e1", border: "1px solid #f59f0033",
            borderRadius: 10, padding: "10px 14px", marginBottom: 20,
            fontSize: 12, color: "#92400e", lineHeight: 1.5,
            display: "flex", alignItems: "flex-start", gap: 8,
        }}>
            <Clock size={13} color="#92400e" style={{ flexShrink: 0, marginTop: 2 }} />
            <span>Mientras tanto, no podrás acceder al dashboard. Vuelve a intentar
            iniciar sesión una vez que tu cuenta haya sido aprobada.</span>
        </div>
        <button onClick={onBack} style={{
            width: "100%", height: 44, borderRadius: 10, border: `1.5px solid ${T.border}`,
            background: T.surfaceElevated, color: T.text, fontSize: 14, fontWeight: 700,
            fontFamily: "inherit", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
        }}>
            <ArrowLeft size={15} /> Volver al Login
        </button>
    </div>
);

// ── Register form ─────────────────────────────────────────────────────────────
export const RegisterPage = ({ onBack }) => {
    const [form, setForm] = useState({
        nombre: "", username: "", email: "", codigo: "",
        carrera: "", password: "", confirm: "",
    });
    const [errors,    setErrors]    = useState({});
    const [loading,   setLoading]   = useState(false);
    const [submitted, setSubmitted] = useState(false);

    const set = (key) => (e) => setForm(p => ({ ...p, [key]: e.target.value }));

    const validate = () => {
        const e = {};
        if (!form.nombre.trim())                              e.nombre    = "Requerido";
        if (!form.username.trim())                            e.username  = "Requerido";
        if (!form.email.endsWith("@pucp.edu.pe"))             e.email     = "Debe ser un correo @pucp.edu.pe";
        if (!/^\d{7,9}$/.test(form.codigo))                  e.codigo    = "Código PUCP inválido (7-9 dígitos)";
        if (!form.carrera)                                    e.carrera   = "Selecciona una carrera";
        if (form.password.length < 8)                         e.password  = "Mínimo 8 caracteres";
        if (form.password !== form.confirm)                   e.confirm   = "Las contraseñas no coinciden";
        return e;
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        const v = validate();
        if (Object.keys(v).length) { setErrors(v); return; }
        setLoading(true);
        try {
            const API_BASE = import.meta.env.VITE_API_BASE ?? "http://10.20.11.212:8085/api/v1";
            const nameParts = form.nombre.trim().split(/\s+/);
            const res = await fetch(`${API_BASE}/users/register`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username:  form.username.trim(),
                    email:     form.email.trim(),
                    password:  form.password,
                    fullname:  nameParts.slice(0, -1).join(" ") || nameParts[0],
                    lastname:  nameParts.length > 1 ? nameParts[nameParts.length - 1] : "",
                    pucp_code: form.codigo,
                    career:    form.carrera,
                }),
            });
            if (res.ok) {
                setSubmitted(true);
            } else {
                const data = await res.json().catch(() => ({}));
                setErrors({ email: data.detail || `Error ${res.status} al registrar` });
            }
        } catch {
            setErrors({ email: "No se pudo conectar al servidor" });
        }
        setLoading(false);
    };

    if (submitted) return <SuccessScreen nombre={form.nombre.split(" ")[0]} onBack={onBack} />;

    return (
        <>
            <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: T.text, marginBottom: 4 }}>Crear cuenta</div>
                <div style={{ fontSize: 12, color: T.textMuted }}>Solo para miembros de la comunidad PUCP</div>
            </div>

            <form onSubmit={handleSubmit} noValidate style={{ display: "flex", flexDirection: "column", gap: 11 }}>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div>
                        <Field id="reg-nombre" label="Nombre Completo" value={form.nombre} onChange={set("nombre")} placeholder="Juan Pérez" disabled={loading} />
                        {errors.nombre && <Err>{errors.nombre}</Err>}
                    </div>
                    <div>
                        <Field id="reg-username" label="Nombre de usuario" value={form.username} onChange={set("username")} placeholder="jperez" disabled={loading} />
                        {errors.username && <Err>{errors.username}</Err>}
                    </div>
                </div>

                <div>
                    <Field id="reg-email" label="Correo Institucional" type="email" value={form.email} onChange={set("email")} placeholder="a20XXXXXX@pucp.edu.pe" disabled={loading} />
                    {errors.email && <Err>{errors.email}</Err>}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div>
                        <Field id="reg-codigo" label="Código PUCP" value={form.codigo} onChange={set("codigo")} placeholder="20XXXXXXX" disabled={loading} />
                        {errors.codigo && <Err>{errors.codigo}</Err>}
                    </div>
                    <div>
                        <Field id="reg-carrera" label="Carrera" as="select" value={form.carrera} onChange={set("carrera")} disabled={loading} />
                        {errors.carrera && <Err>{errors.carrera}</Err>}
                    </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div>
                        <Field id="reg-pass" label="Contraseña" type="password" value={form.password} onChange={set("password")} placeholder="••••••••" disabled={loading} />
                        {errors.password && <Err>{errors.password}</Err>}
                    </div>
                    <div>
                        <Field id="reg-confirm" label="Confirmar Contraseña" type="password" value={form.confirm} onChange={set("confirm")} placeholder="••••••••" disabled={loading} />
                        {errors.confirm && <Err>{errors.confirm}</Err>}
                    </div>
                </div>

                <button type="submit" disabled={loading} style={{
                    marginTop: 6, height: 46, borderRadius: 11, border: "none",
                    background: loading ? T.border : `linear-gradient(135deg, ${T.accent} 0%, #388e3c 100%)`,
                    color: loading ? T.textMuted : "#fff",
                    fontSize: 14, fontWeight: 700, fontFamily: "inherit",
                    cursor: loading ? "not-allowed" : "pointer",
                    boxShadow: loading ? "none" : `0 4px 14px ${T.accent}44`,
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                    transition: "all 0.2s",
                }}>
                    {loading
                        ? <><Loader size={15} style={{ animation: "spin 0.8s linear infinite", flexShrink: 0 }} /> Enviando…</>
                        : <><Send size={15} /> Enviar Solicitud</>}
                </button>

                <button type="button" onClick={onBack} style={{
                    background: "none", border: "none", cursor: "pointer",
                    fontSize: 13, color: T.textMuted, fontFamily: "inherit",
                    textDecoration: "underline", padding: "2px 0",
                    display: "flex", alignItems: "center", gap: 5,
                }}>
                    <ArrowLeft size={13} /> Volver al Login
                </button>
            </form>
        </>
    );
};

const Err = ({ children }) => (
    <div style={{ fontSize: 10, color: T.red, marginTop: 3, fontWeight: 600 }}>{children}</div>
);
