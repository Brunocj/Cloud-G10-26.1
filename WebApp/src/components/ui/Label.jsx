import { T } from "../../theme/tokens";

export const Label = ({ children, style: s }) => (
    <div style={{
        fontSize: 10, fontWeight: 700, color: T.textMuted,
        textTransform: "uppercase", letterSpacing: "0.07em",
        marginBottom: 5, ...s,
    }}>
        {children}
    </div>
);
