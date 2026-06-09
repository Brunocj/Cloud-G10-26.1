import { T } from "../../theme/tokens";

export const Badge = ({ status }) => {
    const map = {
        ACTIVE:           [T.accent,    T.accentLight],
        DRAFT:            [T.textMuted, T.surfaceElevated],
        PROVISIONING:     ["#1976d2",   "#e3f2fd"],
        PENDING_APPROVAL: [T.yellow,    T.yellowLight],
        FAILED:           [T.red,       T.redLight],
        TERMINATED:       ["#616161",   "#eeeeee"],
    };
    const [c, bg] = map[status] || map.DRAFT;
    return (
        <span style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            padding: "2px 9px", borderRadius: 20, fontSize: 10,
            fontWeight: 700, color: c, background: bg,
            border: `1px solid ${c}33`, textTransform: "uppercase",
            letterSpacing: "0.05em",
        }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: c }} />
            {status}
        </span>
    );
};
