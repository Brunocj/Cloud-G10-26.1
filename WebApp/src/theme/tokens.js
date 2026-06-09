// ─── Theme Definitions ────────────────────────────────────────────────────────

const themes = {

    /** Forest Light — original */
    light: {
        bg:              "#f4f7f1",
        surface:         "#ffffff",
        surfaceElevated: "#eef3ea",
        border:          "#d5e4cc",
        borderHover:     "#9dc48e",
        accent:          "#2e7d32",
        accentLight:     "#e8f5e9",
        accentMid:       "#4caf50",
        red:             "#c62828",
        redLight:        "#ffebee",
        yellow:          "#e65100",
        yellowLight:     "#fff3e0",
        text:            "#1b2e1c",
        textMuted:       "#5a8060",
        textFaint:       "#adc8a8",
        shadow:          "0 1px 3px rgba(20,50,22,0.08), 0 4px 12px rgba(20,50,22,0.05)",
        shadowMd:        "0 4px 16px rgba(20,50,22,0.12), 0 8px 28px rgba(20,50,22,0.07)",
    },

    /** Midnight — dark mode */
    dark: {
        bg:              "#0f1117",
        surface:         "#1a1d27",
        surfaceElevated: "#21263a",
        border:          "#2a3050",
        borderHover:     "#3d4f7c",
        accent:          "#4ade80",
        accentLight:     "#0d2a1a",
        accentMid:       "#22c55e",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fb923c",
        yellowLight:     "#2a1a0a",
        text:            "#e2e8f0",
        textMuted:       "#7c9cba",
        textFaint:       "#3a4a60",
        shadow:          "0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.5), 0 8px 28px rgba(0,0,0,0.35)",
    },

    /** Slate Pro — professional dark navy */
    slate: {
        bg:              "#0d1520",
        surface:         "#132030",
        surfaceElevated: "#1a2d42",
        border:          "#1e3a52",
        borderHover:     "#2e5a7e",
        accent:          "#38bdf8",
        accentLight:     "#071a2e",
        accentMid:       "#0ea5e9",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fbbf24",
        yellowLight:     "#2a1e08",
        text:            "#e2eaf4",
        textMuted:       "#6b8fae",
        textFaint:       "#2a3d52",
        shadow:          "0 1px 3px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.4)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.6), 0 8px 28px rgba(0,0,0,0.45)",
    },

    /** Emerald Night — dark with green tones */
    emerald: {
        bg:              "#071210",
        surface:         "#0e1f1c",
        surfaceElevated: "#142c27",
        border:          "#1a3d34",
        borderHover:     "#1f5c4e",
        accent:          "#34d399",
        accentLight:     "#042018",
        accentMid:       "#10b981",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fbbf24",
        yellowLight:     "#2a1e08",
        text:            "#d1fae5",
        textMuted:       "#4d9d82",
        textFaint:       "#193d32",
        shadow:          "0 1px 3px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.3)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.5), 0 8px 28px rgba(0,0,0,0.35)",
    },

    /** Violet Dusk — purple accent */
    violet: {
        bg:              "#0e0c1a",
        surface:         "#16132a",
        surfaceElevated: "#1d1a36",
        border:          "#2a264a",
        borderHover:     "#3f3870",
        accent:          "#a78bfa",
        accentLight:     "#1a1030",
        accentMid:       "#8b5cf6",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fbbf24",
        yellowLight:     "#2a1e08",
        text:            "#ede9fe",
        textMuted:       "#7c6fa8",
        textFaint:       "#2a2448",
        shadow:          "0 1px 3px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.35)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.55), 0 8px 28px rgba(0,0,0,0.4)",
    },
};

// ─── Theme metadata for UI picker ─────────────────────────────────────────────
export const THEMES = [
    { id: "light",   label: "Forest",        dot: "#2e7d32",  dark: false },
    { id: "dark",    label: "Midnight",       dot: "#4ade80",  dark: true  },
    { id: "slate",   label: "Slate Pro",      dot: "#38bdf8",  dark: true  },
    { id: "emerald", label: "Emerald Night",  dot: "#34d399",  dark: true  },
    { id: "violet",  label: "Violet Dusk",    dot: "#a78bfa",  dark: true  },
];

// ─── Active token object — mutated at runtime by applyTheme() ─────────────────
export const T = { ...themes.light };

// Persisted preference
const STORAGE_KEY = "pucp_cloud_theme";

export function applyTheme(id) {
    const palette = themes[id] ?? themes.light;
    Object.assign(T, palette);
    try { localStorage.setItem(STORAGE_KEY, id); } catch {}
}

export function loadSavedTheme() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved && themes[saved]) applyTheme(saved);
    } catch {}
}

// Apply saved theme immediately on module load
loadSavedTheme();

// ─── Shared style helpers — always read live T values ─────────────────────────
export const btnBase = (extra = {}) => ({
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
    padding: "7px 14px", borderRadius: 8, border: `1px solid ${T.border}`,
    cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: "inherit",
    background: T.surface, color: T.text, transition: "opacity 0.15s",
    boxShadow: T.shadow, ...extra,
});

export const inp = {
    get background() { return T.surfaceElevated; },
    get border()     { return `1px solid ${T.border}`; },
    get color()      { return T.text; },
    fontSize: 13, padding: "8px 10px", width: "100%",
    outline: "none", fontFamily: "inherit", boxSizing: "border-box",
    borderRadius: 7,
};

// ─── Global CSS — rebuilt when theme changes ───────────────────────────────────
export const getGlobalCss = () => `
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
    * { box-sizing: border-box; margin: 0; padding: 0; }
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: ${T.surfaceElevated}; }
    ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 4px; }
    input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
    input:focus, select:focus { border-color: ${T.accentMid} !important; }
`;

// Backwards compat alias (used in LoginPage / ProfilePage)
export const globalCss = `
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
    * { box-sizing: border-box; }
    @keyframes fadeIn { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }
    @keyframes shake  { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-5px)} 40%,80%{transform:translateX(5px)} }
    @keyframes spin   { to { transform:rotate(360deg); } }
    input:focus, select:focus { outline: none; }
`;
