import { T } from "../../theme/tokens";

export const Overlay = ({ children, label = "Diálogo" }) => (
    // role/aria-modal: sin esto un lector de pantalla no anuncia que se abrió un
    // diálogo ni acota la lectura a su contenido — sigue leyendo la página de
    // detrás como si nada hubiera cambiado.
    <div role="dialog" aria-modal="true" aria-label={label} style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000,
    }}>
        {children}
    </div>
);
