/**
 * api.js — Helper para fetch autenticado con el API Gateway.
 *
 * Uso en App.jsx:
 *   import { createApiFetch } from "./utils/api";
 *   const apiFetch = useMemo(
 *     () => createApiFetch(token, refreshToken, logout),
 *     [token, refreshToken, logout]
 *   );
 *
 *   const res = await apiFetch("/slices");
 *
 * Opciones adicionales:
 *  - `timeout` (segundos): abort automático via AbortController.
 *  - `onUploadProgress(pct, loaded, total)`: seguimiento de subidas via XHR.
 *  - `signal`: AbortSignal externo (cancel button, etc).
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://10.20.11.212:8085/api/v1";

const buildHeaders = (token, extraHeaders = {}, hasBody = false, bodyIsFormData = false) => {
    const headers = { ...extraHeaders };
    if (hasBody && !bodyIsFormData && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
};

/**
 * XHR interno usado cuando se pide onUploadProgress.
 * Devuelve Promise<Response> compatible con fetch.
 */
const _xhrFetch = (url, options, token, onUploadProgress, timeoutSecs, signal) =>
    new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        if (timeoutSecs) xhr.timeout = timeoutSecs * 1000;

        xhr.upload.addEventListener("progress", (e) => {
            if (e.lengthComputable)
                onUploadProgress(Math.round((e.loaded / e.total) * 100), e.loaded, e.total);
        });

        xhr.addEventListener("load", () => {
            const ct = xhr.getResponseHeader("Content-Type") || "application/json";
            resolve(new Response(xhr.responseText, { status: xhr.status, headers: { "Content-Type": ct } }));
        });

        xhr.addEventListener("error", () => reject(new Error("Error de red al subir el archivo")));
        xhr.addEventListener("timeout", () => reject(new Error("Timeout: la subida tardó demasiado tiempo")));
        xhr.addEventListener("abort", () => reject(new Error("Subida cancelada")));

        // Conectar AbortController externo con el XHR
        if (signal) signal.addEventListener("abort", () => xhr.abort());

        xhr.open(options.method || "POST", url);
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        // NO fijar Content-Type para FormData (el browser añade el boundary automáticamente)
        xhr.send(options.body);
    });

/**
 * Devuelve una función fetch que:
 *  1. Añade Authorization: Bearer <token> automáticamente.
 *  2. Si recibe 401, refresca el token y reintenta una sola vez.
 *  3. Acepta `timeout` (segundos) para abort automático.
 *  4. Acepta `onUploadProgress` para tracking de subidas via XHR.
 *  5. Acepta `signal` (AbortSignal) para cancelación externa.
 */
export const createApiFetch = (token, refreshToken, logout) => {
    return async (path, options = {}) => {
        const { timeout, onUploadProgress, signal: externalSignal, ...fetchOptions } = options;
        const bodyIsFormData = fetchOptions.body instanceof FormData;
        const hasBody = !!fetchOptions.body;
        const url = `${API_BASE}${path}`;

        // ── Rama XHR: cuando se pide seguimiento de progreso ──────────────
        if (typeof onUploadProgress === "function") {
            return _xhrFetch(url, fetchOptions, token, onUploadProgress, timeout, externalSignal);
        }

        // ── Rama fetch estándar con timeout opcional ───────────────────────
        let controller = null;
        let timeoutId = null;
        if (!externalSignal && timeout) {
            controller = new AbortController();
            timeoutId = setTimeout(() => controller.abort(), timeout * 1000);
        }
        const signal = externalSignal ?? controller?.signal;

        try {
            const res = await fetch(url, {
                ...fetchOptions,
                ...(signal ? { signal } : {}),
                headers: buildHeaders(token, fetchOptions.headers, hasBody, bodyIsFormData),
            });

            // ── Interceptor 401 ───────────────────────────────────────────
            if (res.status === 401 && typeof refreshToken === "function") {
                const newToken = await refreshToken();
                if (!newToken) return res;
                return fetch(url, {
                    ...fetchOptions,
                    headers: buildHeaders(newToken, fetchOptions.headers, hasBody, bodyIsFormData),
                });
            }
            return res;
        } finally {
            if (timeoutId) clearTimeout(timeoutId);
        }
    };
};

export { API_BASE };
