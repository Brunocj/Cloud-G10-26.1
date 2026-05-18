import { useState, useRef, useCallback, useEffect } from "react"; // <-- Importa useEffect
import VmConsole from "./VmConsole.jsx"; // 🔥 IMPORTA TU COMPONENTE AQUÍ
// --- TOKENS ------------------------------------------------------------------
const T = {
    bg: "#f4f7f1",
    surface: "#ffffff",
    surfaceElevated: "#eef3ea",
    border: "#d5e4cc",
    borderHover: "#9dc48e",
    accent: "#2e7d32",
    accentLight: "#e8f5e9",
    accentMid: "#4caf50",
    red: "#c62828",
    redLight: "#ffebee",
    yellow: "#e65100",
    yellowLight: "#fff3e0",
    text: "#1b2e1c",
    textMuted: "#5a8060",
    textFaint: "#adc8a8",
    shadow: "0 1px 3px rgba(20,50,22,0.08), 0 4px 12px rgba(20,50,22,0.05)",
    shadowMd: "0 4px 16px rgba(20,50,22,0.12), 0 8px 28px rgba(20,50,22,0.07)",
};


let _nid = 200;
// Añade defaultImg como parámetro
const mkNode = (x, y, label, defaultImg) => ({
    id: `n${_nid++}`, x, y,
    label: label || `VM-${_nid - 200}`,
    // 🔥 Bajamos los valores por defecto aquí
    vcores: 1, ram: 256, disk: 1,
    image: defaultImg?.name || "Cirros",
    image_id: defaultImg?.id || null
});

const buildLinear = (count, cx, cy) => {
    const sp = 150, totalW = (count - 1) * sp;
    const nodes = Array.from({ length: count }, (_, i) => mkNode(cx - totalW / 2 + i * sp, cy, `VM-${i + 1}`));
    const edges = nodes.slice(0, -1).map((_, i) => ({ id: `e${Date.now()}-${i}`, from: nodes[i].id, to: nodes[i + 1].id }));
    return { nodes, edges };
};
const buildRing = (count, cx, cy) => {
    const r = Math.max(100, count * 30);
    const nodes = Array.from({ length: count }, (_, i) => {
        const a = (2 * Math.PI * i) / count - Math.PI / 2;
        return mkNode(cx + r * Math.cos(a), cy + r * Math.sin(a), `VM-${i + 1}`);
    });
    const edges = nodes.map((_, i) => ({ id: `e${Date.now()}-${i}`, from: nodes[i].id, to: nodes[(i + 1) % count].id }));
    return { nodes, edges };
};

// --- STYLES -------------------------------------------------------------------
const btnBase = (extra = {}) => ({
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
    padding: "7px 14px", borderRadius: 8, border: `1px solid ${T.border}`,
    cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: "inherit",
    background: T.surface, color: T.text, transition: "opacity 0.15s",
    boxShadow: T.shadow, ...extra,
});
const inp = {
    background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 7,
    color: T.text, fontSize: 13, padding: "8px 10px", width: "100%",
    outline: "none", fontFamily: "inherit", boxSizing: "border-box",
};
const Lbl = ({ children, style: s }) => (
    <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 5, ...s }}>{children}</div>
);

const Badge = ({ status }) => {
    const map = {
        ACTIVE: [T.accent, T.accentLight],
        DRAFT: [T.textMuted, T.surfaceElevated],
        PROVISIONING: ["#1976d2", "#e3f2fd"], // Azul
        PENDING_APPROVAL: [T.yellow, T.yellowLight], // Naranja
        FAILED: [T.red, T.redLight],
        TERMINATED: ["#616161", "#eeeeee"] // Gris oscuro
    };
    const [c, bg] = map[status] || map.DRAFT;
    return (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 9px", borderRadius: 20, fontSize: 10, fontWeight: 700, color: c, background: bg, border: `1px solid ${c}33`, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: c }} />{status}
        </span>
    );
};

// --- NODE EDITOR --------------------------------------------------------------
const NodeEditor = ({ node, availableImages, sliceStatus, onSave, onDelete, onClose, onOpenConsole }) => {
    const defaultImg = availableImages?.[0];
    const initialImgId = node.image_id || defaultImg?.id || "";
    const initialImgName = node.image || defaultImg?.name || "";

    const [f, setF] = useState({
        ...node,
        image_id: initialImgId,
        image: initialImgName,
        // 🔥 Agregamos los campos de red
        internet_access: node.internet_access || 0,
        external_ip: node.external_ip || ""
    });

    const u = (k, v) => setF(p => ({ ...p, [k]: v }));
    const isReadOnly = sliceStatus && sliceStatus !== "DRAFT";

    const [availableIps, setAvailableIps] = useState([]);

    useEffect(() => {
        if (f.internet_access === 1) {
            // Nota: Puse tu puerto real 8085 del ApiGateway
            fetch(`http://localhost:8085/api/v1/slices/utils/available-ips`)
                .then(res => res.json())
                .then(data => {
                    if (f.external_ip && !data.includes(f.external_ip)) {
                        setAvailableIps([f.external_ip, ...data]);
                    } else {
                        setAvailableIps(data);
                    }
                });
        }
    }, [f.internet_access]);

    return (
        <div style={{ position: "absolute", right: 12, top: 12, width: 268, zIndex: 300, background: T.surface, borderRadius: 14, border: `1.5px solid ${T.accentMid}55`, boxShadow: T.shadowMd, overflow: "hidden" }}>
            <div style={{ background: T.accentLight, padding: "11px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12, fontWeight: 800, color: T.accent }}>
                    {isReadOnly ? "⚙️ Control de Nodo" : "🛠️ Node Properties"}
                </span>
                <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, fontSize: 18, lineHeight: 1 }}>×</button>
            </div>

            <div style={{ padding: "14px 14px 12px", display: "flex", flexDirection: "column", gap: 11 }}>
                <div><Lbl>VM Name</Lbl><input value={f.label} disabled={isReadOnly} onChange={e => u("label", e.target.value)} style={inp} /></div>

                <div>
                    <Lbl>OS Image</Lbl>
                    <select
                        value={f.image_id || ""}
                        disabled={isReadOnly}
                        onChange={e => {
                            const selectedId = Number(e.target.value);
                            const selectedName = availableImages.find(i => i.id === selectedId)?.name;
                            // Guardamos ambos en un solo setState para evitar que React batchee y pierda uno
                            setF(p => ({ ...p, image_id: selectedId, image: selectedName }));
                        }}
                        style={inp}
                    >
                        <option value="" disabled>Select an OS...</option>
                        {availableImages?.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
                    </select>
                </div>

                {/* --- Credenciales de la VM (cloud-init) --- */}
                {!isReadOnly && (
                <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                    <Lbl>Credenciales VM <span style={{ fontWeight: 400, color: T.textFaint }}>(cloud-init)</span></Lbl>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                        <div>
                            <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>Usuario</div>
                            <input
                                value={f.vm_user || ""}
                                placeholder={f.image?.toLowerCase().split("-")[0] || "ubuntu"}
                                onChange={e => u("vm_user", e.target.value)}
                                style={{ ...inp, fontSize: 12 }}
                            />
                        </div>
                        <div>
                            <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>Contraseña</div>
                            <input
                                type="text"
                                value={f.vm_password || ""}
                                placeholder="pucp2026"
                                onChange={e => u("vm_password", e.target.value)}
                                style={{ ...inp, fontSize: 12 }}
                            />
                        </div>
                    </div>
                    <div style={{ fontSize: 9, color: T.textFaint, marginTop: 6 }}>⚠️ Dejar vacío usa el nombre de imagen como usuario y "pucp2026" como contraseña. No aplica a Cirros.</div>
                </div>
                )}

                {/* En modo lectura: mostrar credenciales configuradas */}
                {isReadOnly && (node.vm_user || node.vm_password) && (
                <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "9px 12px", border: `1px solid ${T.border}` }}>
                    <Lbl>Credenciales</Lbl>
                    <div style={{ fontSize: 11, color: T.text }}>
                        <span style={{ fontWeight: 700 }}>Usuario:</span> {node.vm_user || "(imagen)"}{" "}|{" "}
                        <span style={{ fontWeight: 700 }}>Pass:</span> {node.vm_password || "pucp2026"}
                    </div>
                </div>
                )}

                <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                    <Lbl>Resources</Lbl>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                        {[["vcores", "vCPU", 1, 16, 1], ["ram", "RAM MB", 128, 16384, 128], ["disk", "Disk GB", 1, 500, 1]].map(([k, l, mn, mx, st]) => (
                            <div key={k}>
                                <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>{l}</div>
                                <input type="number" value={f[k]} min={mn} max={mx} step={st}
                                    disabled={isReadOnly}
                                    onChange={e => u(k, Number(e.target.value))}
                                    style={{ ...inp, padding: "6px 4px", textAlign: "center", fontWeight: 800, color: T.accent, fontSize: 13 }} />
                            </div>
                        ))}
                    </div>
                </div>

                {/* --- NUEVO BLOQUE: NETWORKING (R5) --- */}
                <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                    <Lbl>Networking (R5)</Lbl>
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

                        {/* Checkbox: Salida a Internet */}
                        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 600, color: T.text, cursor: isReadOnly ? "default" : "pointer" }}>
                            <input
                                type="checkbox"
                                disabled={isReadOnly}
                                checked={f.internet_access === 1}
                                onChange={e => {
                                    const isChecked = e.target.checked;
                                    setF(prev => ({
                                        ...prev,
                                        internet_access: isChecked ? 1 : 0,
                                        // Seguridad de UX: Si apaga internet, borramos la IP externa
                                        external_ip: isChecked ? prev.external_ip : ""
                                    }));
                                }}
                                style={{ accentColor: T.accent, width: 14, height: 14 }}
                            />
                            Habilitar Salida a Internet (NAT)
                        </label>

                        {/* Input: IP Externa (Solo se habilita si hay internet) */}
                        <div style={{ opacity: f.internet_access ? 1 : 0.5, transition: "opacity 0.2s" }}>
                            <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, marginBottom: 4 }}>Asignar IP del Pool (10.60.15.X)</div>
                            <select
                                value={f.external_ip || ""}
                                disabled={isReadOnly || !f.internet_access}
                                onChange={e => u("external_ip", e.target.value)}
                                style={{ ...inp, fontSize: 12, cursor: "pointer", background: (isReadOnly || !f.internet_access) ? "transparent" : T.surface }}
                            >
                                <option value="">-- Seleccionar IP --</option>
                                {availableIps.map(ip => (
                                    <option key={ip} value={ip}>{ip}</option>
                                ))}
                            </select>
                        </div>
                    </div>
                </div>
                {/* -------------------------------------- */}

                {/* BOTONES DINÁMICOS SEGÚN EL ESTADO */}
                {isReadOnly ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
                        {/* REQ-US-11: Botón de Consola Web */}
                        <button
                            // 🔥 FIX: Usamos la prop que recibimos
                            onClick={() => onOpenConsole({ workerIp: node.worker_ip, vncPort: node.vnc_port })}
                            disabled={sliceStatus !== "ACTIVE"}
                            style={btnBase({ width: "100%", background: T.text, color: "#fff", border: "none", padding: "8px 0", opacity: sliceStatus === "ACTIVE" ? 1 : 0.5 })}>
                            Abrir Consola Web
                        </button>
                        {/* REQ-US-12: Botón de Telemetría */}
                        <button style={btnBase({ width: "100%", background: T.surface, color: T.accent, border: `1px solid ${T.accent}` })}>
                            📊 Ver Telemetría
                        </button>
                    </div>
                ) : (
                    <div style={{ display: "flex", gap: 8 }}>
                        <button onClick={() => { onSave(f); onClose(); }} style={btnBase({ flex: 1, background: T.accent, color: "#fff", border: "none" })}>💾 Save</button>
                        <button onClick={() => { onDelete(node.id); onClose(); }} style={btnBase({ background: T.redLight, color: T.red, border: `1px solid ${T.red}33` })}>🗑️</button>
                    </div>
                )}
            </div>
        </div>
    );
};

// --- GRAPH CANVAS -------------------------------------------------------------
//
// KEY FIX: All interaction is tracked purely on the SVG element itself using
// clientX/Y converted to SVG coords. We never call stopPropagation on node
// elements — instead we track which node was hit via a ref, so both the
// background and nodes funnel through the same onPointerDown on the SVG.
//
const Canvas = ({ nodes, edges, setNodes, setEdges, imageList, activeSlice, onOpenConsole }) => {
    const svgRef = useRef();
    const [mode, setMode] = useState("select");

    // Link state
    const [linkFrom, setLinkFrom] = useState(null);
    const [mouse, setMouse] = useState({ x: 0, y: 0 });

    // Drag state (refs = no re-render during drag)
    const dragging = useRef(null); // { id, startNodeX, startNodeY, startMouseX, startMouseY }

    // Double-click state
    const lastTap = useRef({ id: null, t: 0 });
    const [editId, setEditId] = useState(null);

    const toSVG = e => {
        const r = svgRef.current.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    // Hit-test: which node (if any) is under a point?
    const hitNode = (p) => {
        // iterate in reverse so topmost node wins
        for (let i = nodes.length - 1; i >= 0; i--) {
            const n = nodes[i];
            if (Math.abs(p.x - n.x) <= 30 && Math.abs(p.y - n.y) <= 28) return n;
        }
        return null;
    };

    // -- POINTER DOWN on SVG ---------------------------------------------------
    const onPointerDown = useCallback(e => {
        // Only react to primary button (left click)
        if (e.button !== 0) return;
        const p = toSVG(e);
        const hit = hitNode(p);

        // -- LINK MODE ------------------------------------------------------------
        if (mode === "link") {
            if (!hit) { setLinkFrom(null); return; }
            if (!linkFrom) {
                setLinkFrom(hit.id);
                return;
            }
            if (hit.id === linkFrom) { setLinkFrom(null); return; }
            // Connect
            const dup = edges.find(ed =>
                (ed.from === linkFrom && ed.to === hit.id) ||
                (ed.from === hit.id && ed.to === linkFrom)
            );
            if (!dup) setEdges(prev => [...prev, { id: `e${Date.now()}`, from: linkFrom, to: hit.id }]);
            setLinkFrom(null);
            return;
        }

        // -- SELECT MODE ----------------------------------------------------------
        if (!hit) { setEditId(null); return; }

        // Double-click detection
        const now = Date.now();
        if (lastTap.current.id === hit.id && now - lastTap.current.t < 380) {
            lastTap.current = { id: null, t: 0 };
            setEditId(hit.id);
            return;
        }
        lastTap.current = { id: hit.id, t: now };

        // Start drag
        dragging.current = { id: hit.id, startNodeX: hit.x, startNodeY: hit.y, startMouseX: p.x, startMouseY: p.y };
        svgRef.current.setPointerCapture(e.pointerId);
    }, [mode, linkFrom, edges, nodes]);

    // -- POINTER MOVE ----------------------------------------------------------
    const onPointerMove = useCallback(e => {
        const p = toSVG(e);
        setMouse(p);
        if (!dragging.current) return;
        const { id, startNodeX, startNodeY, startMouseX, startMouseY } = dragging.current;
        setNodes(prev => prev.map(n =>
            n.id === id
                ? { ...n, x: startNodeX + p.x - startMouseX, y: startNodeY + p.y - startMouseY }
                : n
        ));
    }, []);

    const onPointerUp = useCallback(() => { dragging.current = null; }, []);

    // -- DROP from sidebar -----------------------------------------------------
    const onDrop = e => {
        e.preventDefault();
        const p = toSVG(e);
        const tplType = e.dataTransfer.getData("templateType");
        const tplCount = Number(e.dataTransfer.getData("templateCount") || 0);
        if (tplType && tplCount) {
            const built = tplType === "linear" ? buildLinear(tplCount, p.x, p.y) : buildRing(tplCount, p.x, p.y);
            setNodes(prev => [...prev, ...built.nodes]);
            setEdges(prev => [...prev, ...built.edges]);
            return;
        }
        if (e.dataTransfer.getData("nodeType")) {
            setNodes(prev => [...prev, mkNode(p.x, p.y, undefined, imageList[0])]);
        }
    };

    const switchMode = m => { setMode(m); setLinkFrom(null); setEditId(null); };

    const editingNode = editId ? nodes.find(n => n.id === editId) : null;
    const linkFromNode = linkFrom ? nodes.find(n => n.id === linkFrom) : null;
    const totalRam = nodes.reduce((s, n) => s + n.ram, 0);

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative" }}>

            {/* Mode bar */}
            <div style={{ padding: "8px 14px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated, display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                {[["select", "🖱️ Select & Move"], ["link", "🔗 Link Nodes"]].map(([m, lbl]) => (
                    <button key={m} onClick={() => switchMode(m)}
                        style={btnBase({
                            padding: "5px 13px", fontSize: 11, boxShadow: "none",
                            background: mode === m ? T.accentLight : T.surface,
                            color: mode === m ? T.accent : T.textMuted,
                            border: `1px solid ${mode === m ? T.accent + "66" : T.border}`,
                        })}>{lbl}
                    </button>
                ))}
                {mode === "link" && (
                    <span style={{ fontSize: 11, color: T.accent, background: T.accentLight, padding: "4px 12px", borderRadius: 6, border: `1px solid ${T.accent}44` }}>
                        {linkFrom ? "Click the destination node to connect — or click background to cancel" : "Click the source node to start a link"}
                    </span>
                )}
                {mode === "select" && <span style={{ fontSize: 11, color: T.textMuted }}>Drag to move · Double-click to edit · Click an edge to delete it</span>}
                <div style={{ flex: 1 }} />
                <button onClick={() => {
                    if (window.confirm("⚠️ ¿Estás seguro de limpiar todo el lienzo? Perderás el trabajo no guardado.")) {
                        setNodes([]); setEdges([]); setLinkFrom(null); setEditId(null);
                    }
                }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 12px", color: T.red, border: `1px solid ${T.red}33`, background: T.redLight })}>
                    🗑️ Clear
                </button>
            </div>

            {/* SVG */}
            <div style={{ flex: 1, position: "relative", overflow: "hidden" }}
                onDragOver={e => e.preventDefault()} onDrop={onDrop}>

                <svg ref={svgRef} width="100%" height="100%"
                    style={{ display: "block", cursor: mode === "link" ? "crosshair" : "default", touchAction: "none" }}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}>

                    <defs>
                        <pattern id="dots2" width="24" height="24" patternUnits="userSpaceOnUse">
                            <circle cx="0.8" cy="0.8" r="0.8" fill={T.border} />
                        </pattern>
                    </defs>
                    <rect width="100%" height="100%" fill={T.bg} />
                    <rect width="100%" height="100%" fill="url(#dots2)" />

                    {/* Temp link line */}
                    {linkFromNode && (
                        <line x1={linkFromNode.x} y1={linkFromNode.y} x2={mouse.x} y2={mouse.y}
                            stroke={T.accentMid} strokeWidth="2" strokeDasharray="7,4" opacity="0.7"
                            style={{ pointerEvents: "none" }} />
                    )}

                    {/* Edges — rendered below nodes */}
                    {edges.map(ed => {
                        const A = nodes.find(n => n.id === ed.from), B = nodes.find(n => n.id === ed.to);
                        if (!A || !B) return null;
                        return (
                            <g key={ed.id}>
                                <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                    stroke={T.accentMid} strokeWidth="2.5" opacity="0.5" strokeLinecap="round"
                                    style={{ pointerEvents: "none" }} />
                                {/* Wide invisible hit area for delete */}
                                <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                    stroke="transparent" strokeWidth="18" strokeLinecap="round"
                                    style={{ cursor: "pointer" }}
                                    onPointerDown={ev => {
                                        // Only delete if we're not over a node
                                        const p = toSVG(ev);
                                        if (!hitNode(p)) {
                                            ev.stopPropagation();
                                            setEdges(prev => prev.filter(x => x.id !== ed.id));
                                        }
                                    }} />
                            </g>
                        );
                    })}

                    {/* Nodes */}
                    {nodes.map(node => {
                        const isLinkSrc = linkFrom === node.id;
                        const isEditing = editId === node.id;
                        const isLinkTarget = mode === "link" && linkFrom && linkFrom !== node.id;
                        const ram = node.ram >= 1024 ? `${node.ram / 1024}GB` : `${node.ram}MB`;
                        return (
                            <g key={node.id} transform={`translate(${node.x},${node.y})`} style={{ pointerEvents: "none" }}>
                                {/* Shadow */}
                                <rect x="-28" y="-24" width="56" height="52" rx="12" fill="rgba(20,50,22,0.12)" transform="translate(2,3)" />
                                {/* Card */}
                                <rect x="-28" y="-24" width="56" height="52" rx="12"
                                    fill={T.surface}
                                    stroke={isLinkSrc ? T.accent : isEditing ? T.accentMid : isLinkTarget ? T.accentMid + "88" : T.border}
                                    strokeWidth={isLinkSrc || isEditing ? 2 : 1.5} />
                                {/* Top stripe */}
                                <rect x="-28" y="-24" width="56" height="8" rx="12" fill={isLinkSrc ? T.accent : T.accentMid} />
                                <rect x="-28" y="-18" width="56" height="4" fill={isLinkSrc ? T.accent : T.accentMid} />
                                {/* Icon */}
                                <text textAnchor="middle" dominantBaseline="middle" y="-5" style={{ fontSize: 17 }}>🖥️</text>
                                {/* Worker */}
                                <rect x="-22" y="5" width="44" height="12" rx="3" fill={T.accentLight} />
                                <text x="0" y="11" textAnchor="middle" dominantBaseline="middle"
                                    style={{ fontSize: 7.5, fill: T.accent, fontFamily: "monospace", fontWeight: 700 }}>
                                    {node.worker}
                                </text>
                                {/* Label */}
                                <text x="0" y="25" textAnchor="middle" style={{ fontSize: 10, fontWeight: 700, fill: T.text }}>{node.label}</text>
                                {/* Specs */}
                                <text x="0" y="36" textAnchor="middle" style={{ fontSize: 8, fill: T.textMuted }}>
                                    {node.vcores}vCPU · {ram} · {node.disk}GB
                                </text>
                                {/* Ring when link source */}
                                {isLinkSrc && <circle r="36" fill="none" stroke={T.accent} strokeWidth="1.5" strokeDasharray="5,3" opacity="0.45" />}
                            </g>
                        );
                    })}
                </svg>

                {/* Empty hint */}
                {nodes.length === 0 && (
                    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none", gap: 8 }}>
                        <div style={{ fontSize: 44, opacity: 0.2 }}>🖥️</div>
                        <div style={{ color: T.textMuted, fontSize: 13, fontWeight: 600 }}>Drag VMs here or drop a template from the sidebar</div>
                        <div style={{ color: T.textFaint, fontSize: 11 }}>Double-click any node to edit it</div>
                    </div>
                )}

                {/* Node editor overlay */}
                {editingNode && (
                    <NodeEditor node={editingNode}
                        availableImages={imageList}
                        sliceStatus={activeSlice ? activeSlice.status : "DRAFT"}
                        onSave={updated => setNodes(prev => prev.map(n => n.id === updated.id ? updated : n))}
                        onDelete={id => { setNodes(p => p.filter(n => n.id !== id)); setEdges(p => p.filter(e => e.from !== id && e.to !== id)); }}
                        onClose={() => setEditId(null)}
                        onOpenConsole={onOpenConsole} // 🔥 FIX: Se la pasamos al NodeEditor
                    />
                )}
            </div>

            {/* Stats bar */}
            {nodes.length > 0 && (
                <div style={{ padding: "7px 16px", borderTop: `1px solid ${T.border}`, background: T.surfaceElevated, display: "flex", gap: 20, flexShrink: 0 }}>
                    {[
                        ["VMs", nodes.length],
                        ["Links", edges.length],
                        ["Total vCPU", nodes.reduce((s, n) => s + n.vcores, 0)],
                        ["Total RAM", totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`],
                        ["Total Disk", `${nodes.reduce((s, n) => s + n.disk, 0)} GB`],
                    ].map(([l, v]) => (
                        <div key={l} style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
                            <span style={{ fontSize: 14, fontWeight: 800, color: T.accent }}>{v}</span>
                            <span style={{ fontSize: 10, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em" }}>{l}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

// --- TEMPLATE PICKER ----------------------------------------------------------
const TemplatePicker = () => {
    const [counts, setCounts] = useState({ linear: 4, ring: 5 });
    const [draggingTpl, setDraggingTpl] = useState(null);
    return (
        <div style={{ padding: "14px 16px 12px", borderBottom: `1px solid ${T.border}` }}>
            <Lbl>Templates — drag to canvas</Lbl>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {[{ id: "linear", icon: "⛓️", name: "Linear Chain" }, { id: "ring", icon: "⭕", name: "Ring" }].map(tpl => (
                    <div key={tpl.id} style={{ border: `1px solid ${T.border}`, borderRadius: 10, overflow: "hidden", background: T.surface, boxShadow: T.shadow }}>
                        <div style={{ padding: "7px 10px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated, display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ fontSize: 12, fontWeight: 700, color: T.text, flex: 1 }}>{tpl.icon} {tpl.name}</span>
                            <span style={{ fontSize: 10, color: T.textMuted }}>VMs:</span>
                            <select value={counts[tpl.id]}
                                onChange={e => setCounts(c => ({ ...c, [tpl.id]: Number(e.target.value) }))}
                                onClick={e => e.stopPropagation()}
                                style={{ ...inp, padding: "2px 6px", width: 52, fontSize: 12, fontWeight: 700, color: T.accent }}>
                                {Array.from({ length: 9 }, (_, i) => i + 2).map(n => <option key={n}>{n}</option>)}
                            </select>
                        </div>
                        <div draggable
                            onDragStart={e => { e.dataTransfer.setData("templateType", tpl.id); e.dataTransfer.setData("templateCount", counts[tpl.id]); setDraggingTpl(tpl.id); }}
                            onDragEnd={() => setDraggingTpl(null)}
                            style={{ padding: "9px 10px", display: "flex", alignItems: "center", gap: 10, cursor: "grab", background: draggingTpl === tpl.id ? T.accentLight : "transparent", transition: "background 0.15s" }}>
                            <span style={{ fontSize: 20 }}>{tpl.id === "linear" ? "⛓️" : "⭕"}</span>
                            <div>
                                <div style={{ fontSize: 11, fontWeight: 600, color: T.text }}>{counts[tpl.id]} nodes · drag to place</div>
                                <div style={{ fontSize: 10, color: T.textMuted }}>Adds to existing topology</div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

// --- SLICE CARD ---------------------------------------------------------------
const SliceCard = ({ slice, active, onClick, onDestroy, onDeploy }) => (
    <div onClick={onClick} style={{ padding: "12px 14px", borderRadius: 10, cursor: "pointer", border: `1.5px solid ${active ? T.accent : T.border}`, background: active ? T.accentLight : T.surface, transition: "all 0.15s", boxShadow: active ? `0 0 0 3px ${T.accent}18` : T.shadow }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6 }}>
            <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{slice.name}</div>
                <Badge status={slice.status} />
            </div>

            {/* 🔥 FIX: Solo mostramos la basura si NO está terminado */}
            {slice.status !== "TERMINATED" && (
                <button onClick={e => { e.stopPropagation(); onDestroy(slice.id); }}
                    style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4, fontSize: 15 }}
                    title="Destroy slice">🗑️</button>
            )}

        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 5, marginTop: 6 }}>
            {[["VMs", slice.nodeCount], ["Links", slice.edgeCount], ["vCPU", slice.vcpus], ["RAM", slice.ramLabel]].map(([l, v]) => (
                <div key={l} style={{ textAlign: "center", background: T.surfaceElevated, borderRadius: 6, padding: "5px 2px", border: `1px solid ${T.border}` }}>
                    <div style={{ fontSize: 13, fontWeight: 800, color: T.accent }}>{v}</div>
                    <div style={{ fontSize: 8, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                </div>
            ))}
        </div>
        {/* Draft: show Request Deploy button */}
        {slice.status === "Draft" && (
            <button onClick={e => { e.stopPropagation(); onDeploy(slice.id); }}
                style={{ ...btnBase({ width: "100%", marginTop: 10, background: T.accent, color: "#fff", border: "none", padding: "7px 0", fontSize: 12 }) }}>
                🚀 Request Deployment
            </button>
        )}
    </div>
);

// --- MODALS -------------------------------------------------------------------
const Overlay = ({ children }) => (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9000 }}>
        {children}
    </div>
);

const SaveDraftModal = ({ nodes, edges, onSave, onClose }) => {
    const [name, setName] = useState(`draft-${Math.random().toString(36).slice(2, 6)}`);
    return (
        <Overlay>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16, padding: 28, maxWidth: 380, width: "90%", boxShadow: T.shadowMd }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4 }}>💾 Save as Draft</div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 18 }}>Your topology will be saved. You can deploy it later.</div>
                <Lbl>Slice Name</Lbl>
                <input value={name} onChange={e => setName(e.target.value)} style={inp} />
                <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancel</button>
                    <button onClick={() => onSave(name)} disabled={!name.trim()}
                        style={btnBase({ flex: 2, background: T.accent, color: "#fff", border: "none", opacity: !name.trim() ? 0.5 : 1 })}>
                        💾 Save Draft
                    </button>
                </div>
            </div>
        </Overlay>
    );
};

const DeployModal = ({ nodes, edges, onDeploy, onClose }) => {
    const [name, setName] = useState(`slice-${Math.random().toString(36).slice(2, 6)}`);
    const totalRam = nodes.reduce((s, n) => s + n.ram, 0);
    const ramLabel = totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`;
    return (
        <Overlay>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16, padding: 28, maxWidth: 400, width: "90%", boxShadow: T.shadowMd }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4 }}>🚀 Deploy Slice</div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 20 }}>Name your slice and confirm deployment.</div>
                <Lbl>Slice Name</Lbl>
                <input value={name} onChange={e => setName(e.target.value)} style={inp} />
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, margin: "18px 0", background: T.accentLight, borderRadius: 10, padding: "14px 12px", border: `1px solid ${T.accent}33` }}>
                    {[["VMs", nodes.length], ["Links", edges.length], ["vCPU", nodes.reduce((s, n) => s + n.vcores, 0)], ["RAM", ramLabel]].map(([l, v]) => (
                        <div key={l} style={{ textAlign: "center" }}>
                            <div style={{ fontSize: 20, fontWeight: 900, color: T.accent }}>{v}</div>
                            <div style={{ fontSize: 9, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                        </div>
                    ))}
                </div>
                <div style={{ display: "flex", gap: 10 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancel</button>
                    <button onClick={() => onDeploy(name)} disabled={!name.trim() || nodes.length === 0}
                        style={btnBase({ flex: 2, background: T.accent, color: "#fff", border: "none", opacity: (!name.trim() || nodes.length === 0) ? 0.5 : 1 })}>
                        🚀 Deploy Now
                    </button>
                </div>
            </div>
        </Overlay>
    );
};

const ConfirmModal = ({ title, msg, onOk, onCancel }) => (
    <Overlay>
        <div style={{ background: T.surface, border: `1px solid ${T.red}33`, borderRadius: 14, padding: 26, maxWidth: 380, width: "90%", boxShadow: T.shadowMd }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: T.text, marginBottom: 8 }}>🗑️ {title}</div>
            <div style={{ fontSize: 13, color: T.textMuted, marginBottom: 22, lineHeight: 1.6 }}>{msg}</div>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <button onClick={onCancel} style={btnBase()}>Cancel</button>
                <button onClick={onOk} style={btnBase({ background: T.redLight, color: T.red, border: `1px solid ${T.red}44` })}>
                    💥 Yes, Destroy
                </button>
            </div>
        </div>
    </Overlay>
);

const ConsoleModal = ({ workerIp, vncPort, onClose }) => (
    <Overlay>
        <div style={{
            background: T.surface,
            border: `1px solid ${T.border}`,
            borderRadius: 16,
            padding: "20px",
            boxShadow: T.shadowMd,
            display: "flex",
            flexDirection: "column",
            gap: "10px"
        }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text }}>💻 Consola Interactiva VNC</div>
                <button onClick={onClose} style={btnBase({ background: T.redLight, color: T.red, padding: "5px 10px" })}>Cerrar</button>
            </div>

            {/* Aquí inyectamos tu componente VmConsole */}
            <VmConsole workerIp={workerIp} vncPort={vncPort} />
        </div>
    </Overlay>
);

const Toast = ({ msg, type }) => (
    <div style={{ position: "fixed", bottom: 24, right: 24, zIndex: 9999, background: type === "error" ? T.redLight : T.accentLight, border: `1px solid ${type === "error" ? T.red : T.accent}44`, color: type === "error" ? T.red : T.accent, padding: "10px 18px", borderRadius: 10, fontSize: 13, fontWeight: 600, boxShadow: T.shadowMd }}>
        {type === "error" ? "❌" : "✅"} {msg}
    </div>
);

// --- IMAGE PANEL COMPONENT ---------------------------------------------------
const ImagePanel = ({ fullImages, onRefresh, flash, refreshImageList }) => {
    const [uploading, setUploading] = useState(false);
    const [gcRunning, setGcRunning] = useState(false);
    const [uploadForm, setUploadForm] = useState({ name: "", file: null });
    const [showUpload, setShowUpload] = useState(false);
    const fileRef = useRef(null);

    const handleUpload = async () => {
        if (!uploadForm.file || !uploadForm.name.trim()) {
            flash("Completa el nombre y selecciona un archivo", "error"); return;
        }
        setUploading(true);
        const fd = new FormData();
        fd.append("name", uploadForm.name.trim());
        fd.append("is_general", 0);
        fd.append("file", uploadForm.file);
        try {
            const res = await fetch("http://localhost:8085/api/v1/slices/utils/images/upload", { method: "POST", body: fd });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al subir");
            flash(`Imagen '${uploadForm.name}' subida correctamente ✅`);
            setShowUpload(false);
            setUploadForm({ name: "", file: null });
            onRefresh();
            if (refreshImageList) refreshImageList();
        } catch (e) { flash(e.message, "error"); }
        finally { setUploading(false); }
    };

    const handleDelete = async (img) => {
        if (!window.confirm(`¿Eliminar la imagen '${img.name}'?`)) return;
        try {
            const res = await fetch(`http://localhost:8085/api/v1/slices/utils/images/${img.id}`, { method: "DELETE" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al eliminar");
            flash(data.message);
            onRefresh();
        } catch (e) { flash(e.message, "error"); }
    };

    const handleGC = async () => {
        setGcRunning(true);
        try {
            const res = await fetch("http://localhost:8085/api/v1/slices/utils/images/gc/run", { method: "POST" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error GC");
            flash(data.message);
            // El GC corre en background, esperamos un poco y refrescamos la lista
            setTimeout(() => {
                onRefresh();
                if (refreshImageList) refreshImageList();
            }, 3000);
        } catch (e) { flash(e.message, "error"); }
        finally { setTimeout(() => setGcRunning(false), 3500); }
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
            {/* GC + Upload buttons */}
            <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, display: "flex", gap: 6, flexShrink: 0 }}>
                <button onClick={() => setShowUpload(v => !v)}
                    style={btnBase({ flex: 1, fontSize: 11, padding: "6px 8px", background: T.accent, color: "#fff", border: "none" })}>
                    ⬆️ Subir Imagen
                </button>
                <button onClick={handleGC} disabled={gcRunning}
                    style={btnBase({ flex: 1, fontSize: 11, padding: "6px 8px", background: gcRunning ? T.surfaceElevated : T.yellowLight, color: gcRunning ? T.textMuted : T.yellow, border: `1px solid ${T.yellow}44` })}>
                    {gcRunning ? "🔄 Limpiando..." : "🧹 Ejecutar GC"}
                </button>
            </div>

            {/* Upload form (colapsable) */}
            {showUpload && (
                <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, background: T.accentLight, flexShrink: 0 }}>
                    <Lbl>Nombre de la imagen</Lbl>
                    <input value={uploadForm.name} onChange={e => setUploadForm(p => ({ ...p, name: e.target.value }))}
                        placeholder="ej: ubuntu-custom-v1" style={{ ...inp, marginBottom: 8 }} />
                    <Lbl>Archivo (.qcow2 / .img / .iso)</Lbl>
                    <input type="file" accept=".qcow2,.img,.iso" ref={fileRef}
                        onChange={e => setUploadForm(p => ({ ...p, file: e.target.files[0] }))}
                        style={{ fontSize: 11, color: T.text, marginBottom: 8, width: "100%" }} />
                    <button onClick={handleUpload} disabled={uploading}
                        style={btnBase({ width: "100%", background: T.accent, color: "#fff", border: "none", opacity: uploading ? 0.6 : 1 })}>
                        {uploading ? "Subiendo..." : "✅ Confirmar Subida"}
                    </button>
                </div>
            )}

            {/* Image list */}
            <div style={{ flex: 1, overflowY: "auto", padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
                {fullImages.length === 0 && (
                    <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>Sin imágenes registradas</div>
                )}
                {fullImages.map(img => (
                    <div key={img.id} style={{ background: T.surface, border: `1px solid ${img.in_use ? T.accent + "44" : T.border}`, borderRadius: 10, padding: "9px 10px", boxShadow: T.shadow }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 12, fontWeight: 700, color: T.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                    {img.is_general ? "🔒" : "📦"} {img.name}
                                </div>
                                <div style={{ fontSize: 10, color: T.textMuted, marginTop: 2 }}>
                                    {img.in_use
                                        ? <span style={{ color: T.accent, fontWeight: 600 }}>✅ En uso ({img.active_vm_count} VM{img.active_vm_count > 1 ? "s" : ""})</span>
                                        : <span style={{ color: img.is_general ? T.textMuted : T.yellow }}>⚪ Sin VMs activas</span>
                                    }
                                </div>
                                <div style={{ fontSize: 9, color: T.textFaint, marginTop: 2, fontFamily: "monospace" }}>{img.path}</div>
                            </div>
                            {img.is_general !== 1 && (
                                <button onClick={() => handleDelete(img)} disabled={img.in_use}
                                    title={img.in_use ? "No se puede eliminar: tiene VMs activas" : "Eliminar imagen"}
                                    style={{ ...btnBase({ padding: "4px 8px", fontSize: 13, background: img.in_use ? T.surfaceElevated : T.redLight, color: img.in_use ? T.textFaint : T.red, border: `1px solid ${img.in_use ? T.border : T.red + "44"}`, marginLeft: 6, cursor: img.in_use ? "not-allowed" : "pointer", flexShrink: 0 }) }}>
                                    🗑️
                                </button>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

// --- SEED DATA ----------------------------------------------------------------
const mkSlice = (name, status, nodes, edges) => {
    const totalRam = nodes.reduce((s, n) => s + n.ram, 0);
    return { id: `s${_nid++}`, name, status, nodes, edges, nodeCount: nodes.length, edgeCount: edges.length, vcpus: nodes.reduce((s, n) => s + n.vcores, 0), ramLabel: totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)}G` : `${totalRam}M` };
};
const refreshMeta = sl => {
    const totalRam = sl.nodes.reduce((s, n) => s + n.ram, 0);
    return { ...sl, nodeCount: sl.nodes.length, edgeCount: sl.edges.length, vcpus: sl.nodes.reduce((s, n) => s + n.vcores, 0), ramLabel: totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)}G` : `${totalRam}M` };
};

const s1 = buildLinear(4, 340, 200);
const s2 = buildRing(5, 300, 220);
const s3n = [mkNode(120, 160, "router-1"), mkNode(300, 160, "server-1"), mkNode(210, 290, "server-2")];
const s3e = [{ id: "es1", from: s3n[0].id, to: s3n[1].id }, { id: "es2", from: s3n[0].id, to: s3n[2].id }];

const INIT_SLICES = [
    mkSlice("lab-ospf-base", "Active", s1.nodes, s1.edges),
    mkSlice("draft-mesh", "Draft", s3n, s3e),
];

// --- ROOT ---------------------------------------------------------------------
export default function App() {
    const [slices, setSlices] = useState([]);
    // Dentro de export default function App() { ...
    const [imageList, setImageList] = useState(["Cargando imágenes..."]);
    const [activeId, setActiveId] = useState(null);
    const [sidebarTab, setSidebarTab] = useState("slices");
    const [fullImages, setFullImages] = useState([]);

    const fetchFullImages = async () => {
        try {
            const res = await fetch("http://localhost:8085/api/v1/slices/utils/images/");
            if (res.ok) setFullImages(await res.json());
        } catch (e) { console.error("Error cargando imágenes completas:", e); }
    };

    useEffect(() => {
        const fetchSlices = async () => {
            try {
                const res = await fetch("http://localhost:8085/api/v1/slices");
                if (res.ok) setSlices(await res.json());
            } catch (error) { console.error("Error cargando slices:", error); }
        };
        const fetchImages = async () => {
            try {
                const res = await fetch("http://localhost:8085/api/v1/slices/utils/images");
                if (res.ok) setImageList(await res.json());
            } catch (error) { console.error("Error cargando imágenes:", error); }
        };
        fetchSlices();
        fetchImages();
        fetchFullImages();

        // Polling: actualizar slices cada 8s para reflejar cambios de estado en tiempo real
        const interval = setInterval(async () => {
            try {
                const res = await fetch("http://localhost:8085/api/v1/slices");
                if (res.ok) setSlices(await res.json());
            } catch (_) { /* silencioso */ }
        }, 8000);
        return () => clearInterval(interval);
    }, []);


    // Designer state (for new slices)
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);

    // Modals
    const [modal, setModal] = useState(null); // null | "deploy" | "draft" | { type:"confirm", ... }
    const [toast, setToast] = useState(null);
    const [consoleVm, setConsoleVm] = useState(null);
    const flash = (msg, type = "success") => { setToast({ msg, type }); setTimeout(() => setToast(null), 3000); };

    const activeSlice = slices.find(s => s.id === activeId) || null;

    // -- Slice CRUD --------------------------------------------------------------
    const updateSlice = (id, patch) =>
        setSlices(p => p.map(s => s.id === id ? refreshMeta({ ...s, ...patch }) : s));

    const destroySlice = (id) => {
        const sl = slices.find(s => s.id === id);
        setModal({
            type: "confirm",
            title: "Destroy Slice",
            msg: `Are you sure you want to destroy "${sl?.name}"?`,
            onOk: async () => {
                try {
                    const res = await fetch(`http://localhost:8085/api/v1/slices/${id}`, { method: "DELETE" });
                    if (!res.ok) throw new Error("Fallo al destruir");

                    const data = await res.json();

                    if (data.status === "DELETED") {
                        // Era un draft, se borró de la BD. Lo quitamos de la lista.
                        setSlices(prev => prev.filter(s => s.id !== id));
                        if (activeId === id) setActiveId(null);
                    } else {
                        // Era físico, NATS lo está matando. Lo pasamos a TERMINATED.
                        updateSlice(id, { status: "TERMINATED" });
                    }

                    setModal(null);
                    flash(data.message);
                } catch (e) {
                    flash("Error al eliminar", "error");
                }
            }
        });
    };

    const deployFromDesigner = async (name) => {
        try {
            // 🔥 PRE-PROCESAMIENTO: Si alguna VM no tiene imagen, le asignamos la primera de la lista
            const defaultImg = imageList.length > 0 ? imageList[0] : { id: 1, name: "Cirros" };
            const processedNodes = nodes.map(n => ({
                ...n,
                image_id: n.image_id || defaultImg.id,
                image: n.image || defaultImg.name
            }));
            // Usamos processedNodes en lugar de nodes
            const payloadDraft = {
                name: name,
                slice_json: { nodes: processedNodes, edges: edges }
            };

            const resDraft = await fetch("http://localhost:8085/api/v1/slices/draft", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payloadDraft)
            });

            if (!resDraft.ok) throw new Error("Fallo al guardar la topología en BD");
            const draftData = await resDraft.json();
            const newSliceId = draftData.slice_id;

            // PASO 2: Mandamos a desplegar ese ID que acabamos de crear
            const payloadDeploy = {
                availability_zone: "Linux Cluster", // Opcional: hacerlo dinámico luego
                ttl_hours: 4,
                motivo: "Despliegue directo desde Canvas"
            };

            const resDeploy = await fetch(`http://localhost:8085/api/v1/slices/${newSliceId}/deploy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payloadDeploy)
            });

            if (!resDeploy.ok) throw new Error("Fallo al solicitar el despliegue");

            // PASO 3: Actualizamos la UI
            const sl = mkSlice(name, "PENDING_APPROVAL", [...nodes], [...edges]);
            sl.id = newSliceId;
            setSlices(p => [sl, ...p]);
            setNodes([]); setEdges([]);
            setModal(null);
            flash(`"${name}" enviado a validación de recursos!`);

        } catch (error) {
            flash("Error de conexión con el servidor", "error");
        }
    };

    // Save draft from designer
    const saveDraft = async (name) => {
        try {
            // 🔥 PRE-PROCESAMIENTO IDÉNTICO
            const defaultImg = imageList.length > 0 ? imageList[0] : { id: 1, name: "Cirros" };
            const processedNodes = nodes.map(n => ({
                ...n,
                image_id: n.image_id || defaultImg.id,
                image: n.image || defaultImg.name
            }));
            const payload = {
                name: name,
                slice_json: { nodes: processedNodes, edges: edges }
            };

            const response = await fetch("http://localhost:8085/api/v1/slices/draft", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!response.ok) throw new Error("Fallo al guardar el borrador");

            const data = await response.json();

            // 3. Actualizamos la vista local
            const sl = mkSlice(name, "Draft", [...nodes], [...edges]);
            sl.id = data.slice_id; // Usamos el ID real de la BD!
            setSlices(p => [sl, ...p]);
            setNodes([]); setEdges([]);
            setModal(null);
            flash(`"${name}" saved as draft`);

        } catch (error) {
            flash("Error de conexión con el servidor", "error");
        }
    };

    const deployDraft = async (id) => {
        try {
            const payload = {
                availability_zone: "Linux Cluster", // O saca esto del modal
                ttl_hours: 4,
                motivo: "Despliegue desde la UI"
            };

            const res = await fetch(`http://localhost:8085/api/v1/slices/${id}/deploy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!res.ok) throw new Error("Fallo al desplegar");

            updateSlice(id, { status: "PENDING_APPROVAL" }); // Actualiza la UI
            flash("Solicitud de despliegue encolada con éxito");
        } catch (error) {
            flash("Error al solicitar despliegue", "error");
        }
    };

    // -- Exportar / Importar Topologías ------------------------------------------
    const exportarTopologia = () => {
        if (nodes.length === 0) {
            flash("No hay nodos para exportar", "error");
            return;
        }
        const dataActual = {
            vms: nodes,
            edges: edges
        };
        const dataStr = JSON.stringify(dataActual, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);

        const link = document.createElement("a");
        link.href = url;
        link.download = `topologia_pucp_${new Date().getTime()}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        flash("Topología exportada exitosamente");
    };

    const importarTopologia = (event) => {
        const file = event.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const jsonImportado = JSON.parse(e.target.result);
                if (jsonImportado.vms && Array.isArray(jsonImportado.vms)) {
                    // Generamos un sufijo único para evitar colisiones de IDs (ej. n1_imp_4012)
                    const suffix = `_imp_${new Date().getTime().toString().slice(-4)}`;
                    const idMap = {};

                    const newNodes = jsonImportado.vms.map(n => {
                        const newId = `${n.id}${suffix}`;
                        idMap[n.id] = newId;
                        // Desplazamos un poco las X y Y para que no se superpongan exactamente encima de las actuales
                        return { ...n, id: newId, x: (n.x || 0) + 50, y: (n.y || 0) + 50 };
                    });

                    const newEdges = (jsonImportado.edges || []).map(edge => ({
                        ...edge,
                        id: `${edge.id}${suffix}`,
                        from: idMap[edge.from] || edge.from,
                        to: idMap[edge.to] || edge.to
                    }));

                    // MAGIA: Usamos el estado previo (prev) y le SUMAMOS los nuevos, en lugar de reemplazarlos
                    setNodes(prev => [...prev, ...newNodes]);
                    setEdges(prev => [...prev, ...newEdges]);
                    flash("Topología importada y añadida al lienzo actual");
                } else {
                    throw new Error("Formato inválido");
                }
            } catch (error) {
                console.error("Error al leer el JSON:", error);
                flash("El archivo no es una topología válida.", "error");
            }
        };
        event.target.value = null;
        reader.readAsText(file);
    };

    // Setters that update in-place when viewing a slice
    const setSliceNodes = fn => setSlices(p => p.map(s => s.id === activeId ? refreshMeta({ ...s, nodes: typeof fn === "function" ? fn(s.nodes) : fn }) : s));
    const setSliceEdges = fn => setSlices(p => p.map(s => s.id === activeId ? refreshMeta({ ...s, edges: typeof fn === "function" ? fn(s.edges) : fn }) : s));

    return (
        <div style={{ display: "flex", height: "100vh", background: T.bg, fontFamily: "'DM Sans','Segoe UI',sans-serif", color: T.text, overflow: "hidden" }}>

            {/* -- SIDEBAR ---------------------------------------------------- */}
            <div style={{ width: 268, background: T.surface, borderRight: `1px solid ${T.border}`, display: "flex", flexDirection: "column", flexShrink: 0, boxShadow: "2px 0 10px rgba(20,50,22,0.07)" }}>

                {/* Logo */}
                <div style={{ padding: "16px 16px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 38, height: 38, borderRadius: 10, background: T.accentLight, border: `1.5px solid ${T.accent}44`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22 }}>☁️</div>
                    <div>
                        <div style={{ fontSize: 15, fontWeight: 800, color: T.text, letterSpacing: "-0.02em" }}>PUCP Cloud</div>
                        <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: "0.08em", textTransform: "uppercase" }}>Orchestrator</div>
                    </div>
                </div>

                {/* VM palette — only show in designer mode */}
                {!activeSlice && (
                    <div style={{ padding: "14px 16px 12px", borderBottom: `1px solid ${T.border}` }}>
                        <Lbl>Drag VM to canvas</Lbl>
                        <div draggable onDragStart={e => e.dataTransfer.setData("nodeType", "vm")}
                            style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", background: T.surfaceElevated, border: `1.5px dashed ${T.borderHover}`, borderRadius: 10, cursor: "grab" }}>
                            <span style={{ fontSize: 22 }}>🖥️</span>
                            <div>
                                <div style={{ fontSize: 12, fontWeight: 700, color: T.text }}>Virtual Machine</div>
                                <div style={{ fontSize: 10, color: T.textMuted }}>Configurable node</div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Templates — only in designer mode */}
                {!activeSlice && <TemplatePicker />}

                {/* Slices / Images tab bar */}
                <div style={{ display: "flex", borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
                    {[["slices", "📋 Slices"], ["images", "🖼️ Imágenes"]].map(([id, label]) => (
                        <button key={id} onClick={() => setSidebarTab(id)}
                            style={{ flex: 1, padding: "9px 4px", fontSize: 11, fontWeight: 700, fontFamily: "inherit",
                                background: sidebarTab === id ? T.accentLight : "transparent",
                                color: sidebarTab === id ? T.accent : T.textMuted,
                                border: "none", borderBottom: sidebarTab === id ? `2px solid ${T.accent}` : "2px solid transparent",
                                cursor: "pointer", transition: "all 0.15s" }}>
                            {label}
                        </button>
                    ))}
                </div>

                {/* Slices tab */}
                {sidebarTab === "slices" && (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                    <div style={{ padding: "12px 16px 8px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
                        <Lbl style={{ marginBottom: 0 }}>My Slices <span style={{ color: T.accent, marginLeft: 5 }}>{slices.length}</span></Lbl>
                        <button onClick={() => setActiveId(null)}
                            style={btnBase({ padding: "4px 10px", fontSize: 10, background: T.accentLight, color: T.accent, border: `1px solid ${T.accent}44`, boxShadow: "none" })}>
                            + New
                        </button>
                    </div>
                    <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                        {slices.map(sl => (
                            <SliceCard key={sl.id} slice={sl} active={activeId === sl.id}
                                onClick={() => setActiveId(sl.id)}
                                onDestroy={destroySlice}
                                onDeploy={deployDraft} />
                        ))}
                        {slices.length === 0 && <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>No slices yet</div>}
                    </div>
                </div>
                )}

                {/* Images tab */}
                {sidebarTab === "images" && (
                <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                    <ImagePanel fullImages={fullImages} onRefresh={fetchFullImages} flash={flash}
                        refreshImageList={async () => {
                            try {
                                const res = await fetch("http://localhost:8085/api/v1/slices/utils/images");
                                if (res.ok) setImageList(await res.json());
                            } catch (_) {}
                        }}
                    />
                </div>
                )}
            </div>

            {/* -- MAIN ------------------------------------------------------- */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

                {/* Topbar */}
                <div style={{ padding: "0 20px", height: 54, borderBottom: `1px solid ${T.border}`, background: T.surface, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, boxShadow: "0 2px 8px rgba(20,50,22,0.05)" }}>
                    {activeSlice ? (
                        <>
                            <button onClick={() => setActiveId(null)} style={btnBase({ padding: "5px 12px", fontSize: 11, boxShadow: "none" })}>⬅ Back</button>
                            <div style={{ width: 1, height: 22, background: T.border }} />
                            <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>{activeSlice.name}</span>
                            <Badge status={activeSlice.status} />
                            <div style={{ flex: 1 }} />

                            {activeSlice.status === "DRAFT" && (
                                <button onClick={() => deployDraft(activeSlice.id)}
                                    style={btnBase({ fontSize: 12, padding: "6px 16px", background: T.accent, color: "#fff", border: "none", boxShadow: `0 3px 12px ${T.accent}44` })}>
                                    🚀 Deploy
                                </button>
                            )}

                            {/* 🔥 FIX: Ocultamos el Destroy de la barra principal si está muerto */}
                            {activeSlice.status !== "TERMINATED" && (
                                <button onClick={() => destroySlice(activeSlice.id)}
                                    style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.redLight, color: T.red, border: `1px solid ${T.red}33`, boxShadow: "none" })}>
                                    💥 Destroy
                                </button>
                            )}
                        </>
                    ) : (
                        <>
                            <span style={{ fontSize: 14, fontWeight: 700, color: T.text }}>Topology Designer</span>
                            <span style={{ fontSize: 11, color: T.textMuted }}>— New Slice</span>

                            <div style={{ flex: 1 }} />

                            {/* NUEVOS BOTONES DE IMPORTAR/EXPORTAR */}
                            <label style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surface, color: T.text, boxShadow: "none", cursor: "pointer" })}>
                                ⬆️ Importar
                                <input type="file" accept=".json" style={{ display: 'none' }} onChange={importarTopologia} />
                            </label>

                            {nodes.length > 0 && (
                                <button onClick={exportarTopologia} style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surface, color: T.text, boxShadow: "none" })}>
                                    ⬇️ Exportar
                                </button>
                            )}
                            <div style={{ width: 1, height: 22, background: T.border, margin: "0 4px" }} />

                            {nodes.length > 0 && (
                                <button onClick={() => setModal("draft")}
                                    style={btnBase({ fontSize: 12, padding: "6px 14px", background: T.surfaceElevated, color: T.textMuted, boxShadow: "none" })}>
                                    💾 Save Draft
                                </button>
                            )}
                            <button onClick={() => nodes.length > 0 ? setModal("deploy") : flash("Add at least one VM first", "error")}
                                style={btnBase({ fontSize: 13, fontWeight: 700, padding: "7px 20px", background: T.accent, color: "#fff", border: "none", boxShadow: `0 4px 16px ${T.accent}44` })}>
                                🚀 Deploy Slice
                            </button>
                        </>
                    )}
                </div>

                {/* Canvas */}
                <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                    {activeSlice
                        ? <Canvas nodes={activeSlice.nodes} edges={activeSlice.edges} setNodes={setSliceNodes} setEdges={setSliceEdges} imageList={imageList} activeSlice={activeSlice} onOpenConsole={setConsoleVm} />
                        : <Canvas nodes={nodes} edges={edges} setNodes={setNodes} setEdges={setEdges} imageList={imageList} activeSlice={activeSlice} onOpenConsole={setConsoleVm} />
                    }
                </div>
            </div>

            {/* -- MODALS ----------------------------------------------------- */}
            {modal === "deploy" && <DeployModal nodes={nodes} edges={edges} onDeploy={deployFromDesigner} onClose={() => setModal(null)} />}
            {modal === "draft" && <SaveDraftModal nodes={nodes} edges={edges} onSave={saveDraft} onClose={() => setModal(null)} />}
            {modal?.type === "confirm" && <ConfirmModal title={modal.title} msg={modal.msg} onOk={modal.onOk} onCancel={() => setModal(null)} />}

            {/* 🔥 RENDERIZAMOS LA CONSOLA SI HAY UNA VM SELECCIONADA */}
            {consoleVm && (
                <ConsoleModal
                    workerIp={consoleVm.workerIp}
                    vncPort={consoleVm.vncPort}
                    onClose={() => setConsoleVm(null)}
                />
            )}

            {toast && <Toast {...toast} />}

            <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: ${T.surfaceElevated}; }
        ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 4px; }
        input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
        input:focus, select:focus { border-color: ${T.accentMid} !important; }
      `}</style>
        </div>
    );
}