import { useState, useEffect, useCallback, useRef } from "react";

// ─────────────────────────────────────────────────────────────────────────────
// LogsView — Visor de logs centralizados (Loki) para el superAdmin.
//
// Los logs se separan por `service` (nombre del microservicio) y por `instance`
// (número de réplica), de modo que 2 instancias del mismo servicio se ven aparte.
// La lupita hace búsqueda por texto usando el filtro de línea de LogQL (|=).
//
// Props: { user, onBack, onProfile, apiFetch }
//   apiFetch(path, opts?) → Promise<Response>   (mismo helper que el resto de vistas)
// ─────────────────────────────────────────────────────────────────────────────

const RANGES = [
  { label: "Últimos 15 min", ms: 15 * 60 * 1000 },
  { label: "Última hora",    ms: 60 * 60 * 1000 },
  { label: "Últimas 6 h",    ms: 6 * 60 * 60 * 1000 },
  { label: "Últimas 24 h",   ms: 24 * 60 * 60 * 1000 },
];

// Escapa un valor para incrustarlo en una cadena entre comillas de LogQL.
const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

export function LogsView({ user, onBack, onProfile, apiFetch }) {
  const [services, setServices]         = useState([]);
  const [instances, setInstances]       = useState([]);
  const [service, setService]           = useState("");   // "" → todos los contenedores
  const [instance, setInstance]         = useState("");   // "" → todas las instancias
  const [search, setSearch]             = useState("");
  const [rangeMs, setRangeMs]           = useState(RANGES[1].ms);
  const [lines, setLines]               = useState([]);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState(null);
  const [autoRefresh, setAutoRefresh]   = useState(false);

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
        <mark style={{ background: "#f6c945", color: "#1a1a1a", borderRadius: 2 }}>
          {line.slice(i, i + q.length)}
        </mark>
        {line.slice(i + q.length)}
      </>
    );
  };

  const fmt = (ms) => new Date(ms).toLocaleTimeString("es-PE", { hour12: false }) +
    "." + String(ms % 1000).padStart(3, "0");

  return (
    <div style={S.page}>
      {/* Header */}
      <div style={S.header}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button style={S.ghostBtn} onClick={onBack}>← Volver</button>
          <h1 style={S.title}>Logs de contenedores</h1>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ color: "#8a93a5", fontSize: 13 }}>{user?.username || user?.name}</span>
          {onProfile && <button style={S.ghostBtn} onClick={onProfile}>Perfil</button>}
        </div>
      </div>

      {/* Controles */}
      <div style={S.controls}>
        <label style={S.field}>
          <span style={S.lbl}>Servicio</span>
          <select style={S.select} value={service} onChange={(e) => setService(e.target.value)}>
            <option value="">Todos los contenedores</option>
            {services.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>

        <label style={S.field}>
          <span style={S.lbl}>Instancia</span>
          <select
            style={S.select}
            value={instance}
            onChange={(e) => setInstance(e.target.value)}
            disabled={!instances.length}
          >
            <option value="">Todas</option>
            {instances.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>

        <label style={{ ...S.field, flex: 1, minWidth: 220 }}>
          <span style={S.lbl}>Buscar en los logs</span>
          <div style={S.searchWrap}>
            <span style={S.searchIcon} aria-hidden>🔍</span>
            <input
              style={S.searchInput}
              type="text"
              placeholder='Texto exacto, ej.  slice_id=42'
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={onSearchKey}
            />
          </div>
        </label>

        <label style={S.field}>
          <span style={S.lbl}>Rango</span>
          <select style={S.select} value={rangeMs} onChange={(e) => setRangeMs(Number(e.target.value))}>
            {RANGES.map((r) => <option key={r.ms} value={r.ms}>{r.label}</option>)}
          </select>
        </label>

        <button style={S.primaryBtn} onClick={runQuery} disabled={loading}>
          {loading ? "Buscando…" : "Actualizar"}
        </button>

        <label style={{ ...S.field, flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          <span style={{ color: "#8a93a5", fontSize: 13 }}>Auto (5s)</span>
        </label>
      </div>

      {/* Meta */}
      <div style={S.meta}>
        {error
          ? <span style={{ color: "#ff6b6b" }}>{error}</span>
          : <span>{lines.length} líneas{search.trim() ? ` que contienen "${search.trim()}"` : ""}</span>}
      </div>

      {/* Consola */}
      <div style={S.console}>
        {lines.length === 0 && !loading && (
          <div style={S.empty}>Sin resultados para el filtro actual.</div>
        )}
        {lines.map((l, idx) => (
          <div key={idx} style={S.row}>
            <span style={S.ts}>{fmt(l.ts)}</span>
            <span style={S.tag} title={l.host ? `host: ${l.host}` : undefined}>
              {l.service}{l.instance ? `#${l.instance}` : ""}
            </span>
            <span style={S.msg}>{renderLine(l.line)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Estilos (autocontenidos, look de consola oscura) ────────────────────────
const S = {
  page:   { display: "flex", flexDirection: "column", height: "100vh", background: "#0e1116", color: "#e6e9ef" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 20px", borderBottom: "1px solid #1f2530" },
  title:  { fontSize: 18, fontWeight: 600, margin: 0 },
  controls: { display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end", padding: "14px 20px", borderBottom: "1px solid #1f2530" },
  field:  { display: "flex", flexDirection: "column", gap: 4 },
  lbl:    { fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4, color: "#6b7280" },
  select: { background: "#171b22", color: "#e6e9ef", border: "1px solid #2a303c", borderRadius: 6, padding: "8px 10px", fontSize: 14 },
  searchWrap: { display: "flex", alignItems: "center", background: "#171b22", border: "1px solid #2a303c", borderRadius: 6, padding: "0 10px" },
  searchIcon: { opacity: 0.6, marginRight: 6 },
  searchInput: { flex: 1, background: "transparent", border: "none", outline: "none", color: "#e6e9ef", padding: "8px 0", fontSize: 14 },
  primaryBtn: { background: "#3b82f6", color: "#fff", border: "none", borderRadius: 6, padding: "9px 16px", fontSize: 14, cursor: "pointer" },
  ghostBtn: { background: "transparent", color: "#9aa4b2", border: "1px solid #2a303c", borderRadius: 6, padding: "6px 12px", fontSize: 13, cursor: "pointer" },
  meta:   { padding: "8px 20px", fontSize: 13, color: "#8a93a5", borderBottom: "1px solid #1f2530" },
  console:{ flex: 1, overflow: "auto", padding: "8px 0", fontFamily: "'JetBrains Mono','Fira Code',Consolas,monospace", fontSize: 12.5, lineHeight: 1.55 },
  empty:  { padding: 24, color: "#6b7280", textAlign: "center" },
  row:    { display: "flex", gap: 12, padding: "1px 20px", whiteSpace: "pre-wrap", wordBreak: "break-word" },
  ts:     { color: "#6b7280", flexShrink: 0 },
  tag:    { color: "#5eead4", flexShrink: 0, minWidth: 140 },
  msg:    { color: "#d5dae3", flex: 1 },
};
