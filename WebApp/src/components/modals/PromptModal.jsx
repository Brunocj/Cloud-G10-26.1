/**
 * PromptModal — reemplazo de window.prompt con el diseño de la app.
 *
 * Se usa para pedir un texto antes de una acción (p. ej. el motivo del rechazo
 * de una cuenta, que se envía por correo al solicitante).
 *
 * `required` bloquea el botón de confirmar mientras el campo esté vacío:
 * window.prompt permitía aceptar con la cadena vacía y el motivo llegaba en
 * blanco al correo.
 */
import { useState } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { MessageSquare } from "../ui/Icon";

export const PromptModal = ({
    title,
    msg,
    placeholder = "",
    confirmLabel = "Confirmar",
    required = false,
    multiline = true,
    onOk,
    onCancel,
}) => {
    const [value, setValue] = useState("");
    const canSubmit = !required || value.trim().length > 0;

    const submit = () => { if (canSubmit) onOk(value.trim()); };

    return (
        <Overlay label={title}>
            <div style={{
                background: T.surface, border: `1px solid ${T.border}`, borderRadius: 14,
                padding: 26, maxWidth: 440, width: "90%", boxShadow: T.shadowMd,
            }}>
                <div style={{
                    fontSize: 15, fontWeight: 800, color: T.text, marginBottom: 8,
                    display: "flex", alignItems: "center", gap: 8,
                }}>
                    <MessageSquare size={16} color={T.accent} /> {title}
                </div>

                {msg && (
                    <div style={{ fontSize: 13, color: T.textMuted, marginBottom: 14, lineHeight: 1.6 }}>
                        {msg}
                    </div>
                )}

                {multiline ? (
                    <textarea
                        autoFocus
                        rows={3}
                        value={value}
                        onChange={e => setValue(e.target.value)}
                        placeholder={placeholder}
                        style={{ ...inp, resize: "vertical", minHeight: 70, fontFamily: "inherit" }}
                    />
                ) : (
                    <input
                        autoFocus
                        value={value}
                        onChange={e => setValue(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && submit()}
                        placeholder={placeholder}
                        style={inp}
                    />
                )}

                {required && !canSubmit && (
                    <div style={{ fontSize: 10, color: T.textFaint, marginTop: 5 }}>
                        Campo obligatorio.
                    </div>
                )}

                <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 20 }}>
                    <button onClick={onCancel} style={btnBase()}>Cancelar</button>
                    <button onClick={submit} disabled={!canSubmit}
                        style={btnBase({
                            background: canSubmit ? T.accent : T.border,
                            color: canSubmit ? "#fff" : T.textMuted,
                            border: "none", fontWeight: 700,
                        })}>
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
