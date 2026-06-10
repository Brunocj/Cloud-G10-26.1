// ─── Theme Definitions ────────────────────────────────────────────────────────

const themes = {

    /** Azure Light — Portal Light Theme */
    azureLight: {
        bg:              "#f3f2f1",
        surface:         "#ffffff",
        surfaceElevated: "#faf9f8",
        border:          "#edebe9",
        borderHover:     "#c8c6c4",
        accent:          "#0078d4",
        accentLight:     "#eff6fc",
        accentMid:       "#2b88d8",
        red:             "#a80000",
        redLight:        "#fde7e9",
        yellow:          "#d83b01",
        yellowLight:     "#fde7d9",
        text:            "#323130",
        textMuted:       "#605e5c",
        textFaint:       "#a19f9d",
        shadow:          "0 1px 3px rgba(0,0,0,0.05), 0 4px 12px rgba(0,0,0,0.03)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.08), 0 8px 28px rgba(0,0,0,0.05)",
    },

    /** Azure Dark — Portal Dark Theme */
    azureDark: {
        bg:              "#111111",
        surface:         "#201f1e",
        surfaceElevated: "#292827",
        border:          "#323130",
        borderHover:     "#484644",
        accent:          "#2899f5",
        accentLight:     "#1b2738",
        accentMid:       "#0078d4",
        red:             "#f1707b",
        redLight:        "#441d22",
        yellow:          "#ffaa44",
        yellowLight:     "#442a10",
        text:            "#f3f2f1",
        textMuted:       "#c8c6c4",
        textFaint:       "#8a8886",
        shadow:          "0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.5), 0 8px 28px rgba(0,0,0,0.35)",
    },

    /** Azure Ocean — Deep Blue */
    azureOcean: {
        bg:              "#0a0f1d",
        surface:         "#111936",
        surfaceElevated: "#18244c",
        border:          "#1f2f63",
        borderHover:     "#2c448c",
        accent:          "#00bcf2",
        accentLight:     "#0b223a",
        accentMid:       "#0078d4",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fbbf24",
        yellowLight:     "#2a1e08",
        text:            "#e2efff",
        textMuted:       "#7c9cba",
        textFaint:       "#3a4a60",
        shadow:          "0 1px 3px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.4)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.6), 0 8px 28px rgba(0,0,0,0.45)",
    },

    /** Azure Purple — Indigo/Violet dusk */
    azurePurple: {
        bg:              "#0b0916",
        surface:         "#131024",
        surfaceElevated: "#1a1633",
        border:          "#252047",
        borderHover:     "#3a3270",
        accent:          "#b4a0ff",
        accentLight:     "#191236",
        accentMid:       "#8260ff",
        red:             "#f87171",
        redLight:        "#2a0f0f",
        yellow:          "#fbbf24",
        yellowLight:     "#2a1e08",
        text:            "#ede9fe",
        textMuted:       "#8c7cb8",
        textFaint:       "#2d2654",
        shadow:          "0 1px 3px rgba(0,0,0,0.55), 0 4px 12px rgba(0,0,0,0.35)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.6), 0 8px 28px rgba(0,0,0,0.45)",
    },

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
};

// ─── Theme metadata for UI picker ─────────────────────────────────────────────
export const THEMES = [
    { id: "azureLight",  label: "Azure Light",   dot: "#0078d4",  dark: false },
    { id: "azureDark",   label: "Azure Dark",    dot: "#2899f5",  dark: true  },
    { id: "azureOcean",  label: "Azure Ocean",   dot: "#00bcf2",  dark: true  },
    { id: "azurePurple", label: "Azure Purple",  dot: "#b4a0ff",  dark: true  },
    { id: "light",       label: "Forest Light",  dot: "#2e7d32",  dark: false },
    { id: "dark",        label: "Midnight Dark", dot: "#4ade80",  dark: true  },
];

// ─── Active token object — mutated at runtime by applyTheme() ─────────────────
export const T = { ...themes.azureLight };

// Persisted preference
const STORAGE_KEY = "pucp_cloud_theme";

export function applyTheme(id) {
    const palette = themes[id] ?? themes.azureLight;
    Object.assign(T, palette);
    try { localStorage.setItem(STORAGE_KEY, id); } catch {}
}

export function loadSavedTheme() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        // Fallback mapping for old keys
        const map = { light: "azureLight", dark: "azureDark", slate: "azureOcean", emerald: "azureDark", violet: "azurePurple" };
        const mapped = map[saved] || saved;
        if (mapped && themes[mapped]) applyTheme(mapped);
        else applyTheme("azureLight");
    } catch {
        applyTheme("azureLight");
    }
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
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@400;500;600;700;800&display=swap');
    
    * { 
        box-sizing: border-box; 
        margin: 0; 
        padding: 0; 
        font-family: 'Segoe UI', 'Inter', -apple-system, BlinkMacSystemFont, 'Outfit', sans-serif;
    }

    body {
        background-color: ${T.bg};
        color: ${T.text};
        font-family: 'Segoe UI', 'Inter', -apple-system, BlinkMacSystemFont, 'Outfit', sans-serif;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    button, input, select, textarea {
        font-family: inherit;
    }

    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: ${T.surfaceElevated}; }
    ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: ${T.borderHover}; }
    
    input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
    input:focus, select:focus { border-color: ${T.accentMid} !important; }
`;

// Backwards compat alias (used in LoginPage / ProfilePage)
export const globalCss = `
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    * { 
        box-sizing: border-box; 
        font-family: 'Segoe UI', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    @keyframes fadeIn { from { opacity:0; transform:translateY(15px); } to { opacity:1; transform:translateY(0); } }
    @keyframes shake  { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-5px)} 40%,80%{transform:translateX(5px)} }
    @keyframes spin   { to { transform:rotate(360deg); } }
    input:focus, select:focus { outline: none; }
`;
