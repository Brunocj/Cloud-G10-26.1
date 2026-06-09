import { useState, useRef } from "react";
import { T, btnBase } from "../../theme/tokens";
import { buildLinear, buildRing, buildMesh, buildTree, buildBus, mkNode } from "../../utils/topology";
import { NodeEditor } from "./NodeEditor";
import { MousePointer2, Link2, Monitor, Trash2, Maximize2, AlertTriangle } from "../ui/Icon";

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
export const Canvas = ({ nodes, edges, setNodes, setEdges, imageList, activeSlice, onOpenConsole }) => {
    const svgRef   = useRef();
    const groupRef = useRef();           // root <g> — updated imperatively during pan

    const [mode,     setMode]     = useState("select");
    const [linkFrom, setLinkFrom] = useState(null);
    const [mouse,    setMouse]    = useState({ x: 0, y: 0 }); // world coords, for link preview
    const [editId,   setEditId]   = useState(null);

    // Pan offset – stored in a ref for zero-latency imperative updates;
    // committed to state only on pointerup so React can re-render consistently.
    const panRef  = useRef({ x: 0, y: 0 });
    const [pan, setPan] = useState({ x: 0, y: 0 });

    // Active drag refs (no state → no re-renders mid-drag)
    const nodeDrag = useRef(null); // { id, startNodeX, startNodeY, startWorldX, startWorldY }
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
        return { x: s.x - panRef.current.x, y: s.y - panRef.current.y };
    };

    /** Apply pan imperatively (avoids React re-render during drag). */
    const applyPan = (x, y) => {
        panRef.current = { x, y };
        if (groupRef.current) groupRef.current.setAttribute("transform", `translate(${x},${y})`);
    };

    /** Commit pan to React state (triggers re-render once, on pointer-up). */
    const commitPan = () => setPan({ ...panRef.current });

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
            setNodes(prev => prev.map(n =>
                n.id === id
                    ? { ...n, x: startNodeX + world.x - startWorldX, y: startNodeY + world.y - startWorldY }
                    : n
            ));
            return;
        }

        // ── Background pan ─────────────────────────────────────────────────
        if (bgDrag.current) {
            const { startScreenX, startScreenY, startPanX, startPanY } = bgDrag.current;
            const newX = startPanX + (screen.x - startScreenX);
            const newY = startPanY + (screen.y - startScreenY);
            applyPan(newX, newY);   // imperative: no React re-render mid-drag
        }
    };

    const onPointerUp = () => {
        nodeDrag.current = null;
        if (bgDrag.current) {
            bgDrag.current = null;
            commitPan();            // one React re-render to sync state
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

    // ── Derived values ────────────────────────────────────────────────────────

    const editingNode  = editId   ? nodes.find(n => n.id === editId)   : null;
    const linkFromNode = linkFrom ? nodes.find(n => n.id === linkFrom) : null;
    const totalRam     = nodes.reduce((s, n) => s + n.ram, 0);
    const ifaceMap     = buildIfaceMap(edges);

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative",
            userSelect: "none", WebkitUserSelect: "none" }}>

            {/* ── Mode bar ── */}
            <div style={{ padding: "8px 14px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated,
                display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                {[  ["select", <MousePointer2 size={12} />, "Select & Move"],
                    ["link",   <Link2 size={12} />,          "Link Nodes"],
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
                            ? "Click the destination node to connect — or click background to cancel"
                            : "Click the source node to start a link"}
                    </span>
                )}
                {mode === "select" && (
                    <span style={{ fontSize: 11, color: T.textMuted }}>
                        Drag node to move · Drag background to pan · Double-click to edit · Click edge to delete
                    </span>
                )}

                <div style={{ flex: 1 }} />

                {/* Fit to screen button */}
                <button
                    title="Reset view"
                    onClick={() => { applyPan(0, 0); commitPan(); }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 10px", color: T.textMuted, border: `1px solid ${T.border}`,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Maximize2 size={12} /> Reset View
                </button>

                <button
                    onClick={() => {
                        if (window.confirm("¿Estás seguro de limpiar todo el lienzo? Perderás el trabajo no guardado.")) {
                            setNodes([]); setEdges([]); setLinkFrom(null); setEditId(null);
                        }
                    }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 12px",
                        color: T.red, border: `1px solid ${T.red}33`, background: T.redLight,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Trash2 size={12} /> Clear
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
                >
                    {/* ── Background (static, outside pan group) ── */}
                    <defs>
                        {/* Grid dots shift with pan for infinite-canvas feel */}
                        <pattern id="dots2" width="24" height="24" patternUnits="userSpaceOnUse"
                            patternTransform={`translate(${pan.x % 24},${pan.y % 24})`}>
                            <circle cx="0.8" cy="0.8" r="0.8" fill={T.border} />
                        </pattern>
                    </defs>
                    <rect width="100%" height="100%" fill={T.bg} />
                    <rect width="100%" height="100%" fill="url(#dots2)" />

                    {/* ── All canvas content inside a panned group ── */}
                    <g ref={groupRef} transform={`translate(${pan.x},${pan.y})`}>

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
                                <g key={ed.id}>
                                    {/* Visible line */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        stroke={T.accentMid} strokeWidth="2.5" opacity="0.5" strokeLinecap="round"
                                        style={{ pointerEvents: "none" }} />
                                    {/* Wide invisible hit area for delete */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        stroke="transparent" strokeWidth="18" strokeLinecap="round"
                                        style={{ cursor: "pointer" }}
                                        onPointerDown={ev => onEdgePointerDown(ev, ed.id)} />
                                    {/* Interface labels */}
                                    {[{ x: fx, y: fy, label: iface.fromIface }, { x: tx, y: ty, label: iface.toIface }].map(({ x, y, label }) =>
                                        label ? (
                                            <g key={label + x} style={{ pointerEvents: "none" }}>
                                                <rect x={x - 17} y={y - 8} width={34} height={14} rx={4}
                                                    fill={T.accentLight} stroke={T.accent + "66"} strokeWidth={1} />
                                                <text x={x} y={y + 0.5} textAnchor="middle" dominantBaseline="middle"
                                                    style={{ fontSize: 7.5, fill: T.accent, fontFamily: "monospace", fontWeight: 800 }}>
                                                    {label}
                                                </text>
                                            </g>
                                        ) : null
                                    )}
                                </g>
                            );
                        })}

                        {/* Nodes */}
                        {nodes.map(node => {
                            const isLinkSrc    = linkFrom === node.id;
                            const isEditing    = editId   === node.id;
                            const isLinkTarget = mode === "link" && linkFrom && linkFrom !== node.id;
                            const ram = node.ram >= 1024 ? `${node.ram / 1024}GB` : `${node.ram}MB`;
                            return (
                                <g key={node.id} transform={`translate(${node.x},${node.y})`}
                                    style={{ pointerEvents: "none" }}>
                                    {/* Drop shadow */}
                                    <rect x="-28" y="-24" width="56" height="52" rx="12"
                                        fill="rgba(20,50,22,0.12)" transform="translate(2,3)" />
                                    {/* Card body */}
                                    <rect x="-28" y="-24" width="56" height="52" rx="12"
                                        fill={T.surface}
                                        stroke={isLinkSrc ? T.accent : isEditing ? T.accentMid : isLinkTarget ? T.accentMid + "88" : T.border}
                                        strokeWidth={isLinkSrc || isEditing ? 2 : 1.5} />
                                    {/* Top color stripe */}
                                    <rect x="-28" y="-24" width="56" height="8"  rx="12" fill={isLinkSrc ? T.accent : T.accentMid} />
                                    <rect x="-28" y="-18" width="56" height="4"         fill={isLinkSrc ? T.accent : T.accentMid} />
                                    {/* Icon — SVG foreignObject lets us embed Lucide */}
                                    <foreignObject x="-11" y="-18" width="22" height="22" style={{ pointerEvents: "none", overflow: "visible" }}>
                                        <Monitor
                                            xmlns="http://www.w3.org/2000/svg"
                                            size={18}
                                            color={isLinkSrc ? T.accent : T.textMuted}
                                            style={{ display: "block" }}
                                        />
                                    </foreignObject>
                                    {/* Worker badge */}
                                    <rect x="-22" y="5" width="44" height="12" rx="3" fill={T.accentLight} />
                                    <text x="0" y="11" textAnchor="middle" dominantBaseline="middle"
                                        style={{ fontSize: 7.5, fill: T.accent, fontFamily: "monospace", fontWeight: 700 }}>
                                        {node.worker}
                                    </text>
                                    {/* Label */}
                                    <text x="0" y="25" textAnchor="middle"
                                        style={{ fontSize: 10, fontWeight: 700, fill: T.text }}>
                                        {node.label}
                                    </text>
                                    {/* Specs */}
                                    <text x="0" y="36" textAnchor="middle"
                                        style={{ fontSize: 8, fill: T.textMuted }}>
                                        {node.vcores}vCPU · {ram} · {node.disk}GB
                                    </text>
                                    {/* Link-source ring */}
                                    {isLinkSrc && (
                                        <circle r="36" fill="none" stroke={T.accent}
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
                        alignItems: "center", justifyContent: "center", pointerEvents: "none", gap: 8 }}>
                        <Monitor size={52} color={T.border} style={{ opacity: 0.4 }} />
                        <div style={{ color: T.textMuted, fontSize: 13, fontWeight: 600 }}>
                            Drag VMs here or drop a template from the sidebar
                        </div>
                        <div style={{ color: T.textFaint, fontSize: 11 }}>Double-click any node to edit it</div>
                    </div>
                )}

                {/* ── Node editor overlay ── */}
                {editingNode && (
                    <NodeEditor
                        node={editingNode}
                        availableImages={imageList}
                        sliceStatus={activeSlice ? activeSlice.status : "DRAFT"}
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
                        ["Links",      edges.length],
                        ["Total vCPU", nodes.reduce((s, n) => s + n.vcores, 0)],
                        ["Total RAM",  totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`],
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
