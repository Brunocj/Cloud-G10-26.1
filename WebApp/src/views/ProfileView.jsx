import { T, getGlobalCss } from "../theme/tokens";
import { Cloud }         from "../components/ui/Icon";
import { ThemePicker }   from "../components/ui/ThemePicker";
import { UserAvatar }    from "../components/ui/UserAvatar";
import { ProfilePage }   from "../components/profile/ProfilePage";

// ─── ProfileView ──────────────────────────────────────────────────────────────
// Full-screen view (no sidebar) with a slim topbar + ProfilePage.
export const ProfileView = ({ user, logout, reTheme, themeRev, onBack, apiFetch }) => (
    <div style={{
        display: "flex", height: "100vh",
        background: T.bg,
        fontFamily: "'DM Sans','Segoe UI',sans-serif",
        color: T.text, overflow: "hidden",
    }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* Slim topbar */}
            <div style={{
                padding: "0 20px", height: 54,
                borderBottom: `1px solid ${T.border}`, background: T.surface,
                display: "flex", alignItems: "center", gap: 12,
                flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)",
            }}>
                <div style={{ width: 38, height: 38, borderRadius: 10, background: T.accentLight, border: `1.5px solid ${T.accent}44`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Cloud size={20} color={T.accent} />
                </div>
                <span style={{ fontSize: 14, fontWeight: 800, color: T.text }}>PUCP Cloud</span>
                <div style={{ flex: 1 }} />
                <ThemePicker onThemeChange={reTheme} />
                <UserAvatar user={user} onLogout={logout} onProfile={() => {}} />
            </div>
            <ProfilePage user={user} onBack={onBack} apiFetch={apiFetch} />
        </div>
        <style key={themeRev}>{getGlobalCss()}</style>
    </div>
);
