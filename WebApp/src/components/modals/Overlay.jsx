import { T } from "../../theme/tokens";

export const Overlay = ({ children }) => (
    <div style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000,
    }}>
        {children}
    </div>
);
