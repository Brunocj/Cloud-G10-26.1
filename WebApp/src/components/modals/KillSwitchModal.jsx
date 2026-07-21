import { useState } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { AlertTriangle, X } from "../ui/Icon";

/**
 * KillSwitchModal (REQ-AD-07) — destrucción administrativa forzada.
 * Aparece cuando un admin/superAdmin (o jefe del proyecto) destruye un slice
 * que NO es suyo. Exige un motivo, que queda en los logs y se le notifica
 * al dueño en tiempo real.
 */
export const KillSwitchModal = ({ sliceName, ownerLabel, onConfirm, onClose }) => {
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);

    const submit = async () => {
        if (!reason.trim()) return;
        setBusy(true);
        await onConfirm(reason.trim());
        setBusy(false);
    };

    return (
        <Overlay>
            <div style={{
                background: T.surface, borderRadius: 14, width: 440,
                border: `1.5px solid ${T.red}66`, boxShadow: "0 12px 40px rgba(0,0,0,0.3)",
                padding: "24px 26px",
            }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 16, fontWeight: 800, color: T.red }}>
                        <AlertTriangle size={18} /> Forzar Destrucción (Kill Switch)
                    </div>
                    <button onClick={onClose} aria-label="Cerrar" style={btnBase({ padding: 4, background: "transparent", boxShadow: "none", color: T.textMuted })}>
                        <X size={16} />
                    </button>
                </div>

                <div style={{ fontSize: 12, color: T.textMuted, lineHeight: 1.55, marginBottom: 14 }}>
                    Estás por destruir <b style={{ color: T.text }}>"{sliceName}"</b>
                    {ownerLabel && <> de <b style={{ color: T.text }}>{ownerLabel}</b></>}.
                    Esta acción es <b style={{ color: T.red }}>irreversible</b>: las máquinas y sus
                    datos se perderán. El dueño recibirá una notificación con tu motivo.
                </div>

                <label style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                    Motivo de la Destrucción (obligatorio)
                </label>
                <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3}
                    placeholder='Ej. "Consumo excesivo de ancho de banda" o "Uso indebido de recursos"'
                    style={{ ...inp, marginTop: 6, resize: "vertical", minHeight: 60, fontFamily: "inherit" }} />

                <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancelar</button>
                    <button onClick={submit} disabled={busy || !reason.trim()}
                        style={btnBase({
                            flex: 2, background: T.red, color: "#fff", border: "none",
                            opacity: (busy || !reason.trim()) ? 0.5 : 1,
                            cursor: (busy || !reason.trim()) ? "not-allowed" : "pointer",
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                        })}>
                        <AlertTriangle size={13} /> {busy ? "Destruyendo…" : "Forzar Destrucción"}
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
