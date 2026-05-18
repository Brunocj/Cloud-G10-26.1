import React, { useRef } from "react";
import { VncScreen } from "react-vnc";

const API_GW = "localhost:8085";

// X11 keysyms for common special characters
const KEYSYMS = {
    slash:      { sym: 0x002F, code: "Slash" },
    backslash:  { sym: 0x005C, code: "Backslash" },
    pipe:       { sym: 0x007C, code: "Backslash" },
    underscore: { sym: 0x005F, code: "Minus" },
    dash:       { sym: 0x002D, code: "Minus" },
    tilde:      { sym: 0x007E, code: "Backquote" },
    at:         { sym: 0x0040, code: "Digit2" },
};

const VmConsole = ({ workerIp, vncPort }) => {
    const vncRef = useRef(null);

    // display = vncPort - 5900  (ej: 5944 - 5900 = 44)
    // QEMU abre WebSocket en puerto 5700 + display  (ej: 5744)
    // El ApiGW proxea:  ws://localhost:8085/vnc/{workerIp}/{wsPort}  →  ws://10.0.10.x:{wsPort}
    const display = vncPort - 5900;
    const wsPort = 5700 + display;
    const wsUrl = "ws://" + API_GW + "/vnc/" + workerIp + "/" + wsPort;

    // Send a single key press (down + up) using X11 keysym
    const sendKey = (sym, code) => {
        if (!vncRef.current) return;
        vncRef.current.sendKey(sym, code, true);   // keydown
        vncRef.current.sendKey(sym, code, false);  // keyup
    };

    const specialKeys = [
        { label: "/",  title: "Slash",      ...KEYSYMS.slash },
        { label: "\\", title: "Backslash",  ...KEYSYMS.backslash },
        { label: "|",  title: "Pipe",       ...KEYSYMS.pipe },
        { label: "_",  title: "Underscore", ...KEYSYMS.underscore },
        { label: "-",  title: "Dash",       ...KEYSYMS.dash },
        { label: "~",  title: "Tilde",      ...KEYSYMS.tilde },
        { label: "@",  title: "At",         ...KEYSYMS.at },
    ];

    const btnStyle = {
        backgroundColor: "#2a2a4a",
        color: "#e0e0e0",
        border: "1px solid #444",
        borderRadius: "4px",
        padding: "3px 9px",
        cursor: "pointer",
        fontSize: 13,
        fontWeight: 700,
        fontFamily: "monospace",
        transition: "background 0.1s",
        minWidth: 30,
        lineHeight: 1.4,
    };

    return (
        <div style={{ width: "800px", backgroundColor: "#000", borderRadius: "8px", overflow: "hidden" }}>
            {/* Toolbar row 1: title + Ctrl+Alt+Del */}
            <div style={{ padding: "8px 10px 6px", backgroundColor: "#1a1a2e", color: "#e0e0e0", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #333" }}>
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

            {/* Toolbar row 2: special character keys */}
            <div style={{ padding: "5px 10px", backgroundColor: "#12122a", borderBottom: "1px solid #333", display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
                <span style={{ fontSize: 10, color: "#666", marginRight: 4, fontFamily: "monospace" }}>TECLAS:</span>
                {specialKeys.map(({ label, title, sym, code }) => (
                    <button
                        key={title}
                        title={`Enviar ${title}`}
                        onClick={() => sendKey(sym, code)}
                        style={btnStyle}
                        onMouseEnter={e => e.target.style.backgroundColor = "#3a3a6a"}
                        onMouseLeave={e => e.target.style.backgroundColor = "#2a2a4a"}
                    >
                        {label}
                    </button>
                ))}
                <span style={{ fontSize: 10, color: "#555", marginLeft: 4 }}>— clic para insertar en la consola</span>
            </div>

            <VncScreen
                url={wsUrl}
                scaleViewport={true}
                background="#000000"
                style={{ width: "100%", height: "556px" }}
                ref={vncRef}
                onConnect={() => console.log("[VNC] Conectado:", wsUrl)}
                onDisconnect={(e) => console.warn("[VNC] Desconectado:", e)}
            />
        </div>
    );
};

export default VmConsole;
