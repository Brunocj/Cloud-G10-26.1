import { useState, useRef } from "react";
import { T, btnBase } from "../../theme/tokens";
import { buildLinear, buildRing, buildMesh, buildTree, buildBus, mkNode } from "../../utils/topology";
import { NodeEditor } from "./NodeEditor";
import { MousePointer2, Link2, Monitor, Trash2, Maximize2, AlertTriangle, ZoomIn, ZoomOut } from "../ui/Icon";
import { AzureVm, AzureNetwork, UbuntuLogo, WindowsLogo } from "../ui/AzureIcons";

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Build interface label map: ens3 = management; data links start at ens4. */
const buildIfaceMap = (edges) => {
    const cnt = {}, map = {};
    edges.forEach(ed => {
        const fi = cnt[ed.from] ?? 0, ti = cnt[ed.to] ?? 0;
        map[ed.id] = { fromIface: `ens${4 + fi}`, toIface: `ens${4 + ti}` };
        cnt[ed.from] = fi + 1;
        cnt[ed.to]   = ti + 1;
    });
    return map;
};

// ─── Canvas ──────────────────────────────────────────────────────────────────
export const Canvas = ({ nodes, edges, setNodes, setEdges, imageList, activeSlice, onOpenConsole, targetAz, setTargetAz, apiFetch, onCleared }) => {
    const svgRef   = useRef();
    const groupRef = useRef();           // root <g> — updated imperatively during pan/zoom

    const [mode,     setMode]     = useState("select");
    const [linkFrom, setLinkFrom] = useState(null);
    const [mouse,    setMouse]    = useState({ x: 0, y: 0 }); // world coords, for link preview
    const [editId,   setEditId]   = useState(null);

    // Pan and Zoom – stored in refs for zero-latency imperative updates;
    // committed to state only on pointerup/wheel/buttons so React can re-render consistently.
    const panRef  = useRef({ x: 0, y: 0 });
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const zoomRef = useRef(1);
    const [zoom, setZoom] = useState(1);

    // Active drag refs (no state → no re-renders mid-drag)
    const nodeDrag = useRef(null); // { id, startNodeX, startNodeY, startWorldX, startWorldY }
    const nodePositionsRef = useRef({}); // maps node.id -> {x, y} during drag
    const bgDrag   = useRef(null); // { startScreenX, startScreenY, startPanX, startPanY }
    const lastTap  = useRef({ id: null, t: 0 });
    const cursor   = useRef("default"); // updated imperatively

    // ── Coordinate helpers ───────────────────────────────────────────────────

    /** Raw screen coords relative to the SVG bounding rect. */
    const toScreen = (e) => {
        const r = svgRef.current.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    /** Convert a screen point to world (canvas) coordinates. */
    const toWorld = (e) => {
        const s = toScreen(e);
        return {
            x: (s.x - panRef.current.x) / zoomRef.current,
            y: (s.y - panRef.current.y) / zoomRef.current
        };
    };

    /** Apply transform imperatively (avoids React re-render during drag). */
    const applyTransform = (x, y, z) => {
        panRef.current = { x, y };
        zoomRef.current = z;
        if (groupRef.current) {
            groupRef.current.setAttribute("transform", `translate(${x},${y}) scale(${z})`);
        }
    };

    /** Update connected edges imperatively during drag */
    const updateEdgeCoords = (edgeId) => {
        const edge = edges.find(e => e.id === edgeId);
        if (!edge) return;
        const A = nodePositionsRef.current[edge.from];
        const B = nodePositionsRef.current[edge.to];
        if (!A || !B) return;
        
        const lines = document.querySelectorAll(`[data-edge-id="${edgeId}"]`);
        lines.forEach(line => {
            line.setAttribute("x1", A.x);
            line.setAttribute("y1", A.y);
            line.setAttribute("x2", B.x);
            line.setAttribute("y2", B.y);
        });

        const labelsGroup = document.getElementById(`edge-labels-${edgeId}`);
        if (labelsGroup) {
            const fx = A.x + 0.28 * (B.x - A.x);
            const fy = A.y + 0.28 * (B.y - A.y);
            const tx = A.x + 0.72 * (B.x - A.x);
            const ty = A.y + 0.72 * (B.y - A.y);
            
            const fromRect = labelsGroup.querySelector('.label-from-rect');
            const fromText = labelsGroup.querySelector('.label-from-text');
            const toRect = labelsGroup.querySelector('.label-to-rect');
            const toText = labelsGroup.querySelector('.label-to-text');

            if (fromRect) {
                fromRect.setAttribute("x", fx - 17);
                fromRect.setAttribute("y", fy - 8);
            }
            if (fromText) {
                fromText.setAttribute("x", fx);
                fromText.setAttribute("y", fy + 0.5);
            }
            if (toRect) {
                toRect.setAttribute("x", tx - 17);
                toRect.setAttribute("y", ty - 8);
            }
            if (toText) {
                toText.setAttribute("x", tx);
                toText.setAttribute("y", ty + 0.5);
            }
        }
    };

    /** Commit transform to React state (triggers re-render once, on pointer-up/wheel). */
    const commitTransform = () => {
        setPan({ ...panRef.current });
        setZoom(zoomRef.current);
    };

    // ── Hit test ─────────────────────────────────────────────────────────────

    /** Return the topmost node under a world-space point, or null. */
    const hitNode = (world) => {
        for (let i = nodes.length - 1; i >= 0; i--) {
            const n = nodes[i];
            if (Math.abs(world.x - n.x) <= 30 && Math.abs(world.y - n.y) <= 28) return n;
        }
        return null;
    };

    // ── Pointer events ────────────────────────────────────────────────────────

    const onPointerDown = (e) => {
        // Only react to primary (left) button
        if (e.button !== 0) return;
        e.preventDefault(); // prevents text selection highlight

        const world  = toWorld(e);
        const screen = toScreen(e);
        const hit    = hitNode(world);

        // ── LINK mode ──────────────────────────────────────────────────────
        if (mode === "link") {
            if (!hit)                { setLinkFrom(null); return; }
            if (!linkFrom)           { setLinkFrom(hit.id); return; }
            if (hit.id === linkFrom) { setLinkFrom(null); return; }
            const dup = edges.find(ed =>
                (ed.from === linkFrom && ed.to === hit.id) ||
                (ed.from === hit.id   && ed.to === linkFrom)
            );
            if (!dup) setEdges(prev => [...prev, { id: `e${Date.now()}`, from: linkFrom, to: hit.id }]);
            setLinkFrom(null);
            return;
        }

        // ── SELECT mode ────────────────────────────────────────────────────
        if (!hit) {
            // Click on background → clear edit & start pan
            setEditId(null);
            bgDrag.current = {
                startScreenX: screen.x, startScreenY: screen.y,
                startPanX: panRef.current.x, startPanY: panRef.current.y,
            };
            svgRef.current.setPointerCapture(e.pointerId);
            svgRef.current.style.cursor = "grabbing";
            return;
        }

        // Double-click detection (< 380 ms on same node)
        const now = Date.now();
        if (lastTap.current.id === hit.id && now - lastTap.current.t < 380) {
            lastTap.current = { id: null, t: 0 };
            setEditId(hit.id);
            return;
        }
        lastTap.current = { id: hit.id, t: now };

        // If another node is already being edited, switch to this one immediately
        if (editId && editId !== hit.id) {
            setEditId(hit.id);
            return;
        }

        // Initialize node positions reference
        nodePositionsRef.current = {};
        nodes.forEach(n => {
            nodePositionsRef.current[n.id] = { x: n.x, y: n.y };
        });

        // Start node drag
        nodeDrag.current = {
            id: hit.id,
            startNodeX: hit.x, startNodeY: hit.y,
            startWorldX: world.x, startWorldY: world.y,
        };
        svgRef.current.setPointerCapture(e.pointerId);
        svgRef.current.style.cursor = "grabbing";
    };

    const onPointerMove = (e) => {
        const world  = toWorld(e);
        const screen = toScreen(e);

        // Update mouse for link preview line
        setMouse(world);

        // ── Node drag ──────────────────────────────────────────────────────
        if (nodeDrag.current) {
            const { id, startNodeX, startNodeY, startWorldX, startWorldY } = nodeDrag.current;
            const newX = startNodeX + world.x - startWorldX;
            const newY = startNodeY + world.y - startWorldY;
            
            // 1. Update mutable ref position
            nodePositionsRef.current[id] = { x: newX, y: newY };
            
            // 2. Update node DOM element transform
            const nodeEl = document.getElementById(`node-${id}`);
            if (nodeEl) {
                nodeEl.setAttribute("transform", `translate(${newX},${newY})`);
            }
            
            // 3. Update all connected edges and their labels
            edges.forEach(ed => {
                if (ed.from === id || ed.to === id) {
                    updateEdgeCoords(ed.id);
                }
            });
            return;
        }

        // ── Background pan ─────────────────────────────────────────────────
        if (bgDrag.current) {
            const { startScreenX, startScreenY, startPanX, startPanY } = bgDrag.current;
            const newX = startPanX + (screen.x - startScreenX);
            const newY = startPanY + (screen.y - startScreenY);
            applyTransform(newX, newY, zoomRef.current);   // imperative: no React re-render mid-drag
        }
    };

    const onPointerUp = () => {
        if (nodeDrag.current) {
            const { id } = nodeDrag.current;
            const finalPos = nodePositionsRef.current[id];
            if (finalPos) {
                setNodes(prev => prev.map(n =>
                    n.id === id ? { ...n, x: finalPos.x, y: finalPos.y } : n
                ));
            }
            nodeDrag.current = null;
        }
        if (bgDrag.current) {
            bgDrag.current = null;
            commitTransform();            // one React re-render to sync state
        }
        if (svgRef.current) {
            svgRef.current.style.cursor = mode === "link" ? "crosshair" : "default";
        }
    };

    /** Prevent browser context menu on right-click inside the canvas. */
    const onContextMenu = (e) => e.preventDefault();

    // ── Drop from sidebar ─────────────────────────────────────────────────────

    const onDrop = (e) => {
        e.preventDefault();
        const world    = toWorld(e);
        const tplType  = e.dataTransfer.getData("templateType");
        const tplCount = Number(e.dataTransfer.getData("templateCount") || 0);
        if (tplType && tplCount) {
            const builders = { linear: buildLinear, ring: buildRing, mesh: buildMesh, tree: buildTree, bus: buildBus };
            const build = builders[tplType];
            if (build) {
                const built = build(tplCount, world.x, world.y);
                setNodes(prev => [...prev, ...built.nodes]);
                setEdges(prev => [...prev, ...built.edges]);
            }
            return;
        }
        if (e.dataTransfer.getData("nodeType")) {
            setNodes(prev => [...prev, mkNode(world.x, world.y, undefined, imageList[0])]);
        }
    };

    // ── Edge delete (inside edge's onPointerDown) ─────────────────────────────

    const onEdgePointerDown = (e, edgeId) => {
        if (e.button !== 0) return;
        e.preventDefault();
        const world = toWorld(e);
        if (!hitNode(world)) {
            e.stopPropagation();
            setEdges(prev => prev.filter(x => x.id !== edgeId));
        }
    };

    // ── Mode switch ───────────────────────────────────────────────────────────

    const switchMode = (m) => {
        setMode(m);
        setLinkFrom(null);
        setEditId(null);
        if (svgRef.current) svgRef.current.style.cursor = m === "link" ? "crosshair" : "default";
    };

    // ── Interactive Zoom Handler ──────────────────────────────────────────────

    const onWheel = (e) => {
        if (nodeDrag.current || bgDrag.current) return;
        e.preventDefault();
        const s = toScreen(e);
        const currentZoom = zoomRef.current;
        const currentPan = panRef.current;
        
        const worldX = (s.x - currentPan.x) / currentZoom;
        const worldY = (s.y - currentPan.y) / currentZoom;
        
        const zoomFactor = 1.15;
        const nextZoom = e.deltaY < 0 
            ? Math.min(3.0, currentZoom * zoomFactor) 
            : Math.max(0.3, currentZoom / zoomFactor);
            
        const nextPanX = s.x - worldX * nextZoom;
        const nextPanY = s.y - worldY * nextZoom;
        
        applyTransform(nextPanX, nextPanY, nextZoom);
        commitTransform();
    };

    // ── Derived values ────────────────────────────────────────────────────────

    const editingNode  = editId   ? nodes.find(n => n.id === editId)   : null;
    const linkFromNode = linkFrom ? nodes.find(n => n.id === linkFrom) : null;
    const totalRam     = nodes.reduce((s, n) => s + n.ram, 0);
    const ifaceMap     = buildIfaceMap(edges);

    // ── Filter Images based on Target AZ ──────────────────────────────────────
    const availableImages = imageList.filter(img => {
        if (!targetAz) return true; // "Cualquiera"
        return img.availability_zone_id == targetAz;
    });

    // Zona efectiva del slice: la del slice ya desplegado, o la elegida en el toolbar (borrador)
    const effectiveZoneId = activeSlice ? activeSlice.availability_zone_id : targetAz;

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative",
            userSelect: "none", WebkitUserSelect: "none" }}>

            {/* ── Mode bar ── */}
            <div style={{ padding: "8px 14px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated,
                display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                {[  ["select", <MousePointer2 size={12} />, "Seleccionar y Mover"],
                    ["link",   <Link2 size={12} />,          "Enlazar Nodos"],
                ].map(([m, icon, lbl]) => (
                    <button key={m} onClick={() => switchMode(m)}
                        style={btnBase({
                            padding: "5px 13px", fontSize: 11, boxShadow: "none",
                            background: mode === m ? T.accentLight : T.surface,
                            color:      mode === m ? T.accent      : T.textMuted,
                            border: `1px solid ${mode === m ? T.accent + "66" : T.border}`,
                            display: "flex", alignItems: "center", gap: 6,
                        })}>
                        {icon} {lbl}
                    </button>
                ))}

                {mode === "link" && (
                    <span style={{ fontSize: 11, color: T.accent, background: T.accentLight,
                        padding: "4px 12px", borderRadius: 6, border: `1px solid ${T.accent}44` }}>
                        {linkFrom
                            ? "Haga clic en el nodo de destino para conectar — o en el fondo para cancelar"
                            : "Haga clic en el nodo de origen para comenzar el enlace"}
                    </span>
                )}
                {mode === "select" && (
                    <span style={{ fontSize: 11, color: T.textMuted }}>
                        Arrastre el nodo para moverlo · Arrastre el fondo para desplazar · Doble clic para editar · Clic en enlace para eliminar
                    </span>
                )}

                <div style={{ flex: 1 }} />

                {/* Target AZ Selector — se bloquea una vez hay nodos, para evitar incompatibilidades de imagen */}
                <div style={{ display: "flex", alignItems: "center", gap: 6, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "2px 8px", marginRight: 4 }}
                    title={nodes.length > 0 ? "Limpia el lienzo para poder cambiar la Zona Objetivo" : ""}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: T.textMuted, textTransform: "uppercase" }}>Zona Objetivo:</span>
                    <select
                        value={targetAz || ""}
                        disabled={nodes.length > 0}
                        onChange={(e) => setTargetAz(e.target.value)}
                        style={{ ...btnBase({ boxShadow: "none" }), background: "transparent", border: "none", color: T.accent, fontSize: 11, fontWeight: 700, padding: "2px", cursor: nodes.length > 0 ? "not-allowed" : "pointer", outline: "none", opacity: nodes.length > 0 ? 0.6 : 1 }}
                    >
                        <option value="">Cualquiera</option>
                        <option value="1">Linux Cluster</option>
                        <option value="2">OpenStack (Cloud)</option>
                    </select>
                </div>

                {/* Controles de Zoom */}
                <div style={{ display: "flex", alignItems: "center", gap: 4, marginRight: 4, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "2px" }}>
                    <button
                        title="Acercar"
                        onClick={() => {
                            const nextZoom = Math.min(3.0, zoomRef.current * 1.25);
                            if (svgRef.current) {
                                const rect = svgRef.current.getBoundingClientRect();
                                const cx = rect.width / 2;
                                const cy = rect.height / 2;
                                const worldX = (cx - panRef.current.x) / zoomRef.current;
                                const worldY = (cy - panRef.current.y) / zoomRef.current;
                                const nextPanX = cx - worldX * nextZoom;
                                const nextPanY = cy - worldY * nextZoom;
                                applyTransform(nextPanX, nextPanY, nextZoom);
                                commitTransform();
                            } else {
                                applyTransform(panRef.current.x, panRef.current.y, nextZoom);
                                commitTransform();
                            }
                        }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: "4px 8px", display: "flex", alignItems: "center" }}
                        onMouseEnter={e => e.currentTarget.style.color = T.accent}
                        onMouseLeave={e => e.currentTarget.style.color = T.textMuted}
                    >
                        <ZoomIn size={14} />
                    </button>
                    <span style={{ fontSize: 10, color: T.textMuted, minWidth: 36, textAlign: "center", fontWeight: 700 }}>
                        {Math.round(zoom * 100)}%
                    </span>
                    <button
                        title="Alejar"
                        onClick={() => {
                            const nextZoom = Math.max(0.3, zoomRef.current / 1.25);
                            if (svgRef.current) {
                                const rect = svgRef.current.getBoundingClientRect();
                                const cx = rect.width / 2;
                                const cy = rect.height / 2;
                                const worldX = (cx - panRef.current.x) / zoomRef.current;
                                const worldY = (cy - panRef.current.y) / zoomRef.current;
                                const nextPanX = cx - worldX * nextZoom;
                                const nextPanY = cy - worldY * nextZoom;
                                applyTransform(nextPanX, nextPanY, nextZoom);
                                commitTransform();
                            } else {
                                applyTransform(panRef.current.x, panRef.current.y, nextZoom);
                                commitTransform();
                            }
                        }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: "4px 8px", display: "flex", alignItems: "center" }}
                        onMouseEnter={e => e.currentTarget.style.color = T.accent}
                        onMouseLeave={e => e.currentTarget.style.color = T.textMuted}
                    >
                        <ZoomOut size={14} />
                    </button>
                </div>

                {/* Restablecer Vista */}
                <button
                    title="Restablecer vista"
                    onClick={() => { applyTransform(0, 0, 1); commitTransform(); }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 10px", color: T.textMuted, border: `1px solid ${T.border}`,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Maximize2 size={12} /> Restablecer Vista
                </button>

                <button
                    onClick={() => {
                        if (window.confirm("¿Estás seguro de limpiar todo el lienzo? Perderás el trabajo no guardado.")) {
                            setNodes([]); setEdges([]); setLinkFrom(null); setEditId(null);
                            if (onCleared) onCleared();
                        }
                    }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 12px",
                        color: T.red, border: `1px solid ${T.red}33`, background: T.redLight,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Trash2 size={12} /> Limpiar
                </button>
            </div>

            {/* ── SVG canvas ── */}
            <div style={{ flex: 1, position: "relative", overflow: "hidden" }}
                onDragOver={e => e.preventDefault()}
                onDrop={onDrop}>

                <svg
                    ref={svgRef}
                    width="100%" height="100%"
                    style={{ display: "block", touchAction: "none", cursor: mode === "link" ? "crosshair" : "default" }}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onContextMenu={onContextMenu}
                    onWheel={onWheel}
                >
                    {/* ── Background (static, outside pan group) ── */}
                    <defs>
                        {/* Grid dots shift with pan for infinite-canvas feel */}
                        <pattern id="dots2" width="24" height="24" patternUnits="userSpaceOnUse"
                            patternTransform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
                            <circle cx="0.8" cy="0.8" r="0.8" fill={T.border} />
                        </pattern>
                    </defs>
                    <rect width="100%" height="100%" fill={T.bg} />
                    <rect width="100%" height="100%" fill="url(#dots2)" />

                    {/* ── All canvas content inside a panned group ── */}
                    <g ref={groupRef} transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>

                        {/* Temp link line (world space) */}
                        {linkFromNode && (
                            <line x1={linkFromNode.x} y1={linkFromNode.y} x2={mouse.x} y2={mouse.y}
                                stroke={T.accentMid} strokeWidth="2" strokeDasharray="7,4" opacity="0.7"
                                style={{ pointerEvents: "none" }} />
                        )}

                        {/* Edges */}
                        {edges.map(ed => {
                            const A = nodes.find(n => n.id === ed.from);
                            const B = nodes.find(n => n.id === ed.to);
                            if (!A || !B) return null;
                            const iface = ifaceMap[ed.id] || {};
                            const fx = A.x + 0.28 * (B.x - A.x), fy = A.y + 0.28 * (B.y - A.y);
                            const tx = A.x + 0.72 * (B.x - A.x), ty = A.y + 0.72 * (B.y - A.y);
                            return (
                                <g key={ed.id} id={`edge-group-${ed.id}`}>
                                    {/* Visible line */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        data-edge-id={ed.id} data-from={ed.from} data-to={ed.to}
                                        stroke={T.accentMid} strokeWidth="2.5" opacity="0.5" strokeLinecap="round"
                                        style={{ pointerEvents: "none" }} />
                                    {/* Wide invisible hit area for delete */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        data-edge-id={ed.id} data-from={ed.from} data-to={ed.to}
                                        stroke="transparent" strokeWidth="18" strokeLinecap="round"
                                        style={{ cursor: "pointer" }}
                                        onPointerDown={ev => onEdgePointerDown(ev, ed.id)} />
                                    {/* Interface labels */}
                                    <g id={`edge-labels-${ed.id}`} style={{ pointerEvents: "none" }}>
                                        {iface.fromIface && (
                                            <g>
                                                <rect className="label-from-rect" x={fx - 17} y={fy - 8} width={34} height={14} rx={4}
                                                    fill={T.accentLight} stroke={T.accent + "66"} strokeWidth={1} />
                                                <text className="label-from-text" x={fx} y={fy + 0.5} textAnchor="middle" dominantBaseline="middle"
                                                    style={{ fontSize: 7.5, fill: T.accent, fontFamily: "monospace", fontWeight: 800 }}>
                                                    {iface.fromIface}
                                                </text>
                                            </g>
                                        )}
                                        {iface.toIface && (
                                            <g>
                                                <rect className="label-to-rect" x={tx - 17} y={ty - 8} width={34} height={14} rx={4}
                                                    fill={T.accentLight} stroke={T.accent + "66"} strokeWidth={1} />
                                                <text className="label-to-text" x={tx} y={ty + 0.5} textAnchor="middle" dominantBaseline="middle"
                                                    style={{ fontSize: 7.5, fill: T.accent, fontFamily: "monospace", fontWeight: 800 }}>
                                                    {iface.toIface}
                                                </text>
                                            </g>
                                        )}
                                    </g>
                                </g>
                            );
                        })}

                        {/* Nodes */}
                        {nodes.map(node => {
                            const isLinkSrc    = linkFrom === node.id;
                            const isEditing    = editId   === node.id;
                            const isLinkTarget = mode === "link" && linkFrom && linkFrom !== node.id;
                            const ram = node.ram >= 1024 ? `${node.ram / 1024}GB` : `${node.ram}MB`;
                            const isUbuntu = node.image?.toLowerCase().includes("ubuntu");
                            const isWindows = node.image?.toLowerCase().includes("win");

                            return (
                                <g key={node.id} id={`node-${node.id}`} transform={`translate(${node.x},${node.y})`}
                                    style={{ pointerEvents: "none" }}>
                                    {/* Drop shadow (subtle Azure style) */}
                                    <rect x="-36" y="-30" width="72" height="60" rx="6"
                                        fill="rgba(0,120,212,0.08)" transform="translate(1.5,2.5)" />
                                    {/* Card body */}
                                    <rect x="-36" y="-30" width="72" height="60" rx="6"
                                        fill={T.surface}
                                        stroke={isLinkSrc ? T.accent : isEditing ? T.accentMid : isLinkTarget ? T.accentMid + "88" : T.border}
                                        strokeWidth={isLinkSrc || isEditing ? 2 : 1.25} />
                                    
                                    {/* Accent top border highlight */}
                                    {(isLinkSrc || isEditing) && (
                                        <path d="M-30 -30 H30" stroke={T.accent} strokeWidth="2.5" strokeLinecap="round" />
                                    )}

                                    {/* Icon — SVG foreignObject lets us embed AzureVm */}
                                    <foreignObject x="-14" y="-22" width="28" height="28" style={{ pointerEvents: "none", overflow: "visible" }}>
                                        <AzureVm size={28} />
                                    </foreignObject>

                                    {/* Operating System Badge Logo inside node card */}
                                    <g transform="translate(22, -18)" style={{ pointerEvents: "none" }}>
                                        {isUbuntu ? (
                                            <UbuntuLogo size={11} />
                                        ) : isWindows ? (
                                            <WindowsLogo size={11} />
                                        ) : (
                                            <circle cx="0" cy="0" r="4.5" fill={T.border} />
                                        )}
                                    </g>

                                    {/* Worker badge */}
                                    <rect x="-26" y="8" width="52" height="11" rx="3" fill={T.accentLight} />
                                    <text x="0" y="13.5" textAnchor="middle" dominantBaseline="middle"
                                        style={{ fontSize: 7, fill: T.accent, fontFamily: "monospace", fontWeight: 800 }}>
                                        {node.worker}
                                    </text>
                                    {/* Label */}
                                    <text x="0" y="27.5" textAnchor="middle"
                                        style={{ fontSize: 9.5, fontWeight: 700, fill: T.text }}>
                                        {node.label}
                                    </text>
                                    {/* Specs */}
                                    <text x="0" y="38.5" textAnchor="middle"
                                        style={{ fontSize: 7.5, fill: T.textMuted, fontWeight: 500 }}>
                                        {node.vcores}vCPU · {ram} · {node.disk}GB
                                    </text>
                                    {/* Link-source ring */}
                                    {isLinkSrc && (
                                        <circle r="44" fill="none" stroke={T.accent}
                                            strokeWidth="1.5" strokeDasharray="5,3" opacity="0.45" />
                                    )}
                                </g>
                            );
                        })}
                    </g>
                </svg>

                {/* ── Empty state hint ── */}
                {nodes.length === 0 && (
                    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column",
                        alignItems: "center", justifyContent: "center", pointerEvents: "none", gap: 12 }}>
                        <AzureVm size={64} />
                        <div style={{ color: T.textMuted, fontSize: 13, fontWeight: 600 }}>
                            Arrastre VMs aquí o suelte una plantilla del panel lateral
                        </div>
                        <div style={{ color: T.textFaint, fontSize: 11 }}>Doble clic en cualquier nodo para editarlo</div>
                    </div>
                )}

                {/* ── Node editor overlay ── */}
                {editingNode && (
                    <NodeEditor
                        node={editingNode}
                        availableImages={availableImages}
                        sliceStatus={activeSlice ? activeSlice.status : "DRAFT"}
                        sliceId={activeSlice ? activeSlice.id : null}
                        zoneId={effectiveZoneId}
                        apiFetch={apiFetch}
                        onSave={updated => setNodes(prev => prev.map(n => n.id === updated.id ? updated : n))}
                        onDelete={id => {
                            setNodes(p => p.filter(n => n.id !== id));
                            setEdges(p => p.filter(e => e.from !== id && e.to !== id));
                        }}
                        onClose={() => setEditId(null)}
                        onOpenConsole={onOpenConsole}
                    />
                )}
            </div>

            {/* ── Stats bar ── */}
            {nodes.length > 0 && (
                <div style={{ padding: "7px 16px", borderTop: `1px solid ${T.border}`,
                    background: T.surfaceElevated, display: "flex", gap: 20, flexShrink: 0 }}>
                    {[
                        ["VMs",        nodes.length],
                        ["Enlaces",      edges.length],
                        ["vCPU Totales", nodes.reduce((s, n) => s + n.vcores, 0)],
                        ["RAM Total",  totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`],
                        ["Disco Total", `${nodes.reduce((s, n) => s + n.disk, 0)} GB`],
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
