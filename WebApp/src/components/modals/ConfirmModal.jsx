import { T, btnBase } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { AlertTriangle, Trash2 } from "../ui/Icon";

export const ConfirmModal = ({ title, msg, onOk, onCancel }) => (
    <Overlay>
        <div style={{ background: T.surface, border: `1px solid ${T.red}33`, borderRadius: 14, padding: 26, maxWidth: 380, width: "90%", boxShadow: T.shadowMd }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: T.text, marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
                <AlertTriangle size={16} color={T.red} /> {title}
            </div>
            <div style={{ fontSize: 13, color: T.textMuted, marginBottom: 22, lineHeight: 1.6 }}>{msg}</div>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <button onClick={onCancel} style={btnBase()}>Cancel</button>
                <button onClick={onOk} style={btnBase({ background: T.redLight, color: T.red, border: `1px solid ${T.red}44`,
                    display: "flex", alignItems: "center", gap: 6 })}>
                    <Trash2 size={13} /> Yes, Destroy
                </button>
            </div>
        </div>
    </Overlay>
);
