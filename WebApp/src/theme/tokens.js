// ─── Theme Definitions ────────────────────────────────────────────────────────

const themes = {

    /** Azure Light — Portal Light Theme */
    azureLight: {
        bg:              "#f3f2f1",
        surface:         "#ffffff",
        surfaceElevated: "#faf9f8",
        border:          "#edebe9",
        borderHover:     "#c8c6c4",
        // #0072ca en vez del #0078d4 de Azure: 6/255 y 10/255 de diferencia
        // (imperceptible) pero sube el badge ACTIVO sobre accentLight de
        // 4.15:1 a 4.52:1 y el blanco sobre botones de 4.53:1 a 4.93:1.
        accent:          "#0072ca",
        accentLight:     "#eff6fc",
        accentMid:       "#2b88d8",
        red:             "#a80000",
        redLight:        "#fde7e9",
        yellow:          "#c53601",
        yellowLight:     "#fde7d9",
        text:            "#323130",
        textMuted:       "#545251",
        textFaint:       "#6f6d6b",
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
        textFaint:       "#91908e",
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
        textMuted:       "#9ab3ca",
        textFaint:       "#7a90af",
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
        textMuted:       "#a99dca",
        textFaint:       "#857ac3",
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
        yellow:          "#c54600",
        yellowLight:     "#fff3e0",
        text:            "#1b2e1c",
        textMuted:       "#3e5842",
        textFaint:       "#52764b",
        // Sombras con tinte verde a propósito: son las del tema Forest.
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
        textMuted:       "#9bb4ca",
        textFaint:       "#7a91af",
        shadow:          "0 1px 3px rgba(0,0,0,0.4), 0 4px 12px rgba(0,0,0,0.3)",
        shadowMd:        "0 4px 16px rgba(0,0,0,0.5), 0 8px 28px rgba(0,0,0,0.35)",
    },
};

// ─── Theme metadata for UI picker ─────────────────────────────────────────────
export const THEMES = [
    { id: "azureLight",  label: "Azure Light",   dot: "#0072ca",  dark: false },
    { id: "azureDark",   label: "Azure Dark",    dot: "#2899f5",  dark: true  },
    { id: "azureOcean",  label: "Azure Ocean",   dot: "#00bcf2",  dark: true  },
    { id: "azurePurple", label: "Azure Purple",  dot: "#b4a0ff",  dark: true  },
    { id: "light",       label: "Forest Light",  dot: "#2e7d32",  dark: false },
    { id: "dark",        label: "Midnight Dark", dot: "#4ade80",  dark: true  },
];

// ─── Tipografía ───────────────────────────────────────────────────────────────
// Un único stack para toda la app. Se usan fuentes del sistema a propósito: en
// Windows 'Segoe UI' ganaba siempre sobre las webfonts que se importaban desde
// Google Fonts (Inter / Outfit / DM Sans), así que esas descargas solo añadían
// latencia y un riesgo de render bloqueado si la red va lenta.
export const FONT_STACK =
    "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif";

export const DEFAULT_THEME = "azureLight";

// ─── Active token object — mutated at runtime by applyTheme() ─────────────────
export const T = { ...themes[DEFAULT_THEME] };

// Persisted preference
const STORAGE_KEY = "pucp_cloud_theme";

// Id del tema activo. Se expone con getCurrentTheme() para que el ThemePicker
// muestre exactamente el que está aplicado (antes lo releía de localStorage con
// su propio default y podía desincronizarse en el primer arranque).
let currentThemeId = DEFAULT_THEME;

export const getCurrentTheme = () => currentThemeId;

export function applyTheme(id) {
    const resolved = themes[id] ? id : DEFAULT_THEME;
    Object.assign(T, themes[resolved]);
    currentThemeId = resolved;
    try { localStorage.setItem(STORAGE_KEY, resolved); } catch {}
}

export function loadSavedTheme() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        // Remapeo de ids antiguos que YA NO existen en `themes`. OJO: 'light' y
        // 'dark' NO van aquí — siguen siendo ids válidos (Forest Light y
        // Midnight Dark), y mapearlos hacía imposible conservarlos entre
        // recargas: se guardaban y al volver se reescribían como Azure.
        const legacy = { slate: "azureOcean", emerald: "azureDark", violet: "azurePurple" };
        applyTheme(themes[saved] ? saved : (legacy[saved] ?? DEFAULT_THEME));
    } catch {
        applyTheme(DEFAULT_THEME);
    }
}

// Apply saved theme immediately on module load
loadSavedTheme();

// ─── Estados de slice ─────────────────────────────────────────────────────────
/**
 * Mapa único estado → color + etiqueta, compartido por el Badge y el canvas.
 * Es función (no constante) porque lee los valores vivos de T.
 *
 * Dos cosas que arregla respecto del mapa que vivía dentro de Badge.jsx:
 *  - PROVISIONING era un azul fijo (#1976d2) prácticamente idéntico al accent
 *    de los temas Azure (#0078d4), así que ACTIVO y PROVISIONANDO se veían
 *    iguales — justo la transición que más se mira en una demo. Los estados
 *    "en vuelo" pasan a ámbar (T.yellow), que contrasta en los 6 temas.
 *  - Faltaban ROLLING_BACK, TERMINATING y TEMPLATE, que el backend sí emite
 *    (ver SliceManager); caían al estilo de BORRADOR mostrando el texto crudo.
 *
 * `pulse: true` marca los estados transitorios: el canvas los anima.
 */
export const getStatusVisual = (status) => ({
    ACTIVE:           { fg: T.accent,    bg: T.accentLight,     label: "ACTIVO"        },
    DRAFT:            { fg: T.textMuted, bg: T.surfaceElevated, label: "BORRADOR"      },
    TEMPLATE:         { fg: T.accentMid, bg: T.accentLight,     label: "PLANTILLA"     },
    PROVISIONING:     { fg: T.yellow,    bg: T.yellowLight,     label: "PROVISIONANDO", pulse: true },
    PENDING_APPROVAL: { fg: T.yellow,    bg: T.yellowLight,     label: "PENDIENTE"     },
    REJECTED:         { fg: T.yellow,    bg: T.yellowLight,     label: "RECHAZADO"     },
    ROLLING_BACK:     { fg: T.red,       bg: T.redLight,        label: "REVIRTIENDO",   pulse: true },
    TERMINATING:      { fg: T.red,       bg: T.redLight,        label: "TERMINANDO",    pulse: true },
    FAILED:           { fg: T.red,       bg: T.redLight,        label: "FALLIDO"       },
    TERMINATED:       { fg: T.textFaint, bg: T.surfaceElevated, label: "TERMINADO"     },
}[status] ?? { fg: T.textMuted, bg: T.surfaceElevated, label: status || "—" });

// ─── Shared style helpers — always read live T values ─────────────────────────
export const btnBase = (extra = {}) => ({
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
    padding: "7px 14px", borderRadius: 8, border: `1px solid ${T.border}`,
    cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: "inherit",
    background: T.surface, color: T.text,
    // Sin `transition` aquí: animaba `opacity`, que ningún handler cambiaba
    // nunca. Las transiciones de botón viven ahora en getGlobalCss.
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
export const getGlobalCss = () => {
// Dirección del realce al pasar el ratón: en temas oscuros hay que ACLARAR y en
// claros OSCURECER. Se resuelve por tema porque este CSS se regenera al cambiarlo.
const isDark = THEMES.find(t => t.id === currentThemeId)?.dark ?? false;
const hoverBright  = isDark ? 1.22 : 0.945;
const activeBright = isDark ? 1.08 : 0.90;
return `
    * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
        font-family: ${FONT_STACK};
    }

    body {
        background-color: ${T.bg};
        color: ${T.text};
        font-family: ${FONT_STACK};
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

    /* ── Hover / feedback de interacción ─────────────────────────────────────
       Toda la UI se estiliza con style={{}} inline y un estilo inline NO puede
       expresar :hover — por eso solo 15 de los 144 botones tenían realce, con
       onMouseEnter escrito a mano. Al vivir aquí, estas reglas los cubren todos
       sin tocar el JSX.

       Se usa filter:brightness() y no un background fijo porque los botones
       tienen fondos muy distintos (accent, surface, redLight, transparente…):
       el filtro se adapta a cada uno en vez de aplastarlos con un color único.

       Y se eligen filter y transform, que NO se escriben inline en ningún
       componente, para que la regla gane sin recurrir a !important.           */
    button, .slice-card {
        transition: filter 0.15s ease, transform 0.15s ease,
                    border-color 0.15s ease, box-shadow 0.15s ease;
    }
    button:not(:disabled):hover { filter: brightness(${hoverBright}); }
    button:not(:disabled):active {
        filter: brightness(${activeBright});
        transform: translateY(1px);
    }
    button:disabled { cursor: not-allowed; }

    /* Tarjeta de slice de la barra lateral: es lo más clicado de la app y no
       tenía ningún realce (arrastraba un transition:all sin handler). */
    .slice-card:hover {
        filter: brightness(${hoverBright});
        transform: translateX(3px);
    }

    /* Filas de tabla — bitácora, usuarios, proyectos, logs. Se tiñe el <td> y
       no el <tr> porque el tr sí lleva background inline (las filas de error
       van en rojo) y así el realce se superpone sin borrarlo. */
    tbody tr td { transition: background-color 0.12s ease; }
    tbody tr:hover td { background: ${T.accent}14; }

    /* Campos de formulario: indicar que son editables antes de hacer clic. */
    input:not(:disabled):hover, select:not(:disabled):hover, textarea:not(:disabled):hover {
        border-color: ${T.borderHover};
    }

    /* ── Foco de teclado ──────────────────────────────────────────────────────
       Casi toda la UI se estiliza con style={{}} inline, y un estilo inline NO
       puede expresar una pseudo-clase: por eso no había ni un solo indicador de
       foco en la app y navegar con Tab era invisible. Al vivir aquí, esta regla
       cubre de golpe los ~889 elementos con estilo inline.
       :focus-visible (y no :focus) para que el anillo salga al tabular pero no
       al hacer clic con el ratón.                                            */
    :focus-visible {
        outline: 2px solid ${T.accent};
        outline-offset: 2px;
        border-radius: 4px;
    }

    /* ── Animaciones de estado ───────────────────────────────────────────────
       statusPulse: late en los estados transitorios (PROVISIONING, TERMINATING,
       ROLLING_BACK) — ver getStatusVisual().
       edgeFlow: desplaza el guion de los enlaces del canvas para dar sensación
       de tráfico mientras el slice se aprovisiona.                           */
    @keyframes statusPulse {
        0%, 100% { opacity: 1;   transform: scale(1);   }
        50%      { opacity: 0.35; transform: scale(0.8); }
    }
    @keyframes edgeFlow {
        to { stroke-dashoffset: -24; }
    }

    /* ── Layout adaptable ────────────────────────────────────────────────────
       La app se diseñó para escritorio ancho. En un proyector a 1100px o menos
       la barra superior desborda, así que pasa a desplazarse en horizontal en
       lugar de romper el layout.

       OJO: el ancho de la sidebar NO se toca aquí. Ahora es redimensionable por
       el usuario (Sidebar.jsx escribe un width inline) y una regla con
       !important le ganaría siempre al arrastre. El ajuste por tamaño de
       pantalla se calcula en JS, en defaultSidebarWidth().                   */
    @media (max-width: 1100px) {
        .app-topbar { overflow-x: auto; overflow-y: hidden; }
    }

    /* ── Accesibilidad: movimiento reducido ─────────────────────────────────
       Respeta la preferencia del sistema para quien tiene sensibilidad al
       movimiento (y de paso evita animaciones en una demo proyectada).      */
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
        }
    }
`;
};

// Alias sin uso actual: ningún componente lo importa (LoginPage y ProfilePage,
// que el comentario original citaba, nunca lo usaron). Se conserva por si algo
// externo lo referencia.
export const globalCss = `
    * {
        box-sizing: border-box;
        font-family: ${FONT_STACK};
    }
    @keyframes fadeIn { from { opacity:0; transform:translateY(15px); } to { opacity:1; transform:translateY(0); } }
    @keyframes shake  { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-5px)} 40%,80%{transform:translateX(5px)} }
    @keyframes spin   { to { transform:rotate(360deg); } }
    input:focus, select:focus { outline: none; }
`;
