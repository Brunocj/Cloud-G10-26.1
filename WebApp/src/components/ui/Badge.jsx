import { getStatusVisual } from "../../theme/tokens";

export const Badge = ({ status }) => {
    const { fg, bg, label, pulse } = getStatusVisual(status);
    return (
        <span style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            padding: "2px 9px", borderRadius: 20, fontSize: 10,
            fontWeight: 700, color: fg, background: bg,
            border: `1px solid ${fg}33`, textTransform: "uppercase",
            letterSpacing: "0.05em",
        }}>
            <span style={{
                width: 5, height: 5, borderRadius: "50%", background: fg,
                // Los estados transitorios laten para que se note que el sistema
                // está trabajando. El keyframe vive en getGlobalCss (tokens.js).
                animation: pulse ? "statusPulse 1.4s ease-in-out infinite" : undefined,
            }} />
            {label}
        </span>
    );
};
