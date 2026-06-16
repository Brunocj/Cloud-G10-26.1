import { useState, useCallback, useEffect, useRef } from "react";

// ─── Config (desde variables de entorno Vite) ─────────────────────────────────
const KEYCLOAK_URL    = import.meta.env.VITE_KEYCLOAK_URL       ?? "http://10.20.11.212:8086";
const REALM           = import.meta.env.VITE_KEYCLOAK_REALM     ?? "pucp-cloud";
const CLIENT_ID       = import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "pucp-cloud-webapp";
const DEMO_MODE       = import.meta.env.VITE_DEMO_MODE === "true";

const TOKEN_URL = `${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token`;

const TOKEN_KEY   = "pucp_cloud_token";
const REFRESH_KEY = "pucp_cloud_refresh";
const USER_KEY    = "pucp_cloud_user";

// Margen de seguridad: refrescar el token 60 s antes de que expire
const REFRESH_MARGIN_S = 60;

// ─── Helpers de almacenamiento ────────────────────────────────────────────────

const readStoredUser = () => {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
};

const persist = (accessToken, refreshToken, user) => {
    localStorage.setItem(TOKEN_KEY,   accessToken);
    localStorage.setItem(REFRESH_KEY, refreshToken ?? "");
    localStorage.setItem(USER_KEY,    JSON.stringify(user));
};

const clear = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
};

// ─── JWT helpers ──────────────────────────────────────────────────────────────

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

/**
 * Devuelve true si el token es nulo, no parseable, o ya expiró
 * (con el margen de seguridad incluido).
 */
const isTokenExpired = (token) => {
    if (!token || token.startsWith("demo-token-")) return false; // demo tokens no expiran
    const { exp } = decodeJwtPayload(token);
    if (!exp) return true;
    return Date.now() / 1000 >= exp - REFRESH_MARGIN_S;
};

// Extrae el rol de mayor prioridad del JWT
const ROLE_PRIORITY = ["usuario", "jefeProyecto", "admin", "superAdmin"];
const extractTopRole = (roles = []) => {
    const known = roles.filter(r => ROLE_PRIORITY.includes(r));
    if (!known.length) return "usuario";
    known.sort((a, b) => ROLE_PRIORITY.indexOf(b) - ROLE_PRIORITY.indexOf(a));
    return known[0];
};

const buildUser = (payload, fallbackEmail) => ({
    id:       payload.sub,
    name:     payload.name ?? payload.preferred_username ?? fallbackEmail,
    email:    payload.email ?? fallbackEmail,
    username: payload.preferred_username ?? fallbackEmail,
    role:     extractTopRole(payload?.realm_access?.roles ?? []),
    allRoles: (payload?.realm_access?.roles ?? []).filter(r => ROLE_PRIORITY.includes(r)),
});

// ─── Demo mode (sin Keycloak) ─────────────────────────────────────────────────

const DEMO_CREDENTIALS = { email: "demo@pucp.edu.pe", password: "pucp2026" };
const DEMO_USER = { name: "Demo PUCP", email: "demo@pucp.edu.pe", role: "usuario" };

const demoLogin = ({ email, password }) => {
    if (
        email.trim().toLowerCase() === DEMO_CREDENTIALS.email &&
        password === DEMO_CREDENTIALS.password
    ) {
        const token = `demo-token-${Date.now()}`;
        persist(token, "", DEMO_USER);
        return { success: true, token, refreshToken: "", user: DEMO_USER };
    }
    return { success: false, reason: "invalid" };
};

// ─── Keycloak — login con ROPC ────────────────────────────────────────────────

const keycloakLogin = async ({ email, password }) => {
    try {
        const res = await fetch(TOKEN_URL, {
            method:  "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
                grant_type: "password",
                client_id:  CLIENT_ID,
                username:   email.trim(),  // Keycloak acepta email o username
                password:   password,
            }).toString(),
        });

        if (res.status === 401) return { success: false, reason: "invalid" };
        if (!res.ok)           return { success: false, reason: "network" };

        const data         = await res.json();
        const accessToken  = data.access_token;
        const refreshToken = data.refresh_token ?? "";

        const payload = decodeJwtPayload(accessToken);
        const user    = buildUser(payload, email);

        persist(accessToken, refreshToken, user);
        return { success: true, token: accessToken, refreshToken, user };

    } catch (err) {
        console.error("keycloakLogin error:", err);
        return { success: false, reason: "network" };
    }
};

// ─── Keycloak — refresco silencioso de token ──────────────────────────────────

/**
 * Intenta obtener un nuevo access_token usando el refresh_token.
 * Devuelve { success, token, refreshToken } o { success: false }.
 */
const keycloakRefresh = async (currentRefreshToken) => {
    if (!currentRefreshToken) return { success: false };
    try {
        const res = await fetch(TOKEN_URL, {
            method:  "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
                grant_type:    "refresh_token",
                client_id:     CLIENT_ID,
                refresh_token: currentRefreshToken,
            }).toString(),
        });

        if (!res.ok) return { success: false };

        const data         = await res.json();
        const accessToken  = data.access_token;
        const refreshToken = data.refresh_token ?? currentRefreshToken;

        const payload = decodeJwtPayload(accessToken);
        const user    = buildUser(payload, "");

        persist(accessToken, refreshToken, user);
        return { success: true, token: accessToken, refreshToken, user };

    } catch (err) {
        console.error("keycloakRefresh error:", err);
        return { success: false };
    }
};

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useAuth — gestiona el estado de autenticación con Keycloak.
 *
 * Correcciones respecto a la versión anterior:
 *  1. Valida exp del token al restaurar desde localStorage — evita el bug
 *     de "usuario ve el dashboard pero es expulsado en segundos".
 *  2. Persiste y refresca el refresh_token automáticamente antes de que
 *     expire el access_token (timer basado en `exp`).
 *  3. Expone refreshToken() para que createApiFetch pueda reintentar
 *     peticiones que reciban 401 por token expirado en mitad de sesión.
 *
 * Expone:
 *   token            → JWT access_token vigente
 *   user             → { id, name, email, role, allRoles }
 *   isAuthenticated
 *   login(credentials)    → { success, reason? }
 *   logout()
 *   refreshToken()        → Promise<string|null>  (nuevo access_token o null)
 *   isDemoMode
 */
export const useAuth = () => {
    // ── Inicialización: restaurar sesión previa solo si el token es válido ────
    const [token, setToken] = useState(() => {
        const stored = localStorage.getItem(TOKEN_KEY);
        if (!stored) return null;
        // Si el token expiró (o está a punto de expirar) no lo usamos.
        // La lógica de refresco del useEffect se encargará después.
        if (isTokenExpired(stored)) return null;
        return stored;
    });

    const [user,         setUser]         = useState(readStoredUser);
    const [refreshTokenV, setRefreshTokenV] = useState(() => localStorage.getItem(REFRESH_KEY) ?? "");

    // Ref para evitar refreshes simultáneos
    const refreshingRef = useRef(false);
    const timerRef      = useRef(null);

    // ── logout ────────────────────────────────────────────────────────────────
    const logout = useCallback(() => {
        if (timerRef.current) clearTimeout(timerRef.current);
        clear();
        setToken(null);
        setUser(null);
        setRefreshTokenV("");
    }, []);

    // ── Refresco silencioso ───────────────────────────────────────────────────
    /**
     * Llama al endpoint de refresh de Keycloak.
     * Actualiza el estado si tiene éxito, hace logout si el refresh_token
     * también expiró (sesión completamente muerta).
     * @returns {Promise<string|null>} el nuevo access_token o null
     */
    const doRefresh = useCallback(async () => {
        if (DEMO_MODE) return token; // demo tokens no se refrescan
        if (refreshingRef.current) return null;
        refreshingRef.current = true;
        try {
            const rt = localStorage.getItem(REFRESH_KEY) ?? "";
            const result = await keycloakRefresh(rt);
            if (result.success) {
                setToken(result.token);
                setUser(result.user);
                setRefreshTokenV(result.refreshToken);
                return result.token;
            } else {
                // Refresh token expirado — sesión muerta → logout
                logout();
                return null;
            }
        } finally {
            refreshingRef.current = false;
        }
    }, [logout, token]);

    // ── Timer proactivo de refresco ───────────────────────────────────────────
    // Se programa cada vez que cambia el token: dispara el refresco
    // REFRESH_MARGIN_S segundos antes de que expire el access_token.
    useEffect(() => {
        if (timerRef.current) clearTimeout(timerRef.current);
        if (!token || token.startsWith("demo-token-") || DEMO_MODE) return;

        const { exp } = decodeJwtPayload(token);
        if (!exp) return;

        const msUntilRefresh = (exp - REFRESH_MARGIN_S) * 1000 - Date.now();
        if (msUntilRefresh <= 0) {
            // Ya expiró o está a punto → refrescar inmediatamente
            doRefresh();
            return;
        }

        timerRef.current = setTimeout(doRefresh, msUntilRefresh);
        return () => clearTimeout(timerRef.current);
    }, [token, doRefresh]);

    // ── Caso borde: al montar, si el token cargado era expirado ──────────────
    // El useState ya lo descartó (devuelve null); pero si hay refresh_token
    // guardado, intentamos recuperar la sesión silenciosamente.
    useEffect(() => {
        if (token || DEMO_MODE) return;
        const rt = localStorage.getItem(REFRESH_KEY);
        if (!rt) return;
        doRefresh(); // intento silencioso al arrancar
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []); // solo al montar

    // ── login ─────────────────────────────────────────────────────────────────
    const login = useCallback(async (credentials) => {
        const result = DEMO_MODE
            ? demoLogin(credentials)
            : await keycloakLogin(credentials);

        if (result.success) {
            setToken(result.token);
            setUser(result.user);
            setRefreshTokenV(result.refreshToken ?? "");
        }

        return result;
    }, []);

    return {
        token,
        user,
        isAuthenticated: !!token,
        login,
        logout,
        /** Útil para que createApiFetch reintente tras un 401 */
        refreshToken: doRefresh,
        isDemoMode: DEMO_MODE,
    };
};
