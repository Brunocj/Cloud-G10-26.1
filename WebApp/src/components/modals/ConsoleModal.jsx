import { T, btnBase } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { Terminal, X } from "../ui/Icon";
import VmConsole from "../../VmConsole";

export const ConsoleModal = ({ workerIp, workerPort, vncPort, onClose }) => (
    <Overlay>
        <div style={{
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 16, padding: "20px", boxShadow: T.shadowMd,
            display: "flex", flexDirection: "column", gap: "10px",
        }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, display: "flex", alignItems: "center", gap: 8 }}>
                    <Terminal size={18} color={T.accent} /> Consola Interactiva VNC
                </div>
                <button onClick={onClose} style={btnBase({ background: T.redLight, color: T.red, padding: "5px 10px",
                    display: "flex", alignItems: "center", gap: 5 })}>
                    <X size={13} /> Cerrar
                </button>
            </div>
            <VmConsole workerIp={workerIp} workerPort={workerPort} vncPort={vncPort} />
        </div>
    </Overlay>
);
