/**
 * useNotifications — conexión WebSocket persistente al hub de notificaciones.
 *
 *   ws://<gateway>/notifications/ws?token=<JWT>
 *
 * El gateway valida el token y tuneliza hacia el SliceManager, que empuja
 * eventos JSON: { type, title, message, slice_id, ... }
 *
 * - Reconecta con backoff (2s → 30s máx) si se corta.
 * - Envía un ping de texto cada 30s para mantener viva la conexión.
 * - `onEvent` se invoca con cada evento (para toasts / refetch / contadores).
 */
import { useEffect, useRef } from "react";
import { API_BASE } from "../utils/api";

// http://host:8085/api/v1 → ws://host:8085
const WS_BASE = API_BASE.replace(/^http/, "ws").replace(/\/api\/v1\/?$/, "");

export const useNotifications = (token, onEvent) => {
    const onEventRef = useRef(onEvent);
    onEventRef.current = onEvent;

    useEffect(() => {
        if (!token) return;

        let ws        = null;
        let pingTimer = null;
        let retryMs   = 2000;
        let closed    = false;

        const connect = () => {
            if (closed) return;
            try {
                ws = new WebSocket(`${WS_BASE}/notifications/ws?token=${encodeURIComponent(token)}`);
            } catch {
                scheduleReconnect();
                return;
            }

            ws.onopen = () => {
                retryMs = 2000;
                pingTimer = setInterval(() => {
                    if (ws?.readyState === WebSocket.OPEN) ws.send("ping");
                }, 30000);
            };

            ws.onmessage = (e) => {
                try {
                    const event = JSON.parse(e.data);
                    onEventRef.current?.(event);
                } catch { /* ping/pong u otro texto no-JSON */ }
            };

            ws.onclose = () => {
                clearInterval(pingTimer);
                scheduleReconnect();
            };
            ws.onerror = () => ws?.close();
        };

        const scheduleReconnect = () => {
            if (closed) return;
            setTimeout(connect, retryMs);
            retryMs = Math.min(retryMs * 2, 30000);
        };

        connect();

        return () => {
            closed = true;
            clearInterval(pingTimer);
            try { ws?.close(); } catch { /* noop */ }
        };
    }, [token]);
};
