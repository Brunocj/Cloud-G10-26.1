import React, { useRef, useEffect, useState } from "react";
import { VncScreen } from "react-vnc";

// Extraemos solo el host:puerto de la URL base del API Gateway (sin ws:// ni path)
// VITE_API_BASE = "http://10.20.11.212:8085/api/v1" → API_GW = "10.20.11.212:8085"
const _apiBase = import.meta.env.VITE_API_BASE ?? "http://10.20.11.212:8085/api/v1";
const API_GW   = _apiBase.replace(/^https?:\/\//, "").replace(/\/.*$/, "");

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

// ─── Helpers compartidos: teclas especiales para el toolbar de la consola ─────
const SPECIAL_KEYS = [
    { label: "/",  title: "Slash",      ...KEYSYMS.slash },
    { label: "\\", title: "Backslash",  ...KEYSYMS.backslash },
    { label: "|",  title: "Pipe",       ...KEYSYMS.pipe },
    { label: "_",  title: "Underscore", ...KEYSYMS.underscore },
    { label: "-",  title: "Dash",       ...KEYSYMS.dash },
    { label: "~",  title: "Tilde",      ...KEYSYMS.tilde },
    { label: "@",  title: "At",         ...KEYSYMS.at },
];

const SPECIAL_KEY_BTN_STYLE = {
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

const SpecialKeysToolbar = ({ vncRef }) => {
    const sendKey = (sym, code) => {
        if (!vncRef.current) return;
        vncRef.current.sendKey(sym, code, true);   // keydown
        vncRef.current.sendKey(sym, code, false);  // keyup
    };
    return (
        <div style={{ padding: "5px 10px", backgroundColor: "#12122a", borderBottom: "1px solid #333", display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, color: "#666", marginRight: 4, fontFamily: "monospace" }}>TECLAS:</span>
            {SPECIAL_KEYS.map(({ label, title, sym, code }) => (
                <button
                    key={title}
                    title={`Enviar ${title}`}
                    onClick={() => sendKey(sym, code)}
                    style={SPECIAL_KEY_BTN_STYLE}
                    onMouseEnter={e => e.target.style.backgroundColor = "#3a3a6a"}
                    onMouseLeave={e => e.target.style.backgroundColor = "#2a2a4a"}
                >
                    {label}
                </button>
            ))}
            <span style={{ fontSize: 10, color: "#555", marginLeft: 4 }}>— clic para insertar en la consola</span>
        </div>
    );
};

// ─── Consola NoVNC (Linux Cluster) ────────────────────────────────────────────
const LinuxClusterConsole = ({ token, workerIp, workerPort, vncPort }) => {
    const vncRef = useRef(null);
    const containerRef = useRef(null);

    // display = vncPort - 5900  (ej: 5944 - 5900 = 44)
    // QEMU abre WebSocket en puerto 5700 + display  (ej: 5744)
    // El ApiGW tuneliza via SSH:  ws://localhost:8085/vnc/{gatewayIp}/{sshPort}/{wsPort}
    const display = vncPort - 5900;
    const wsPort  = 5700 + display;
    const sshPort = workerPort || 22;   // puerto SSH en el gateway (5811-5814)
    const wsUrl   = `ws://${API_GW}/vnc/${workerIp}/${sshPort}/${wsPort}${token ? `?token=${token}` : ""}`;

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        let canvas = container.querySelector("canvas");

        const setupCanvas = (targetCanvas) => {
            if (!targetCanvas) return;
            if (!targetCanvas.getAttribute("tabindex")) {
                targetCanvas.setAttribute("tabindex", "0");
            }
            targetCanvas.style.outline = "none";
        };

        const observer = new MutationObserver(() => {
            const foundCanvas = container.querySelector("canvas");
            if (foundCanvas && foundCanvas !== canvas) {
                canvas = foundCanvas;
                setupCanvas(canvas);
            }
        });

        observer.observe(container, { childList: true, subtree: true });

        if (canvas) {
            setupCanvas(canvas);
        }

        const handleMouseDown = () => {
            const activeCanvas = container.querySelector("canvas");
            if (activeCanvas) activeCanvas.focus();
        };

        container.addEventListener("mousedown", handleMouseDown);

        setTimeout(() => {
            const activeCanvas = container.querySelector("canvas");
            if (activeCanvas) activeCanvas.focus();
        }, 100);

        return () => {
            observer.disconnect();
            container.removeEventListener("mousedown", handleMouseDown);
        };
    }, [wsUrl]);

    return (
        <div style={{ width: "800px", backgroundColor: "#000", borderRadius: "8px", overflow: "hidden" }}>
            {/* Toolbar row 1: title + Ctrl+Alt+Del */}
            <div style={{ padding: "8px 10px 6px", backgroundColor: "#1a1a2e", color: "#e0e0e0", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #333" }}>
                <div>
                    <span style={{ fontWeight: 700, marginRight: 8 }}>Consola VNC</span>
                    <span style={{ fontSize: 11, color: "#888", fontFamily: "monospace" }}>{workerIp}:{sshPort} → WS :{wsPort}</span>
                </div>
                <button
                    onClick={() => vncRef.current && vncRef.current.sendCtrlAltDel()}
                    style={{ backgroundColor: "#c0392b", color: "#fff", border: "none", borderRadius: "4px", padding: "5px 12px", cursor: "pointer", fontSize: 12, fontWeight: 600 }}
                >
                    Ctrl+Alt+Del
                </button>
            </div>

            <SpecialKeysToolbar vncRef={vncRef} />

            <div
                ref={containerRef}
                style={{ width: "100%", height: "556px" }}
            >
                <VncScreen
                    url={wsUrl}
                    scaleViewport={true}
                    background="#000000"
                    style={{ width: "100%", height: "100%" }}
                    ref={vncRef}
                    focusOnClick={true}
                    onConnect={() => console.log("[VNC] Conectado:", wsUrl)}
                    onDisconnect={(e) => console.warn("[VNC] Desconectado:", e)}
                />
            </div>
        </div>
    );
};

// ─── Consola OpenStack NoVNC (WebSocket via ApiGW → SSH → nova-novncproxy) ────
const OpenStackConsole = ({ jwtToken, vncUrl, sliceId, vmId, apiFetch }) => {
    // Los tokens de nova-novncproxy expiran/se consumen rápido (~10 min, un solo uso).
    // Si tenemos sliceId+vmId+apiFetch pedimos uno fresco; si no, usamos el guardado
    // (compatibilidad hacia atrás).
    const [freshToken, setFreshToken] = useState(null);
    const [status, setStatus] = useState(sliceId && vmId && apiFetch ? "loading" : "ready");
    const vncRef = useRef(null);
    const containerRef = useRef(null);

    useEffect(() => {
        if (!(sliceId && vmId && apiFetch)) return;
        setStatus("loading");
        apiFetch(`/slices/${sliceId}/vms/${vmId}/console`)
            .then(r => r.ok ? r.json() : Promise.reject(r))
            .then(data => { setFreshToken(data.vnc_url); setStatus("ready"); })
            .catch(() => { setFreshToken(null); setStatus("error"); });
    }, [sliceId, vmId, apiFetch]);

    const token = freshToken || vncUrl;
    const wsUrl = `ws://${API_GW}/vnc/openstack/${token}${jwtToken ? `?token=${jwtToken}` : ""}`;

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        let canvas = container.querySelector("canvas");

        const setupCanvas = (targetCanvas) => {
            if (!targetCanvas) return;
            if (!targetCanvas.getAttribute("tabindex")) {
                targetCanvas.setAttribute("tabindex", "0");
            }
            targetCanvas.style.outline = "none";
        };

        const observer = new MutationObserver(() => {
            const foundCanvas = container.querySelector("canvas");
            if (foundCanvas && foundCanvas !== canvas) {
                canvas = foundCanvas;
                setupCanvas(canvas);
            }
        });

        observer.observe(container, { childList: true, subtree: true });

        if (canvas) {
            setupCanvas(canvas);
        }

        const handleMouseDown = () => {
            const activeCanvas = container.querySelector("canvas");
            if (activeCanvas) activeCanvas.focus();
        };

        container.addEventListener("mousedown", handleMouseDown);

        setTimeout(() => {
            const activeCanvas = container.querySelector("canvas");
            if (activeCanvas) activeCanvas.focus();
        }, 100);

        return () => {
            observer.disconnect();
            container.removeEventListener("mousedown", handleMouseDown);
        };
    }, [wsUrl]);

    if (status === "loading") {
        return <div style={{ width: "800px", height: "556px", backgroundColor: "#000", borderRadius: "8px",
            display: "flex", alignItems: "center", justifyContent: "center", color: "#888" }}>
            Solicitando consola…
        </div>;
    }
    if (status === "error" || !token) {
        return <div style={{ width: "800px", height: "556px", backgroundColor: "#000", borderRadius: "8px",
            display: "flex", alignItems: "center", justifyContent: "center", color: "#e74c3c" }}>
            No se pudo obtener la consola de esta VM.
        </div>;
    }

    return (
        <div style={{ width: "800px", backgroundColor: "#000", borderRadius: "8px", overflow: "hidden" }}>
            <div style={{
                padding: "8px 10px 6px", backgroundColor: "#1a0a2e", color: "#e0e0e0",
                display: "flex", justifyContent: "space-between", alignItems: "center",
                borderBottom: "1px solid #333",
            }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{
                        fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 20,
                        background: "#ff820022", color: "#ff8200", border: "1px solid #ff820044",
                    }}>
                        ☁ OpenStack NoVNC
                    </span>
                    <span style={{ fontSize: 11, color: "#555", fontFamily: "monospace" }}>
                        token: {token?.slice(0, 8)}…
                    </span>
                </div>
                <button
                    onClick={() => vncRef.current && vncRef.current.sendCtrlAltDel()}
                    style={{ backgroundColor: "#c0392b", color: "#fff", border: "none", borderRadius: "4px", padding: "5px 12px", cursor: "pointer", fontSize: 12, fontWeight: 600 }}
                >
                    Ctrl+Alt+Del
                </button>
            </div>

            <SpecialKeysToolbar vncRef={vncRef} />

            <div
                ref={containerRef}
                style={{ width: "100%", height: "556px" }}
            >
                <VncScreen
                    url={wsUrl}
                    scaleViewport={true}
                    background="#000000"
                    style={{ width: "100%", height: "100%" }}
                    ref={vncRef}
                    focusOnClick={true}
                    onConnect={() => console.log("[VNC-OS] Conectado:", wsUrl)}
                    onDisconnect={(e) => console.warn("[VNC-OS] Desconectado:", e)}
                />
            </div>
        </div>
    );
};

// ─── Componente público VmConsole ─────────────────────────────────────────────
/**
 * VmConsole — renderizado condicional según el tipo de hipervisor:
 *
 *   · Si `vm.vnc_url` tiene valor → VM de OpenStack → renderiza <iframe> con NoVNC web.
 *   · Si `vm.vnc_port` tiene valor (y no hay vnc_url) → VM de Linux Cluster → usa noVNC/WebSockify.
 *
 * Props heredadas para compatibilidad con ConsoleModal:
 *   workerIp, workerPort, vncPort  → Linux Cluster
 *   vm                            → objeto completo (para detectar vnc_url)
 */
const VmConsole = ({ token, workerIp, workerPort, vncPort, vm }) => {
    // Prioridad: vnc_url de OpenStack > vncPort de Linux Cluster
    if (vm?.vnc_url) {
        return <OpenStackConsole jwtToken={token} vncUrl={vm.vnc_url} sliceId={vm.sliceId} vmId={vm.vmId} apiFetch={vm.apiFetch} />;
    }

    // Fallback Linux Cluster
    return <LinuxClusterConsole token={token} workerIp={workerIp} workerPort={workerPort} vncPort={vncPort} />;
};

export default VmConsole;
