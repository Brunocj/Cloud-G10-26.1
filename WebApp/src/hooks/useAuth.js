import { useState, useCallback } from "react";

// ─── Config (desde variables de entorno Vite) ─────────────────────────────────
const KEYCLOAK_URL    = import.meta.env.VITE_KEYCLOAK_URL       ?? "http://10.20.11.212:8086";
const REALM           = import.meta.env.VITE_KEYCLOAK_REALM     ?? "pucp-cloud";
const CLIENT_ID       = import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "pucp-cloud-webapp";
const DEMO_MODE       = import.meta.env.VITE_DEMO_MODE === "true";

const TOKEN_URL = `${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token`;

const TOKEN_KEY = "pucp_cloud_token";
const USER_KEY  = "pucp_cloud_user";

// ─── Helpers de almacenamiento ────────────────────────────────────────────────

const readStoredUser = () => {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
};

const persist = (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
};

const clear = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
};

// ─── JWT decoder (sin verificar firma — solo para extraer payload) ─────────────

const decodeJwtPayload = (token) => {
    try {
        const base64 = token.split(".")[1]
            .replace(/-/g, "+")
            .replace(/_/g, "/");
        return JSON.parse(atob(base64));
    } catch {
        return {};
    }
};

// Extrae el rol de mayor prioridad del JWT
const ROLE_PRIORITY = ["usuario", "jefeProyecto", "admin", "superAdmin"];
const extractTopRole = (roles = []) => {
    const known = roles.filter(r => ROLE_PRIORITY.includes(r));
    if (!known.length) return "usuario";
    known.sort((a, b) => ROLE_PRIORITY.indexOf(b) - ROLE_PRIORITY.indexOf(a));
    return known[0];
};

// ─── Demo mode (sin Keycloak) ─────────────────────────────────────────────────

const DEMO_CREDENTIALS = { email: "demo@pucp.edu.pe", password: "pucp2026" };
const DEMO_USER = { name: "Demo PUCP", email: "demo@pucp.edu.pe", role: "usuario" };

const demoLogin = ({ email, password }) => {
    if (
        email.trim().toLowerCase() === DEMO_CREDENTIALS.email &&
        password === DEMO_CREDENTIALS.password
    ) {
        const token = `demo-token-${Date.now()}`;
        persist(token, DEMO_USER);
        return { success: true, token, user: DEMO_USER };
    }
    return { success: false, reason: "invalid" };
};

// ─── Keycloak login (Resource Owner Password Credentials) ─────────────────────

const keycloakLogin = async ({ email, password }) => {
    try {
        const body = new URLSearchParams({
            grant_type: "password",
            client_id:  CLIENT_ID,
            username:   email.trim(),  // Keycloak acepta email o username
            password:   password,
        });

        const res = await fetch(TOKEN_URL, {
            method:  "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body:    body.toString(),
        });

        if (res.status === 401) return { success: false, reason: "invalid" };
        if (!res.ok)           return { success: false, reason: "network" };

        const data = await res.json();
        const accessToken = data.access_token;

        // Decodificar payload del JWT para obtener info del usuario
        const payload = decodeJwtPayload(accessToken);
        const roles   = payload?.realm_access?.roles ?? [];
        const topRole = extractTopRole(roles);

        const user = {
            id:       payload.sub,
            name:     payload.name ?? payload.preferred_username ?? email,
            email:    payload.email ?? email,
            username: payload.preferred_username ?? email,
            role:     topRole,
            // Guardar todos los roles para referencia
            allRoles: roles.filter(r => ROLE_PRIORITY.includes(r)),
        };

        persist(accessToken, user);
        return { success: true, token: accessToken, user };

    } catch (err) {
        console.error("keycloakLogin error:", err);
        return { success: false, reason: "network" };
    }
};

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useAuth — gestiona el estado de autenticación con Keycloak.
 *
 * Expone:
 *   token          → JWT access_token (para adjuntar en requests)
 *   user           → { id, name, email, role, allRoles }
 *   isAuthenticated
 *   login(credentials)  → { success, reason? }
 *   logout()
 *   isDemoMode
 */
export const useAuth = () => {
    const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
    const [user,  setUser]  = useState(readStoredUser);

    const login = useCallback(async (credentials) => {
        const result = DEMO_MODE
            ? demoLogin(credentials)
            : await keycloakLogin(credentials);

        if (result.success) {
            setToken(result.token);
            setUser(result.user);
        }

        return result;
    }, []);

    const logout = useCallback(() => {
        clear();
        setToken(null);
        setUser(null);
    }, []);

    return {
        token,
        user,
        isAuthenticated: !!token,
        login,
        logout,
        isDemoMode: DEMO_MODE,
    };
};
