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
 *   // Equivale a fetch(`${API_BASE}/slices`, { headers: { Authorization: ... } })
 *   const res = await apiFetch("/slices");
 *
 * Mejoras respecto a la versión anterior:
 *  - Interceptor de 401: intenta refrescar el token y reintentar la petición
 *    una sola vez. Si el refresco falla, llama a logout() automáticamente.
 *  - Evita que acciones CRUD (delete, deploy, etc.) fallen silenciosamente
 *    cuando el access_token expira en mitad de sesión.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://10.20.11.212:8085/api/v1";

/**
 * Construye los headers de la petición.
 * @param {string|null} token
 * @param {object}      extraHeaders
 * @param {boolean}     hasBody
 */
const buildHeaders = (token, extraHeaders = {}, hasBody = false, bodyIsFormData = false) => {
    const headers = { ...extraHeaders };

    if (hasBody && !bodyIsFormData && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }

    if (token) {
        headers["Authorization"] = `Bearer ${token}`;
    }

    return headers;
};

/**
 * Devuelve una función fetch que:
 *  1. Añade automáticamente Authorization: Bearer <token>
 *  2. Si recibe 401, intenta refrescar el token (refreshToken()) y reintenta.
 *  3. Si el refresco falla, llama a logout() y devuelve la respuesta 401.
 *
 * @param {string|null}   token         JWT access_token actual
 * @param {function}      refreshToken  () => Promise<string|null>  — de useAuth
 * @param {function}      logout        () => void                  — de useAuth
 */
export const createApiFetch = (token, refreshToken, logout) => {
    return async (path, options = {}) => {
        const bodyIsFormData = options.body instanceof FormData;
        const hasBody        = !!options.body;

        // ── Primera intento ───────────────────────────────────────────────────
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: buildHeaders(token, options.headers, hasBody, bodyIsFormData),
        });

        // ── Interceptor 401 ───────────────────────────────────────────────────
        // Solo actuamos si tenemos una función de refresco (modo Keycloak)
        if (res.status === 401 && typeof refreshToken === "function") {
            const newToken = await refreshToken();

            if (!newToken) {
                // El refresh_token también expiró → logout ya fue llamado
                // dentro de doRefresh. Devolvemos la respuesta 401 original.
                return res;
            }

            // Reintentar con el nuevo token (una sola vez)
            return fetch(`${API_BASE}${path}`, {
                ...options,
                headers: buildHeaders(newToken, options.headers, hasBody, bodyIsFormData),
            });
        }

        return res;
    };
};

export { API_BASE };
