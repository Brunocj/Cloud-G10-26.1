import { T, btnBase } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { Terminal, X } from "../ui/Icon";
import VmConsole from "../../VmConsole";

/**
 * ConsoleModal — envuelve VmConsole en un overlay.
 *
 * Props:
 *   vm         → objeto VM completo (para detectar vm.vnc_url de OpenStack)
 *   workerIp   → IP del gateway (Linux Cluster)
 *   workerPort → puerto SSH gateway (Linux Cluster)
 *   vncPort    → puerto VNC (Linux Cluster)
 *   onClose    → callback de cierre
 */
export const ConsoleModal = ({ vm, workerIp, workerPort, vncPort, onClose }) => (
    <Overlay>
        <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 16, padding: "20px", boxShadow: T.shadowMd,
            display: "flex", flexDirection: "column", gap: "10px",
        }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, display: "flex", alignItems: "center", gap: 8 }}>
                    <Terminal size={18} color={T.accent} />
                    {vm?.vnc_url ? "Consola OpenStack NoVNC" : "Consola Interactiva VNC"}
                </div>
                <button onClick={onClose} style={btnBase({ background: T.redLight, color: T.red, padding: "5px 10px",
                    display: "flex", alignItems: "center", gap: 5 })}>
                    <X size={13} /> Cerrar
                </button>
            </div>
            {/* VmConsole detecta automáticamente el tipo: OpenStack (iframe) o Linux Cluster (noVNC) */}
            <VmConsole vm={vm} workerIp={workerIp} workerPort={workerPort} vncPort={vncPort} />
        </div>
    </Overlay>
);
