import { useState, useCallback } from "react";

// ─── Config ──────────────────────────────────────────────────────────────────

const TOKEN_KEY = "pucp_cloud_token";
const USER_KEY  = "pucp_cloud_user";
const API_BASE  = "http://localhost:8085/api/v1";

/**
 * DEMO_MODE — set to true while Keycloak is not yet deployed.
 *
 * When true:
 *   - Skips the real API call.
 *   - Accepts  demo@pucp.edu.pe / pucp2026  as valid credentials.
 *   - Any other combo → "invalid".
 *
 * When Keycloak is ready, set to false (or use an env var).
 * The rest of the code does NOT change.
 */
const DEMO_MODE = true;

const DEMO_CREDENTIALS = {
    email:    "demo@pucp.edu.pe",
    password: "pucp2026",
};

const DEMO_USER = {
    name:  "Demo PUCP",
    email: "demo@pucp.edu.pe",
    role:  "Investigador",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

// ─── Demo login (no Keycloak needed) ─────────────────────────────────────────

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

// ─── Keycloak / real API login ────────────────────────────────────────────────
//
// Keycloak OIDC token endpoint (Resource Owner Password Credentials grant):
//   POST /realms/{realm}/protocol/openid-connect/token
//   Content-Type: application/x-www-form-urlencoded
//   Body: grant_type=password&client_id=...&username=...&password=...
//
// The ApiGW proxies this via identity.py → /api/v1/auth/**
// Future: replace with Authorization Code flow (PKCE) for production.
//
const keycloakLogin = async ({ email, password }) => {
    try {
        const res  = await fetch(`${API_BASE}/auth/login`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ email, password }),
        });

        const data = await res.json().catch(() => ({}));

        if (res.status === 403) return { success: false, reason: "pending" };
        if (!res.ok)            return { success: false, reason: "invalid" };

        const token = data.access_token ?? data.token ?? "";
        const user  = data.user ?? { name: data.name ?? email, email, role: data.role ?? "Investigador" };

        persist(token, user);
        return { success: true, token, user };

    } catch {
        return { success: false, reason: "network" };
    }
};

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useAuth — manages authentication state across the app.
 *
 * Switch to Keycloak:
 *   1. Set DEMO_MODE = false (or read from import.meta.env.VITE_DEMO_MODE)
 *   2. Register identity.py router in ApiGW/main.py
 *   3. Configure KEYCLOAK_URL in ApiGW docker-compose / .env
 *   4. (Optional) Migrate to PKCE Authorization Code flow for production
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
