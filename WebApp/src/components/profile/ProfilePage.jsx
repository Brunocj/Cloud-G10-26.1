import { useState } from "react";
import { T, btnBase } from "../../theme/tokens";
import { ShieldCheck, ArrowLeft, Save, Key, CheckCircle, AlertTriangle, Loader, Download } from "../ui/Icon";

// ─── Crypto helpers ───────────────────────────────────────────────────────────

const bufferToBase64 = (buf) => {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin);
};

const toPem = (b64, type) => {
    const lines = b64.match(/.{1,64}/g)?.join("\n") ?? b64;
    return `-----BEGIN ${type}-----\n${lines}\n-----END ${type}-----\n`;
};

/**
 * Generates a 2048-bit RSA key pair using the Web Crypto API.
 * Returns { privatePem, publicPem } — no server involved.
 *
 * Future: replace this client-side generation with a call to
 *   POST /api/v1/auth/ssh-keys/generate
 * which returns { public_key: "...", download_url: "..." }
 */
const generateRsaKeyPair = async () => {
    const pair = await window.crypto.subtle.generateKey(
        { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
        true,
        ["encrypt", "decrypt"],
    );
    const privBuf = await window.crypto.subtle.exportKey("pkcs8", pair.privateKey);
    const pubBuf  = await window.crypto.subtle.exportKey("spki",  pair.publicKey);
    return {
        privatePem: toPem(bufferToBase64(privBuf), "PRIVATE KEY"),
        publicPem:  toPem(bufferToBase64(pubBuf),  "PUBLIC KEY"),
    };
};

const downloadText = (content, filename) => {
    const blob = new Blob([content], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
};

// ─── Section card ─────────────────────────────────────────────────────────────
const Section = ({ title, IconComp, children }) => (
    <div style={{
        background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 16, padding: "22px 24px", boxShadow: T.shadow,
        flexShrink: 0,   // hijos de columna flex: sin esto se comprimen en vez de hacer scroll
    }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: T.text, marginBottom: 18,
            display: "flex", alignItems: "center", gap: 8,
            borderBottom: `1px solid ${T.border}`, paddingBottom: 14 }}>
            {IconComp && <IconComp size={16} color={T.accent} />} {title}
        </div>
        {children}
    </div>
);

const StatChip = ({ label, value, accent }) => (
    <div style={{ background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 10, padding: "10px 16px" }}>
        <div style={{ fontSize: 10, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>{label}</div>
        <div style={{ fontSize: 14, fontWeight: 700, color: accent ?? T.text }}>{value}</div>
    </div>
);

// ─── ProfilePage ──────────────────────────────────────────────────────────────
export const ProfilePage = ({ user, onBack, apiFetch }) => {
    const [sshKey,      setSshKey]      = useState("");
    const [keySaved,    setKeySaved]    = useState(false);
    const [generating,  setGenerating]  = useState(false);
    const [genSuccess,  setGenSuccess]  = useState(false);
    const [saveLoading, setSaveLoading] = useState(false);
    const [flashMsg,    setFlashMsg]    = useState(null);

    // Cambio de contraseña (autoservicio via Keycloak)
    const [pwCurrent, setPwCurrent] = useState("");
    const [pwNew,     setPwNew]     = useState("");
    const [pwConfirm, setPwConfirm] = useState("");
    const [pwBusy,    setPwBusy]    = useState(false);

    const handleChangePassword = async () => {
        if (!apiFetch) { flash("No disponible en modo demo.", "error"); return; }
        if (pwNew.length < 6) { flash("La nueva contraseña debe tener al menos 6 caracteres.", "error"); return; }
        if (pwNew !== pwConfirm) { flash("Las contraseñas nuevas no coinciden.", "error"); return; }
        setPwBusy(true);
        try {
            const res = await apiFetch("/users/me/password", {
                method: "POST",
                body: JSON.stringify({ current_password: pwCurrent, new_password: pwNew }),
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                flash("Contraseña actualizada correctamente.");
                setPwCurrent(""); setPwNew(""); setPwConfirm("");
            } else {
                flash(data.detail || "No se pudo cambiar la contraseña.", "error");
            }
        } catch { flash("Error de conexión.", "error"); }
        setPwBusy(false);
    };

    const flash = (msg, type = "success") => {
        setFlashMsg({ msg, type });
        setTimeout(() => setFlashMsg(null), 3500);
    };

    const initials = (() => {
        const src = user?.name || user?.email || "?";
        const parts = src.trim().split(/\s+/);
        return parts.length >= 2
            ? (parts[0][0] + parts[1][0]).toUpperCase()
            : src.slice(0, 2).toUpperCase();
    })();

    // ── Save SSH public key (demo) ────────────────────────────────────────────
    const handleSaveKey = async () => {
        if (!sshKey.trim()) { flash("Pega tu llave pública primero.", "error"); return; }
        setSaveLoading(true);
        // TODO: POST /api/v1/auth/ssh-keys  { public_key: sshKey }
        await new Promise(r => setTimeout(r, 700)); // demo delay
        setSaveLoading(false);
        setKeySaved(true);
        flash("Llave SSH guardada correctamente.");
    };

    // ── Generate key pair (demo — Web Crypto API) ─────────────────────────────
    const handleGenerate = async () => {
        setGenerating(true);
        setGenSuccess(false);
        try {
            const { privatePem, publicPem } = await generateRsaKeyPair();

            // Auto-fill the textarea with the public key
            setSshKey(publicPem);
            setKeySaved(false);

            // Trigger .pem download (one-time, as per spec)
            downloadText(privatePem, `pucp_cloud_${user?.email?.split("@")[0] ?? "user"}_rsa.pem`);

            setGenSuccess(true);
            flash("Par de llaves generado. El archivo .pem se ha descargado.");
        } catch (e) {
            flash("Error al generar las llaves. Tu navegador puede no soportar Web Crypto.", "error");
        } finally {
            setGenerating(false);
        }
    };

    return (
        <div style={{
            flex: 1, overflowY: "auto", background: T.bg,
            padding: "28px 36px", display: "flex", flexDirection: "column", gap: 20,
        }}>
            {/* Breadcrumb */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button onClick={onBack} style={btnBase({ fontSize: 12, padding: "5px 14px", boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver al Dashboard
                </button>
                <span style={{ color: T.textMuted, fontSize: 12 }}>/ Mi Perfil</span>
            </div>

            {/* User hero card */}
            <div style={{
                background: T.surface, border: `1px solid ${T.border}`,
                borderRadius: 20, padding: "28px 28px 22px",
                display: "flex", alignItems: "flex-start", gap: 22,
                boxShadow: T.shadowMd,
                position: "relative", overflow: "hidden",
                flexShrink: 0,
            }}>
                {/* Green stripe accent */}
                <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 5, background: `linear-gradient(90deg, ${T.accent}, #66bb6a)` }} />

                {/* Avatar circle */}
                <div style={{
                    width: 72, height: 72, borderRadius: "50%", flexShrink: 0,
                    background: `linear-gradient(135deg, ${T.accent} 0%, #388e3c 100%)`,
                    color: "#fff", display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 26, fontWeight: 800,
                    boxShadow: `0 4px 16px ${T.accent}44`,
                }}>
                    {initials}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 20, fontWeight: 800, color: T.text, marginBottom: 2 }}>
                        {user?.name ?? "Usuario"}
                    </div>
                    <div style={{ fontSize: 13, color: T.textMuted, marginBottom: 12 }}>
                        {user?.email ?? ""}
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                        <StatChip label="Rol"    value={user?.role ?? "Investigador"} accent={T.accent} />
                        <StatChip label="Estado" value="Activo" accent={T.accent} />
                        {user?.carrera && <StatChip label="Carrera" value={user.carrera} />}
                        {user?.codigo  && <StatChip label="Código PUCP" value={user.codigo} />}
                    </div>
                </div>
            </div>

            {/* ── Change password section ── */}
            <Section title="Cambiar Contraseña" IconComp={ShieldCheck}>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 14, lineHeight: 1.6 }}>
                    Actualiza la contraseña con la que inicias sesión. Necesitas tu contraseña actual para confirmar el cambio.
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                    {[
                        ["Contraseña actual",    pwCurrent, setPwCurrent],
                        ["Nueva contraseña",     pwNew,     setPwNew],
                        ["Confirmar nueva",      pwConfirm, setPwConfirm],
                    ].map(([label, value, setter]) => (
                        <div key={label}>
                            <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 5 }}>{label}</div>
                            <input type="password" value={value} onChange={e => setter(e.target.value)}
                                style={{
                                    width: "100%", padding: "9px 12px", borderRadius: 8, fontSize: 13,
                                    border: `1.5px solid ${T.border}`, background: T.surfaceElevated,
                                    color: T.text, fontFamily: "inherit", outline: "none", boxSizing: "border-box",
                                }} />
                        </div>
                    ))}
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
                    <button onClick={handleChangePassword}
                        disabled={pwBusy || !pwCurrent || !pwNew || !pwConfirm}
                        style={btnBase({
                            fontSize: 13, padding: "8px 20px",
                            background: (pwCurrent && pwNew && pwConfirm) ? T.accent : T.border,
                            color: (pwCurrent && pwNew && pwConfirm) ? "#fff" : T.textMuted,
                            border: "none",
                            opacity: pwBusy ? 0.7 : 1,
                        })}>
                        {pwBusy ? "Guardando…" : "Cambiar Contraseña"}
                    </button>
                </div>
            </Section>

            {/* ── Security section ── */}
            <Section title="Seguridad y Acceso" IconComp={ShieldCheck}>

                {/* SSH Public Key textarea */}
                <div style={{ marginBottom: 20 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 6 }}>
                        Llave Pública SSH <span style={{ fontSize: 11, color: T.textMuted, fontWeight: 400 }}>(id_rsa.pub)</span>
                    </div>
                    <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 10, lineHeight: 1.6 }}>
                        Pega aquí el contenido de tu archivo <code style={{ background: T.surfaceElevated, padding: "1px 5px", borderRadius: 4, fontFamily: "monospace", fontSize: 11 }}>~/.ssh/id_rsa.pub</code>.
                        Esta llave se asociará a tus VMs y te permitirá acceder a ellas vía SSH.
                    </div>

                    <textarea
                        value={sshKey}
                        onChange={e => { setSshKey(e.target.value); setKeySaved(false); }}
                        placeholder={"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAB... usuario@maquina"}
                        rows={5}
                        style={{
                            width: "100%", padding: "12px 14px", borderRadius: 10,
                            border: `1.5px solid ${keySaved ? T.accent : T.border}`,
                            background: keySaved ? T.accentLight : T.surfaceElevated,
                            color: T.text, fontFamily: "monospace", fontSize: 12,
                            resize: "vertical", lineHeight: 1.6,
                            transition: "border-color 0.2s, background 0.2s",
                            outline: "none",
                            boxSizing: "border-box",
                        }}
                        onFocus={e  => { if (!keySaved) e.target.style.borderColor = T.accent; }}
                        onBlur={e   => { if (!keySaved) e.target.style.borderColor = T.border; }}
                    />

                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                        <button onClick={handleSaveKey} disabled={saveLoading || !sshKey.trim()}
                            style={btnBase({
                                fontSize: 13, padding: "8px 20px",
                                background: sshKey.trim() ? T.accent : T.border,
                                color: sshKey.trim() ? "#fff" : T.textMuted,
                                border: "none",
                                boxShadow: sshKey.trim() ? `0 3px 10px ${T.accent}44` : "none",
                                opacity: saveLoading ? 0.7 : 1,
                                display: "flex", alignItems: "center", gap: 7,
                            })}>
                            {saveLoading
                                ? <><Loader size={13} style={{ animation: "spin 0.8s linear infinite" }} /> Guardando…</>
                                : keySaved
                                    ? <><CheckCircle size={13} /> Llave Guardada</>
                                    : <><Save size={13} /> Guardar Llave</>}
                        </button>
                    </div>
                </div>

                {/* Divider */}
                <div style={{ height: 1, background: T.border, margin: "4px 0 20px" }} />

                {/* Generate key pair */}
                <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 6 }}>
                        Generar nuevo par de llaves
                    </div>
                    <div style={{
                        background: "#fff8e1", border: "1px solid #f59f0033",
                        borderRadius: 10, padding: "12px 16px", marginBottom: 14,
                        fontSize: 12, color: "#92400e", lineHeight: 1.6,
                        display: "flex", alignItems: "flex-start", gap: 8,
                    }}>
                        <AlertTriangle size={13} color="#92400e" style={{ flexShrink: 0, marginTop: 2 }} />
                        <span><strong>Descarga única.</strong> Al generar un nuevo par, la llave privada (<code style={{ fontFamily: "monospace" }}>.pem</code>) se descargará automáticamente
                        al navegador <strong>por única vez</strong> y no podrá recuperarse después. Guarda el archivo en un lugar seguro.</span>
                    </div>

                    {genSuccess && (
                        <div style={{
                            background: T.accentLight, border: `1px solid ${T.accent}33`,
                            borderRadius: 10, padding: "10px 16px", marginBottom: 14,
                            fontSize: 12, color: T.accent, lineHeight: 1.6,
                            display: "flex", alignItems: "flex-start", gap: 8,
                        }}>
                            <CheckCircle size={13} color={T.accent} style={{ flexShrink: 0, marginTop: 2 }} />
                            <span>Par de llaves generado correctamente. El archivo <code style={{ fontFamily: "monospace" }}>.pem</code> fue descargado.
                            La llave pública ya aparece en el campo de arriba — presiona "Guardar Llave" para asociarla a tu perfil.</span>
                        </div>
                    )}

                    <button onClick={handleGenerate} disabled={generating}
                        style={btnBase({
                            fontSize: 13, padding: "9px 20px",
                            background: generating ? T.border : T.surfaceElevated,
                            color: generating ? T.textMuted : T.text,
                            border: `1.5px solid ${T.border}`,
                            boxShadow: "none",
                            opacity: generating ? 0.7 : 1,
                            display: "flex", alignItems: "center", gap: 8,
                        })}>
                        {generating
                            ? <><Loader size={14} style={{ animation: "spin 0.8s linear infinite" }} /> Generando…</>
                            : <><Key size={14} /> Generar nuevo par de llaves</>}
                    </button>
                </div>
            </Section>

            {/* Inline flash */}
            {flashMsg && (
                <div style={{
                    position: "fixed", bottom: 24, right: 24, zIndex: 9999,
                    background: flashMsg.type === "error" ? T.redLight : T.accentLight,
                    border: `1px solid ${flashMsg.type === "error" ? T.red : T.accent}44`,
                    color: flashMsg.type === "error" ? T.red : T.accent,
                    padding: "12px 20px", borderRadius: 12, fontSize: 13, fontWeight: 600,
                    boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
                    animation: "slideUp 0.25s ease",
                }}>
                    {flashMsg.msg}
                </div>
            )}
            <style>{`
                @keyframes spin    { to { transform: rotate(360deg); } }
                @keyframes slideUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
            `}</style>
        </div>
    );
};
