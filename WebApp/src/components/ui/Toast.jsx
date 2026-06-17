import { T } from "../../theme/tokens";
import { CheckCircle, XCircle, AlertTriangle, Info } from "../ui/Icon";

const TYPES = {
    success: { bg: T.accentLight, border: T.accent,    color: T.accent,    Icon: CheckCircle   },
    error:   { bg: T.redLight,    border: T.red,        color: T.red,       Icon: XCircle       },
    warning: { bg: "#fff8e1",     border: "#f59f00",    color: "#b45309",   Icon: AlertTriangle },
    info:    { bg: T.surfaceElevated, border: T.border, color: T.textMuted, Icon: Info          },
};

export const Toast = ({ msg, type = "success" }) => {
    const s = TYPES[type] ?? TYPES.success;
    const { Icon } = s;
    return (
        <div style={{
            position: "fixed", bottom: 24, right: 24, zIndex: 9999,
            background: s.bg, border: `1px solid ${s.border}44`,
            color: s.color, padding: "12px 18px", borderRadius: 12,
            fontSize: 13, fontWeight: 600, boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
            display: "flex", alignItems: "center", gap: 10,
            animation: "slideUp 0.25s ease",
            maxWidth: 360,
        }}>
            <Icon size={16} color={s.color} style={{ flexShrink: 0 }} />
            {msg}
            <style>{`
                @keyframes slideUp {
                    from { opacity: 0; transform: translateY(12px); }
                    to   { opacity: 1; transform: translateY(0); }
                }
            `}</style>
        </div>
    );
};
