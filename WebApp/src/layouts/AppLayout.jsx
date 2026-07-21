import { T, getGlobalCss, FONT_STACK } from "../theme/tokens";
import { Toast } from "../components/ui/Toast";

// ─── AppLayout ────────────────────────────────────────────────────────────────
// Wraps sidebar (left) + main content (right) + global overlays (toast, CSS).
export const AppLayout = ({ sidebar, children, toast, themeRev }) => (
    <div style={{
        display: "flex", height: "100vh",
        background: T.bg,
        fontFamily: FONT_STACK,
        color: T.text, overflow: "hidden",
    }}>
        {sidebar}

        {/* Main content area */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {children}
        </div>

        {toast && <Toast {...toast} />}
        <style key={themeRev}>{getGlobalCss()}</style>
    </div>
);
