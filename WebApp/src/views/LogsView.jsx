/**
 * LogsView — Visor de logs centralizados (Loki) para el superAdmin.
 *
 * Los logs se separan por `service` (nombre del microservicio) y por `instance`
 * (número de réplica), de modo que 2 instancias del mismo servicio se ven aparte.
 * La búsqueda usa el filtro de línea de LogQL (|=).
 *
 * Props: { user, onBack, onProfile, apiFetch }
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { T, btnBase, inp } from "../theme/tokens";
import { UserAvatar } from "../components/ui/UserAvatar";
import { ArrowLeft, Terminal, RefreshCw, AlertTriangle, Search } from "../components/ui/Icon";

const RANGES = [
    { label: "Últimos 15 min", ms: 15 * 60 * 1000 },
    { label: "Última hora",    ms: 60 * 60 * 1000 },
    { label: "Últimas 6 h",    ms: 6 * 60 * 60 * 1000 },
    { label: "Últimas 24 h",   ms: 24 * 60 * 60 * 1000 },
];

// Escapa un valor para incrustarlo en una cadena entre comillas de LogQL.
const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

// Detecta el nivel del log dentro de la línea para colorearla.
const LEVEL_RE = /\b(CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\b/;
const lineLevel = (line) => {
    const m = LEVEL_RE.exec(line);
    if (!m) return null;
    const lvl = m[1].toUpperCase();
    if (lvl === "CRITICAL" || lvl === "ERROR") return "ERROR";
    if (lvl.startsWith("WARN")) return "WARNING";
    return lvl; // INFO | DEBUG
};

// Estilo compartido de los selects de la barra de filtros (plantilla maestra).
const selStyle = () => ({
    padding: "7px 10px", fontSize: 12, background: T.surfaceElevated,
    border: `1px solid ${T.border}`, borderRadius: 8, color: T.text,
    fontFamily: "inherit", outline: "none",
});

const fieldLbl = () => ({
    fontSize: 10, fontWeight: 700, color: T.textMuted,
    textTransform: "uppercase", letterSpacing: "0.04em",
    display: "block", marginBottom: 4,
});

export function LogsView({ user, onBack, onProfile, apiFetch }) {
    const [services, setServices]       = useState([]);
    const [instances, setInstances]     = useState([]);
    const [service, setService]         = useState("");   // "" → todos los contenedores
    const [instance, setInstance]       = useState("");   // "" → todas las instancias
    const [search, setSearch]           = useState("");
    const [rangeMs, setRangeMs]         = useState(RANGES[1].ms);
    const [lines, setLines]             = useState([]);
    const [loading, setLoading]         = useState(false);
    const [error, setError]             = useState(null);
    const [autoRefresh, setAutoRefresh] = useState(false);

    const searchRef = useRef(search);
    useEffect(() => { searchRef.current = search; }, [search]);

    // ── Construye el selector + filtro LogQL ──────────────────────────────────
    const buildQuery = useCallback((searchText) => {
        const sel = [];
        if (service)  sel.push(`service="${esc(service)}"`);
        if (instance) sel.push(`instance="${esc(instance)}"`);
        // Si no hay servicio elegido, mostramos TODOS los contenedores docker.
        const selector = sel.length ? `{${sel.join(",")}}` : `{platform="docker"}`;
        const filter = searchText ? ` |= "${esc(searchText)}"` : "";
        return selector + filter;
    }, [service, instance]);

    // ── Cargar la lista de servicios al montar ────────────────────────────────
    useEffect(() => {
        (async () => {
            try {
                const res = await apiFetch("/logs/label/service/values");
                if (res.ok) {
                    const j = await res.json();
                    setServices((j.data || []).sort());
                }
            } catch { /* silencioso */ }
        })();
    }, [apiFetch]);

    // ── Cargar instancias cuando cambia el servicio ───────────────────────────
    useEffect(() => {
        setInstance("");
        (async () => {
            try {
                const q = service ? `?query=${encodeURIComponent(`{service="${esc(service)}"}`)}` : "";
                const res = await apiFetch(`/logs/label/instance/values${q}`);
                if (res.ok) {
                    const j = await res.json();
                    setInstances((j.data || []).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
                } else {
                    setInstances([]);
                }
            } catch { setInstances([]); }
        })();
    }, [service, apiFetch]);

    // ── Ejecutar la consulta de logs ──────────────────────────────────────────
    const runQuery = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const endNs   = (BigInt(Date.now()) * 1000000n).toString();
            const startNs = (BigInt(Date.now() - rangeMs) * 1000000n).toString();
            const query   = buildQuery(searchRef.current);
            const params  = new URLSearchParams({
                query,
                start: startNs,
                end: endNs,
                limit: "1000",
                direction: "backward",
            });
            const res = await apiFetch(`/logs/query_range?${params.toString()}`);
            if (!res.ok) {
                const t = await res.text().catch(() => "");
                throw new Error(res.status === 403
                    ? "No autorizado (se requiere superAdmin)."
                    : `Loki respondió ${res.status}. ${t}`);
            }
            const j = await res.json();
            const streams = j?.data?.result || [];
            const flat = [];
            for (const s of streams) {
                const lbl = s.stream || {};
                for (const [tsNs, line] of s.values) {
                    flat.push({
                        ts: Number(BigInt(tsNs) / 1000000n), // ns → ms
                        line,
                        service: lbl.service || lbl.container || "?",
                        instance: lbl.instance || "",
                        host: lbl.host || "",
                    });
                }
            }
            flat.sort((a, b) => b.ts - a.ts);
            setLines(flat);
        } catch (e) {
            setError(e.message || "Error al consultar los logs");
            setLines([]);
        } finally {
            setLoading(false);
        }
    }, [apiFetch, rangeMs, buildQuery]);

    // Primera carga + al cambiar filtros de servicio/instancia/rango
    useEffect(() => { runQuery(); /* eslint-disable-next-line */ }, [service, instance, rangeMs]);

    // Auto-refresco
    useEffect(() => {
        if (!autoRefresh) return;
        const id = setInterval(runQuery, 5000);
        return () => clearInterval(id);
    }, [autoRefresh, runQuery]);

    const onSearchKey = (e) => { if (e.key === "Enter") runQuery(); };

    // ── Resaltado del término buscado ─────────────────────────────────────────
    const renderLine = (line) => {
        const q = search.trim();
        if (!q) return line;
        const i = line.toLowerCase().indexOf(q.toLowerCase());
        if (i < 0) return line;
        return (
            <>
                {line.slice(0, i)}
                <mark style={{ background: T.yellow, color: T.bg, borderRadius: 3, padding: "0 2px" }}>
                    {line.slice(i, i + q.length)}
                </mark>
                {line.slice(i + q.length)}
            </>
        );
    };

    const fmt = (ms) => new Date(ms).toLocaleTimeString("es-PE", { hour12: false }) +
        "." + String(ms % 1000).padStart(3, "0");

    const LEVEL_COLOR = { ERROR: T.red, WARNING: T.yellow, DEBUG: T.textFaint };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text }}>
            {/* ── Topbar (plantilla maestra) ── */}
            <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                <button onClick={onBack}
                    style={btnBase({ padding: "6px 10px", fontSize: 11, background: T.surfaceElevated, color: T.textMuted, border: `1px solid ${T.border}`, boxShadow: "none", display: "flex", alignItems: "center", gap: 6 })}>
                    <ArrowLeft size={13} /> Volver
                </button>
                <Terminal size={16} color={T.accent} />
                <span style={{ fontSize: 15, fontWeight: 800 }}>Logs de Contenedores</span>
                <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 20, background: T.accentLight, color: T.accent, border: `1px solid ${T.accent}44` }}>
                    LOKI · SUPERADMIN
                </span>
                <button onClick={runQuery} title="Refrescar" disabled={loading}
                    style={btnBase({ padding: 6, background: "transparent", boxShadow: "none", color: T.textMuted, opacity: loading ? 0.5 : 1 })}>
                    <RefreshCw size={14} />
                </button>
                <div style={{ flex: 1 }} />
                <UserAvatar user={user} onClick={onProfile} />
            </div>

            {/* ── Barra de filtros ── */}
            <div style={{ padding: "12px 20px", borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-end", flexShrink: 0 }}>
                <div>
                    <label style={fieldLbl()}>Servicio</label>
                    <select style={selStyle()} value={service} onChange={(e) => setService(e.target.value)}>
                        <option value="">Todos los contenedores</option>
                        {services.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                </div>

                <div>
                    <label style={fieldLbl()}>Instancia</label>
                    <select style={{ ...selStyle(), opacity: instances.length ? 1 : 0.5 }}
                        value={instance}
                        onChange={(e) => setInstance(e.target.value)}
                        disabled={!instances.length}>
                        <option value="">Todas</option>
                        {instances.map((i) => <option key={i} value={i}>{i}</option>)}
                    </select>
                </div>

                <div style={{ flex: 1, minWidth: 220 }}>
                    <label style={fieldLbl()}>Buscar en los logs</label>
                    <div style={{ position: "relative" }}>
                        <Search size={13} color={T.textMuted} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
                        <input value={search} onChange={(e) => setSearch(e.target.value)}
                            onKeyDown={onSearchKey}
                            placeholder='Texto exacto (Enter), ej. slice_id=42'
                            style={{ ...inp, marginBottom: 0, padding: "7px 10px 7px 30px", fontSize: 12 }} />
                    </div>
                </div>

                <div>
                    <label style={fieldLbl()}>Rango</label>
                    <select style={selStyle()} value={rangeMs} onChange={(e) => setRangeMs(Number(e.target.value))}>
                        {RANGES.map((r) => <option key={r.ms} value={r.ms}>{r.label}</option>)}
                    </select>
                </div>

                <button onClick={runQuery} disabled={loading}
                    style={btnBase({ padding: "7px 16px", fontSize: 12, fontWeight: 700, background: T.accent, color: "#fff", border: "none", opacity: loading ? 0.6 : 1 })}>
                    {loading ? "Buscando…" : "Actualizar"}
                </button>

                <button onClick={() => setAutoRefresh(a => !a)}
                    style={btnBase({
                        padding: "7px 12px", fontSize: 11, fontWeight: 700, boxShadow: "none",
                        background: autoRefresh ? T.accent : T.surfaceElevated,
                        color: autoRefresh ? "#fff" : T.textMuted,
                        border: `1px solid ${autoRefresh ? T.accent : T.border}`,
                    })}>
                    Auto (5s)
                </button>
            </div>

            {/* ── Consola ── */}
            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: "18px 24px" }}>
                {error && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.red, fontSize: 13, background: T.redLight, padding: "10px 14px", borderRadius: 8, marginBottom: 14 }}>
                        <AlertTriangle size={15} /> {error}
                    </div>
                )}

                <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden", boxShadow: T.shadow }}>
                    {/* Cabecera de la consola */}
                    <div style={{ padding: "9px 14px", background: T.surfaceElevated, borderBottom: `1px solid ${T.border}`, display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                        <span style={{ fontSize: 10, fontWeight: 800, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                            {lines.length} líneas
                        </span>
                        {search.trim() && (
                            <span style={{ fontSize: 10, color: T.textFaint }}>
                                que contienen "{search.trim()}"
                            </span>
                        )}
                        <div style={{ flex: 1 }} />
                        <span style={{ fontSize: 10, color: T.textFaint }}>más recientes primero</span>
                    </div>

                    {/* Líneas */}
                    <div style={{ flex: 1, overflow: "auto", fontFamily: "'JetBrains Mono','Fira Code',Consolas,monospace", fontSize: 12, lineHeight: 1.55 }}>
                        {lines.length === 0 && !loading && (
                            <div style={{ padding: 30, textAlign: "center", color: T.textMuted, fontFamily: "'DM Sans','Segoe UI',sans-serif", fontSize: 13 }}>
                                Sin resultados para el filtro actual.
                            </div>
                        )}
                        {loading && lines.length === 0 && (
                            <div style={{ padding: 30, textAlign: "center", color: T.textMuted, fontFamily: "'DM Sans','Segoe UI',sans-serif", fontSize: 13 }}>
                                Cargando…
                            </div>
                        )}
                        {lines.map((l, idx) => {
                            const lvl = lineLevel(l.line);
                            const isErr = lvl === "ERROR";
                            return (
                                <div key={idx} style={{
                                    display: "flex", gap: 12, padding: "2px 14px",
                                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                                    background: isErr ? `${T.redLight}88` : (idx % 2 ? "transparent" : `${T.surfaceElevated}66`),
                                }}>
                                    <span style={{ color: T.textFaint, flexShrink: 0 }}>{fmt(l.ts)}</span>
                                    <span title={l.host ? `host: ${l.host}` : undefined} style={{
                                        flexShrink: 0, minWidth: 130, alignSelf: "flex-start",
                                        fontSize: 10, fontWeight: 700, padding: "1px 8px", marginTop: 2,
                                        borderRadius: 20, background: T.accentLight, color: T.accent,
                                        textAlign: "center",
                                    }}>
                                        {l.service}{l.instance ? `#${l.instance}` : ""}
                                    </span>
                                    <span style={{ flex: 1, color: LEVEL_COLOR[lvl] || T.text }}>
                                        {renderLine(l.line)}
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
