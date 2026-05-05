import React, { useRef } from 'react';
import { VncScreen } from 'react-vnc';

const VmConsole = ({ workerIp, vncPort }) => {
    const vncRef = useRef(null);

    // Matemáticas mágicas: Si VNC es 5911, Display es 11. 
    // El puerto WebSocket de QEMU será 5711 (5700 + Display).
    const display = vncPort - 5900;
    const wsPort = 5700 + display;

    // Armamos la URL para el navegador
    const wsUrl = `ws://${workerIp}:${wsPort}`;

    return (
        <div style={{ width: '800px', height: '600px', backgroundColor: '#000', borderRadius: '8px', overflow: 'hidden' }}>
            <div style={{ padding: '10px', backgroundColor: '#333', color: '#fff', display: 'flex', justifyContent: 'space-between' }}>
                <span>Consola VM ({workerIp}:{wsPort})</span>
                <button
                    onClick={() => vncRef.current?.sendCtrlAltDel()}
                    style={{ backgroundColor: '#e74c3c', color: '#fff', border: 'none', borderRadius: '4px', padding: '5px 10px', cursor: 'pointer' }}
                >
                    Ctrl+Alt+Del
                </button>
            </div>

            <VncScreen
                url={wsUrl}
                scaleViewport={true} // Escala la pantalla de la VM para que quepa en tu div
                background="#000000"
                style={{ width: '100%', height: 'calc(100% - 40px)' }}
                ref={vncRef}
            />
        </div>
    );
};

export default VmConsole;