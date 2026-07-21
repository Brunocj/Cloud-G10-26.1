import { T } from "../../theme/tokens";
import { CheckCircle, XCircle, AlertTriangle, Info } from "../ui/Icon";

// OJO: tiene que ser una función, no un objeto a nivel de módulo. `applyTheme()`
// MUTA el objeto T en vez de reemplazarlo, así que unas constantes evaluadas al
// importar se quedarían congeladas con los colores del tema inicial y los toasts
// no seguirían los cambios de tema.
const getTypes = () => ({
    success: { bg: T.accentLight,     border: T.accent, color: T.accent,    Icon: CheckCircle   },
    error:   { bg: T.redLight,        border: T.red,    color: T.red,       Icon: XCircle       },
    warning: { bg: T.yellowLight,     border: T.yellow, color: T.yellow,    Icon: AlertTriangle },
    info:    { bg: T.surfaceElevated, border: T.border, color: T.textMuted, Icon: Info          },
});

export const Toast = ({ msg, type = "success" }) => {
    const types = getTypes();
    const s = types[type] ?? types.success;
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
