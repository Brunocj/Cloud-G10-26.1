import { useState, useRef } from "react";
import { T, btnBase, getStatusVisual } from "../../theme/tokens";
import { buildLinear, buildRing, buildMesh, buildTree, buildBus, mkNode, NODE_SCALE } from "../../utils/topology";
import { NodeEditor } from "./NodeEditor";
import { ConfirmModal } from "../modals/ConfirmModal";
import { MousePointer2, Link2, Trash2, Maximize2, ZoomIn, ZoomOut, Globe } from "../ui/Icon";
import { AzureVm, UbuntuLogo, WindowsLogo, CanvasNetBadge, CanvasSwitch } from "../ui/AzureIcons";

// ─── Geometría del nodo ───────────────────────────────────────────────────────
// NODE_SCALE se importa de utils/topology.js, que es también quien escala el
// espaciado de las plantillas con el mismo factor. Se aplica como scale() en el
// <g> del nodo, así que arrastra consigo textos, íconos y badges.
// OJO: los enlaces se dibujan entre CENTROS de nodo, que no dependen de la
// escala — pero sus ETIQUETAS sí (ver labelAnchor).

// Semiejes del área sensible al clic, en coordenadas de mundo.
const HIT_HALF_W = 30 * NODE_SCALE;
const HIT_HALF_H = 28 * NODE_SCALE;

// Media caja que ocupa la tarjeta dibujada. El alto usa 42 y no 30 porque bajo
// el rectángulo todavía se pintan el label y las specs (y=27.5 y y=38.5).
const CARD_HALF_W = 36 * NODE_SCALE;
const CARD_HALF_H = 42 * NODE_SCALE;

// Separación entre el borde de la tarjeta y la etiqueta de interfaz.
const LABEL_MARGIN = 13;

// Etiquetas de interfaz (ens3/ens4…). Antes eran 34×14 con texto de 7.5px:
// ilegibles proyectadas. Se escalan igual que el nodo.
const LABEL_W  = 42 * NODE_SCALE;
const LABEL_H  = 17 * NODE_SCALE;
const LABEL_FS = 9.5 * NODE_SCALE;

/**
 * Punto donde se ancla la etiqueta de interfaz del extremo `from` de un enlace.
 *
 * Antes se usaba una fracción fija de la recta (0.28 / 0.72). Eso rompía en
 * cuanto el enlace era corto o el nodo grande: con el espaciado lineal de 150 y
 * la tarjeta a escala 1.35, el 28% caía en 42 px mientras la tarjeta llega a
 * 48.6 px — la etiqueta quedaba DEBAJO de la VM. Ahora se proyecta el rayo
 * from→to contra el rectángulo de la tarjeta y se separa LABEL_MARGIN del
 * borde real, así que no depende de la longitud del enlace.
 *
 * El clamp a 0.40·longitud evita que en enlaces muy cortos las dos etiquetas
 * del mismo enlace se crucen en el centro.
 */
const labelAnchor = (from, to) => {
    const dx = to.x - from.x, dy = to.y - from.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    // Distancia del centro al borde del rectángulo en la dirección (ux,uy)
    const hitX = Math.abs(ux) > 1e-6 ? CARD_HALF_W / Math.abs(ux) : Infinity;
    const hitY = Math.abs(uy) > 1e-6 ? CARD_HALF_H / Math.abs(uy) : Infinity;
    const d = Math.min(Math.min(hitX, hitY) + LABEL_MARGIN, len * 0.40);
    return { x: from.x + ux * d, y: from.y + uy * d };
};

/**
 * Etiqueta de un extremo de enlace: nombre de interfaz y, opcionalmente, la IP
 * estática que se le asignó.
 *
 * Se dibuja como un <g> posicionado por transform con el contenido en
 * coordenadas relativas, para que el arrastre imperativo solo tenga que mover
 * el grupo (ver updateEdgeCoords) y para que dé igual si lleva una o dos líneas.
 */
const EdgeLabel = ({ className, x, y, iface, ip }) => {
    const twoLines = !!ip;
    // Con IP la caja crece a lo alto y a lo ancho: un CIDR ocupa bastante más
    // que "ens4" y con el ancho de una línea se saldría del recuadro.
    const w = twoLines ? LABEL_W * 1.7 : LABEL_W;
    const h = twoLines ? LABEL_H * 1.85 : LABEL_H;
    return (
        <g className={className} transform={`translate(${x},${y})`}>
            <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={5}
                fill={T.surface} stroke={T.accent + "88"} strokeWidth={1.2} />
            <text x={0} y={twoLines ? -h / 2 + LABEL_H * 0.55 : 0}
                textAnchor="middle" dominantBaseline="middle"
                style={{ fontSize: LABEL_FS, fill: T.accent, fontFamily: "monospace", fontWeight: 800 }}>
                {iface}
            </text>
            {twoLines && (
                <text x={0} y={h / 2 - LABEL_H * 0.5} textAnchor="middle" dominantBaseline="middle"
                    style={{ fontSize: LABEL_FS * 0.85, fill: T.textMuted, fontFamily: "monospace", fontWeight: 700 }}>
                    {ip}
                </text>
            )}
        </g>
    );
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Adivina el label ens3=gestión/ens4+=enlaces contando posiciones en el array
 * de edges actual. SOLO es un fallback para topologías en modo diseño (aún no
 * desplegadas) — para un slice ya desplegado, el nombre real viene persistido
 * desde el backend (ed.fromIface/ed.toIface) y ese se usa en su lugar, porque
 * este conteo se desincroniza apenas se agrega o borra un enlace.
 */
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
export const Canvas = ({ nodes, edges, setNodes, setEdges, imageList, activeSlice, onOpenConsole, targetAz, setTargetAz, apiFetch, onCleared, isDesignMode, editMode = false }) => {
    const svgRef   = useRef();
    const groupRef = useRef();           // root <g> — updated imperatively during pan/zoom

    const [mode,     setMode]     = useState("select");
    const [showMgmt, setShowMgmt] = useState(true);    // red de gestión visible
    const [showIps,  setShowIps]  = useState(false);   // IPs de enlace visibles
    const [confirmClear, setConfirmClear] = useState(false);
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

    /**
     * Único punto que escribe el cursor del SVG. Memoriza el valor en `cursor`
     * para no reescribir el estilo en cada pointermove; por eso TODOS los sitios
     * que cambian el cursor deben pasar por aquí, o el ref quedaría desfasado y
     * el hover dejaría de actualizarse.
     */
    const setCursor = (value) => {
        if (cursor.current === value) return;
        cursor.current = value;
        if (svgRef.current) svgRef.current.style.cursor = value;
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
            // Mismo anclaje que el render (labelAnchor) — si estas dos fórmulas
            // se separan, las etiquetas "saltan" al empezar a arrastrar.
            // Cada etiqueta es un <g> con transform propio y su contenido en
            // coordenadas relativas, así que basta con mover el grupo: no hay
            // que reposicionar rect y text por separado (y sigue funcionando
            // igual tenga una línea o dos, con IP).
            const f = labelAnchor(A, B);
            const t = labelAnchor(B, A);
            const gFrom = labelsGroup.querySelector('.label-from');
            const gTo   = labelsGroup.querySelector('.label-to');
            if (gFrom) gFrom.setAttribute("transform", `translate(${f.x},${f.y})`);
            if (gTo)   gTo.setAttribute("transform", `translate(${t.x},${t.y})`);
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
            if (Math.abs(world.x - n.x) <= HIT_HALF_W && Math.abs(world.y - n.y) <= HIT_HALF_H) return n;
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
            setCursor("grabbing");
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
        setCursor("grabbing");
    };

    const onPointerMove = (e) => {
        const world  = toWorld(e);
        const screen = toScreen(e);

        // Update mouse for link preview line
        setMouse(world);

        // Hover sobre nodo: el cursor indica que la tarjeta es agarrable antes
        // de pulsar. Se aplica de forma imperativa sobre el SVG y se memoriza en
        // `cursor` para no reescribir el estilo en cada evento de movimiento
        // (este handler se dispara decenas de veces por segundo).
        if (!nodeDrag.current && !bgDrag.current && svgRef.current) {
            const over = hitNode(world);
            const want = mode === "link"
                ? (over ? "pointer" : "crosshair")
                : (over ? "grab" : "default");
            setCursor(want);
        }

        // ── Node drag ──────────────────────────────────────────────────────
        if (nodeDrag.current) {
            const { id, startNodeX, startNodeY, startWorldX, startWorldY } = nodeDrag.current;
            const newX = startNodeX + world.x - startWorldX;
            const newY = startNodeY + world.y - startWorldY;
            
            // 1. Update mutable ref position
            nodePositionsRef.current[id] = { x: newX, y: newY };
            
            // 2. Update node DOM element transform
            //    Debe repetir el scale() del render — si se escribe solo el
            //    translate, el nodo se encoge de golpe al empezar a arrastrarlo.
            const nodeEl = document.getElementById(`node-${id}`);
            if (nodeEl) {
                nodeEl.setAttribute("transform", `translate(${newX},${newY}) scale(${NODE_SCALE})`);
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
        setCursor(mode === "link" ? "crosshair" : "default");
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
            setNodes(prev => {
                // Etiqueta única: continúa la numeración VM-N por encima de las
                // que ya existen en el lienzo (evita nombres repetidos al editar).
                let max = 0;
                for (const n of prev) {
                    const m = /^VM-(\d+)$/.exec(n.label || "");
                    if (m) max = Math.max(max, Number(m[1]));
                }
                return [...prev, mkNode(world.x, world.y, `VM-${max + 1}`, imageList[0])];
            });
        }
    };

    // ── Edge delete (inside edge's onPointerDown) ─────────────────────────────

    const onEdgePointerDown = (e, edgeId) => {
        if (!isDesignMode) return; // solo se puede borrar un enlace en modo diseño/edición
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
        setCursor(m === "link" ? "crosshair" : "default");
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

    // Estado visual del slice. OJO: el modelo NO guarda estado por VM
    // (SliceManager solo persiste Slice.status), así que todos los nodos de un
    // slice comparten indicador — no se puede pintar una VM caída aparte.
    // En el diseñador (sin slice todavía) no hay estado que mostrar.
    const statusVis = activeSlice ? getStatusVisual(activeSlice.status) : null;
    // Estados en vuelo → los enlaces "fluyen" para que se vea que hay trabajo.
    const flowing   = !!statusVis?.pulse;

    /**
     * IP estática del extremo `nodeId` del enlace `ed`.
     * Las IPs de enlace se guardan POR NODO, indexadas por id de enlace
     * (`node.link_ips[edgeId]`, ver NodeEditor), no en el propio enlace: cada
     * extremo tiene la suya, así que hay que mirar en el nodo correspondiente.
     */
    const linkIpOf = (ed, nodeId) => {
        const n = nodes.find(x => x.id === nodeId);
        return (n?.link_ips || {})[ed.id] || null;
    };

    // ¿Hay alguna IP de enlace definida? Si no, el botón de mostrarlas no
    // aparece: sería un interruptor que no cambia nada.
    const hasLinkIps = nodes.some(n => Object.values(n.link_ips || {}).some(Boolean));

    // ── Red de gestión ───────────────────────────────────────────────────────
    // Todo slice recibe una red de gestión propia: cada VM la ve como ens3 y es
    // por donde el orquestador la administra (los ens4+ son los enlaces que el
    // usuario dibuja). No es un nodo del modelo — no está en `nodes` ni se
    // puede editar — así que se calcula aquí a partir del conjunto de VMs.
    //
    // El switch se coloca centrado bajo la nube de VMs, a una distancia fija
    // del borde inferior, para no cruzarse con la topología dibujada arriba.
    const mgmtHub = nodes.length > 0 ? {
        x: nodes.reduce((s, n) => s + n.x, 0) / nodes.length,
        y: Math.max(...nodes.map(n => n.y)) + 130 * NODE_SCALE,
    } : null;

    // ── Filter Images based on Target AZ ──────────────────────────────────────
    const availableImages = imageList.filter(img => {
        if (!targetAz) return true; // "Cualquiera"
        return img.availability_zone_id == targetAz;
    });

    // Zona efectiva del slice: la del slice ya desplegado, o la elegida en el toolbar (borrador)
    const effectiveZoneId = activeSlice ? activeSlice.availability_zone_id : targetAz;

    // ── Render ────────────────────────────────────────────────────────────────

    // Toolbar is only relevant when designing (new slice, DRAFT edit, o Modo
    // Edición Post-Despliegue de un slice ACTIVO — REQ-US-14)
    const showToolbar = !activeSlice || activeSlice.status === "DRAFT" || editMode;

    return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative",
            userSelect: "none", WebkitUserSelect: "none" }}>

            {/* ── Mode bar — only shown in design mode ── */}
            {showToolbar && <div style={{ padding: "8px 14px", borderBottom: `1px solid ${T.border}`, background: T.surfaceElevated,
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

                {/* Modo Edición: fuente de arrastre de VMs (el sidebar está en modo browse) */}
                {editMode && (
                    <div draggable onDragStart={e => e.dataTransfer.setData("nodeType", "vm")}
                        title="Arrastra al lienzo para agregar una VM nueva"
                        style={{
                            display: "flex", alignItems: "center", gap: 6, cursor: "grab",
                            padding: "4px 10px", background: T.accentLight,
                            border: `1.5px dashed ${T.accent}66`, borderRadius: 8, marginRight: 4,
                        }}>
                        <span style={{ fontSize: 11, fontWeight: 700, color: T.accent }}>+ VM (arrastrar)</span>
                    </div>
                )}

                {/* Target AZ Selector — se bloquea una vez hay nodos, para evitar incompatibilidades de imagen */}
                {!editMode && <div style={{ display: "flex", alignItems: "center", gap: 6, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "2px 8px", marginRight: 4 }}
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
                </div>}

                {/* Controles de Zoom */}
                <div style={{ display: "flex", alignItems: "center", gap: 4, marginRight: 4, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "2px" }}>
                    <button
                        title="Acercar" aria-label="Acercar"
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
                        title="Alejar" aria-label="Alejar"
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
                    title="Restablecer vista" aria-label="Restablecer vista"
                    onClick={() => { applyTransform(0, 0, 1); commitTransform(); }}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 10px", color: T.textMuted, border: `1px solid ${T.border}`,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Maximize2 size={12} /> Restablecer Vista
                </button>

                {!editMode && <button
                    onClick={() => setConfirmClear(true)}
                    style={btnBase({ boxShadow: "none", fontSize: 11, padding: "5px 12px",
                        color: T.red, border: `1px solid ${T.red}33`, background: T.redLight,
                        display: "flex", alignItems: "center", gap: 5 })}>
                    <Trash2 size={12} /> Limpiar
                </button>}
            </div>}

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

                        {/* ── Red de gestión (ens3) ──────────────────────────
                            Va ANTES que enlaces y nodos para quedar por debajo:
                            es infraestructura de fondo, no la topología que el
                            usuario diseña. Los enlaces son punteados y de color
                            atenuado justo para que no compitan con los ens4+. */}
                        {showMgmt && mgmtHub && (
                            <g style={{ pointerEvents: "none" }}>
                                {nodes.map(n => (
                                    <line key={`mgmt-${n.id}`}
                                        x1={n.x} y1={n.y} x2={mgmtHub.x} y2={mgmtHub.y}
                                        stroke={T.textFaint} strokeWidth="1.4"
                                        strokeDasharray="4 5" opacity="0.55" strokeLinecap="round" />
                                ))}
                                <g transform={`translate(${mgmtHub.x},${mgmtHub.y}) scale(${NODE_SCALE})`}>
                                    <CanvasSwitch ports={nodes.length} />
                                    <text x="0" y="27" textAnchor="middle"
                                        style={{ fontSize: 9, fontWeight: 800, fill: T.text }}>
                                        Red de Gestión
                                    </text>
                                    <text x="0" y="37" textAnchor="middle"
                                        style={{ fontSize: 7.5, fill: T.textMuted, fontFamily: "monospace" }}>
                                        ens3 · {nodes.length} VM{nodes.length === 1 ? "" : "s"}
                                    </text>
                                </g>
                            </g>
                        )}


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
                            // El backend persiste el ensN real (calculado al desplegar/
                            // extender, ver SliceManager placement_worker) — se usa ese
                            // si existe. El mapa contado localmente (ifaceMap) es solo un
                            // fallback para topologías aún no desplegadas (modo diseño),
                            // donde no hay una interfaz real todavía que mostrar.
                            const iface = (ed.fromIface || ed.toIface)
                                ? { fromIface: ed.fromIface, toIface: ed.toIface }
                                : (ifaceMap[ed.id] || {});
                            const f = labelAnchor(A, B);
                            const t = labelAnchor(B, A);
                            return (
                                <g key={ed.id} id={`edge-group-${ed.id}`}>
                                    {/* Visible line.
                                        Se mantiene recta a propósito: en un diagrama de red
                                        la línea recta es la convención y además el arrastre
                                        actualiza x1/y1/x2/y2 de forma imperativa (ver
                                        updateEdgeCoords) — una curva obligaría a recalcular
                                        el path en cada frame sin ganar legibilidad. */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        data-edge-id={ed.id} data-from={ed.from} data-to={ed.to}
                                        stroke={flowing ? T.yellow : T.accentMid}
                                        strokeWidth="2.5" opacity={0.75} strokeLinecap="round"
                                        strokeDasharray={flowing ? "8 4" : undefined}
                                        style={{
                                            pointerEvents: "none",
                                            animation: flowing ? "edgeFlow 0.6s linear infinite" : undefined,
                                        }} />
                                    {/* Wide invisible hit area for delete — solo interactivo en modo diseño/edición */}
                                    <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                                        data-edge-id={ed.id} data-from={ed.from} data-to={ed.to}
                                        stroke="transparent" strokeWidth="18" strokeLinecap="round"
                                        style={{ cursor: isDesignMode ? "pointer" : "default" }}
                                        onPointerDown={ev => onEdgePointerDown(ev, ed.id)} />
                                    {/* Etiquetas de interfaz (+ IP si está activado).
                                        Cada extremo es un <g> con transform y contenido
                                        relativo — ver updateEdgeCoords. */}
                                    <g id={`edge-labels-${ed.id}`} style={{ pointerEvents: "none" }}>
                                        {iface.fromIface && (
                                            <EdgeLabel className="label-from" x={f.x} y={f.y}
                                                iface={iface.fromIface} ip={showIps ? linkIpOf(ed, ed.from) : null} />
                                        )}
                                        {iface.toIface && (
                                            <EdgeLabel className="label-to" x={t.x} y={t.y}
                                                iface={iface.toIface} ip={showIps ? linkIpOf(ed, ed.to) : null} />
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
                            // Ícono real subido para esta imagen (ver ImagePanel) — si no hay uno
                            // cargado todavía, cae al match por nombre como antes.
                            const nodeImage = imageList?.find(i => i.id === node.image_id);
                            const isUbuntu = node.image?.toLowerCase().includes("ubuntu");
                            const isWindows = node.image?.toLowerCase().includes("win");

                            return (
                                <g key={node.id} id={`node-${node.id}`}
                                    transform={`translate(${node.x},${node.y}) scale(${NODE_SCALE})`}
                                    style={{ pointerEvents: "none" }}>
                                    {/* Drop shadow (subtle Azure style) */}
                                    <rect x="-36" y="-30" width="72" height="60" rx="6"
                                        fill="rgba(0,120,212,0.08)" transform="translate(1.5,2.5)" />
                                    {/* Card body — el borde toma el color del estado del
                                        slice, así un slice ACTIVO, uno PROVISIONANDO y uno
                                        FALLIDO se distinguen de un vistazo. En el diseñador
                                        (statusVis null) se queda con el borde neutro. */}
                                    <rect x="-36" y="-30" width="72" height="60" rx="6"
                                        fill={T.surface}
                                        stroke={isLinkSrc ? T.accent : isEditing ? T.accentMid : isLinkTarget ? T.accentMid + "88" : statusVis ? statusVis.fg + "99" : T.border}
                                        strokeWidth={isLinkSrc || isEditing ? 2 : statusVis ? 1.75 : 1.25} />
                                    
                                    {/* Accent top border highlight */}
                                    {(isLinkSrc || isEditing) && (
                                        <path d="M-30 -30 H30" stroke={T.accent} strokeWidth="2.5" strokeLinecap="round" />
                                    )}

                                    {/* Conectividad: nube hueca = NAT saliente,
                                        nube maciza + IP = acceso externo entrante. */}
                                    {node.internet_access === 1 && (
                                        <CanvasNetBadge
                                            external={!!node.external_ip}
                                            ip={node.external_ip} />
                                    )}

                                    {/* Indicador de estado (esquina superior izquierda).
                                        Espeja el badge de SO que va en la derecha. */}
                                    {statusVis && (
                                        <g transform="translate(-24,-20)">
                                            <circle r="4.5" fill={statusVis.bg}
                                                stroke={statusVis.fg} strokeWidth="1.2" />
                                            <circle r="2" fill={statusVis.fg}
                                                style={{ animation: statusVis.pulse ? "statusPulse 1.4s ease-in-out infinite" : undefined }} />
                                        </g>
                                    )}

                                    {/* Icon — SVG foreignObject lets us embed AzureVm */}
                                    <foreignObject x="-14" y="-22" width="28" height="28" style={{ pointerEvents: "none", overflow: "visible" }}>
                                        <AzureVm size={28} />
                                    </foreignObject>

                                    {/* Operating System Badge Logo inside node card */}
                                    <g transform="translate(22, -18)" style={{ pointerEvents: "none" }}>
                                        {nodeImage?.icon_data ? (
                                            <foreignObject x="-6" y="-6" width="12" height="12" style={{ overflow: "visible" }}>
                                                <img src={nodeImage.icon_data} alt="" width={12} height={12}
                                                    style={{ borderRadius: 2, objectFit: "contain" }} />
                                            </foreignObject>
                                        ) : isUbuntu ? (
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

                {/* ── Toggle de la red de gestión ──────────────────────────
                    Flotante y no en la barra de modo porque esa solo aparece
                    en diseño/edición, y la red de gestión interesa sobre todo
                    al mirar un slice ya desplegado. */}
                {hasLinkIps && (
                    <button
                        onClick={() => setShowIps(v => !v)}
                        aria-pressed={showIps}
                        title="Mostrar u ocultar las IPs asignadas a cada interfaz de enlace"
                        style={btnBase({
                            position: "absolute", top: 12, right: 165, zIndex: 5,
                            fontSize: 11, padding: "5px 11px",
                            background: showIps ? T.accentLight : T.surface,
                            color:      showIps ? T.accent      : T.textMuted,
                            border: `1px solid ${showIps ? T.accent + "66" : T.border}`,
                            display: "flex", alignItems: "center", gap: 6,
                        })}>
                        <Globe size={12} /> IPs de enlace
                    </button>
                )}

                {nodes.length > 0 && (
                    <button
                        onClick={() => setShowMgmt(v => !v)}
                        aria-pressed={showMgmt}
                        title="Mostrar u ocultar la red de gestión (ens3)"
                        style={btnBase({
                            position: "absolute", top: 12, right: 12, zIndex: 5,
                            fontSize: 11, padding: "5px 11px",
                            background: showMgmt ? T.accentLight : T.surface,
                            color:      showMgmt ? T.accent      : T.textMuted,
                            border: `1px solid ${showMgmt ? T.accent + "66" : T.border}`,
                            display: "flex", alignItems: "center", gap: 6,
                        })}>
                        <svg width="15" height="9" viewBox="-33 -16 66 34">
                            <rect x="-31" y="-14" width="62" height="30" rx="5"
                                fill="none" stroke="currentColor" strokeWidth="3" />
                            <path d="M-14 -5 H10 M6 -8.5 L10 -5 L6 -1.5 M14 3 H-10 M-6 -0.5 L-10 3 L-6 6.5"
                                stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
                        </svg>
                        Red de Gestión
                    </button>
                )}

                {/* ── Empty state hint (only in design mode) ── */}
                {nodes.length === 0 && isDesignMode && (
                    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column",
                        alignItems: "center", justifyContent: "center", pointerEvents: "none", gap: 12 }}>
                        <AzureVm size={64} />
                        <div style={{ color: T.textMuted, fontSize: 13, fontWeight: 600 }}>
                            Arrastre VMs aquí o suelte una plantilla del panel lateral
                        </div>
                        <div style={{ color: T.textFaint, fontSize: 11 }}>Doble clic en cualquier nodo para editarlo</div>
                    </div>
                )}

                {/* Confirmación de limpieza del lienzo — sustituye al
                    window.confirm nativo, que rompía la estética de la app. */}
                {confirmClear && (
                    <ConfirmModal
                        title="Limpiar el lienzo"
                        msg={`Se eliminarán ${nodes.length} VM${nodes.length === 1 ? "" : "s"} y ${edges.length} enlace${edges.length === 1 ? "" : "s"} del diseño actual. Perderás el trabajo no guardado.`}
                        confirmLabel="Sí, limpiar"
                        onOk={() => {
                            setNodes([]); setEdges([]); setLinkFrom(null); setEditId(null);
                            setConfirmClear(false);
                            if (onCleared) onCleared();
                        }}
                        onCancel={() => setConfirmClear(false)}
                    />
                )}

                {/* ── Node editor overlay ── */}
                {editingNode && (
                    <NodeEditor
                        node={editingNode}
                        availableImages={availableImages}
                        sliceStatus={activeSlice ? activeSlice.status : "DRAFT"}
                        editMode={editMode}
                        sliceId={activeSlice ? activeSlice.id : null}
                        zoneId={effectiveZoneId}
                        edges={edges}
                        ifaceMap={ifaceMap}
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

                    {/* Leyenda de las nubes — solo aparece si hay alguna VM con
                        conectividad, para no ocupar sitio cuando no aplica. */}
                    {nodes.some(n => n.internet_access === 1) && (
                        <div style={{ display: "flex", alignItems: "center", gap: 14, marginLeft: "auto" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                                <svg width="17" height="12" viewBox="-10 -8 20 14">
                                    <g transform="scale(0.9)">
                                        <rect x="-9" y="-1.5" width="18" height="7" rx="3.5" fill={T.surface} stroke={T.accentMid} strokeWidth="1.3" />
                                        <circle cx="-4.5" cy="-2" r="4.5" fill={T.surface} stroke={T.accentMid} strokeWidth="1.3" />
                                        <circle cx="2.5" cy="-1" r="5.5" fill={T.surface} stroke={T.accentMid} strokeWidth="1.3" />
                                        <rect x="-7.5" y="-1" width="15" height="6" rx="3" fill={T.surface} />
                                        <circle cx="-4.5" cy="-2" r="3.4" fill={T.surface} />
                                        <circle cx="2.5" cy="-1" r="4.4" fill={T.surface} />
                                    </g>
                                </svg>
                                <span style={{ fontSize: 10, color: T.textMuted }}>Salida a Internet (NAT)</span>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                                <svg width="17" height="12" viewBox="-10 -8 20 14">
                                    <g transform="scale(0.9)">
                                        <rect x="-9" y="-1.5" width="18" height="7" rx="3.5" fill={T.accent} stroke={T.accent} strokeWidth="1.3" />
                                        <circle cx="-4.5" cy="-2" r="4.5" fill={T.accent} stroke={T.accent} strokeWidth="1.3" />
                                        <circle cx="2.5" cy="-1" r="5.5" fill={T.accent} stroke={T.accent} strokeWidth="1.3" />
                                    </g>
                                </svg>
                                <span style={{ fontSize: 10, color: T.textMuted }}>Acceso externo (IP VPN)</span>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
