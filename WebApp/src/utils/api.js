/**
 * api.js — Helper para fetch autenticado con el API Gateway.
 *
 * Uso en App.jsx:
 *   import { createApiFetch } from "./utils/api";
 *   const apiFetch = useMemo(() => createApiFetch(token), [token]);
 *
 *   // Equivale a fetch(`${API_BASE}/slices`, { headers: { Authorization: ... } })
 *   const res = await apiFetch("/slices");
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://10.20.11.212:8085/api/v1";

/**
 * Devuelve una función fetch que añade automáticamente:
 *  - Content-Type: application/json
 *  - Authorization: Bearer <token>  (si el token está disponible)
 *
 * @param {string|null} token  JWT access_token de Keycloak
 */
export const createApiFetch = (token) => {
    return (path, options = {}) => {
        const headers = {
            ...options.headers,
        };

        // Si el body no es FormData y no tiene Content-Type, ponemos application/json
        if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }

        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }

        return fetch(`${API_BASE}${path}`, {
            ...options,
            headers,
        });
    };
};

export { API_BASE };
