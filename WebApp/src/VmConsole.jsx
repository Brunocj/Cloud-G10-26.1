import React, { useRef } from "react";
import { VncScreen } from "react-vnc";

const API_GW = "localhost:8085";

const VmConsole = ({ workerIp, vncPort }) => {
    const vncRef = useRef(null);

    // display = vncPort - 5900  (ej: 5944 - 5900 = 44)
    // QEMU abre WebSocket en puerto 5700 + display  (ej: 5744)
    // El ApiGW proxea:  ws://localhost:8085/vnc/{workerIp}/{wsPort}  →  ws://10.0.10.x:{wsPort}
    const display = vncPort - 5900;
    const wsPort = 5700 + display;
    const wsUrl = "ws://" + API_GW + "/vnc/" + workerIp + "/" + wsPort;

    return (
        <div style={{ width: "800px", height: "600px", backgroundColor: "#000", borderRadius: "8px", overflow: "hidden" }}>
            <div style={{ padding: "10px", backgroundColor: "#1a1a2e", color: "#e0e0e0", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #333" }}>
                <div>
                    <span style={{ fontWeight: 700, marginRight: 8 }}>💻 Consola VNC</span>
                    <span style={{ fontSize: 11, color: "#888", fontFamily: "monospace" }}>{workerIp} → WS :{wsPort}</span>
                </div>
                <button
                    onClick={() => vncRef.current && vncRef.current.sendCtrlAltDel()}
                    style={{ backgroundColor: "#c0392b", color: "#fff", border: "none", borderRadius: "4px", padding: "5px 12px", cursor: "pointer", fontSize: 12, fontWeight: 600 }}
                >
                    Ctrl+Alt+Del
                </button>
            </div>
            <VncScreen
                url={wsUrl}
                scaleViewport={true}
                background="#000000"
                style={{ width: "100%", height: "calc(100% - 44px)" }}
                ref={vncRef}
                onConnect={() => console.log("[VNC] Conectado:", wsUrl)}
                onDisconnect={(e) => console.warn("[VNC] Desconectado:", e)}
            />
        </div>
    );
};

export default VmConsole;
