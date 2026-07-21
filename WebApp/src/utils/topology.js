// --- NODE / TOPOLOGY HELPERS -------------------------------------------------

/**
 * Escala de la tarjeta de VM en el lienzo. Vive aquí, y no en Canvas.jsx, para
 * que el espaciado de las plantillas de abajo crezca CON el nodo: si el nodo se
 * agranda pero la separación no, las tarjetas acaban tapando las etiquetas de
 * interfaz (ens3/ens4) que se dibujan sobre los enlaces.
 *
 * Canvas.jsx lo importa para el scale() del <g>, el hit-test y el anclaje de
 * las etiquetas. Es la única fuente de verdad: cambiar este número reescala
 * todo de forma consistente.
 */
export const NODE_SCALE = 1.35;

let _nid = 200;

// Base temporal única por carga de página: garantiza que los IDs de nodos NUEVOS
// nunca colisionen con los de un slice ya guardado (que también usan n200, n201…)
// al editar en caliente (Modo Edición) ni entre recargas del navegador.
const _idBase = Date.now().toString(36);
let _nodeSeq = 0;

export const mkNode = (x, y, label, defaultImg) => {
    _nodeSeq++;
    return {
        id: `n${_idBase}${_nodeSeq}`, x, y,
        label: label || `VM-${_nodeSeq}`,
        vcores: 1, ram: 256, disk: 1,
        image:    defaultImg?.name || "Cirros",
        image_id: defaultImg?.id   || null,
    };
};

// ── Linear ────────────────────────────────────────────────────────────────────
export const buildLinear = (count, cx, cy) => {
    const sp = 150 * NODE_SCALE, totalW = (count - 1) * sp;
    const nodes = Array.from({ length: count }, (_, i) =>
        mkNode(cx - totalW / 2 + i * sp, cy, `VM-${i + 1}`)
    );
    const edges = nodes.slice(0, -1).map((_, i) => ({
        id: `e${Date.now()}-${i}`, from: nodes[i].id, to: nodes[i + 1].id,
    }));
    return { nodes, edges };
};

// ── Ring ──────────────────────────────────────────────────────────────────────
export const buildRing = (count, cx, cy) => {
    const r = Math.max(100, count * 30) * NODE_SCALE;
    const nodes = Array.from({ length: count }, (_, i) => {
        const a = (2 * Math.PI * i) / count - Math.PI / 2;
        return mkNode(cx + r * Math.cos(a), cy + r * Math.sin(a), `VM-${i + 1}`);
    });
    const edges = nodes.map((_, i) => ({
        id: `e${Date.now()}-${i}`, from: nodes[i].id, to: nodes[(i + 1) % count].id,
    }));
    return { nodes, edges };
};

// ── Mesh (fully connected) ────────────────────────────────────────────────────
export const buildMesh = (count, cx, cy) => {
    const r = Math.max(110, count * 32) * NODE_SCALE;
    const nodes = Array.from({ length: count }, (_, i) => {
        const a = (2 * Math.PI * i) / count - Math.PI / 2;
        return mkNode(cx + r * Math.cos(a), cy + r * Math.sin(a), `VM-${i + 1}`);
    });
    const edges = [];
    let ei = 0;
    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
            edges.push({ id: `e${Date.now()}-${ei++}`, from: nodes[i].id, to: nodes[j].id });
        }
    }
    return { nodes, edges };
};

// ── Tree (binary balanced) ────────────────────────────────────────────────────
export const buildTree = (count, cx, cy) => {
    const nodes = [];
    const edges = [];
    const spX = 140 * NODE_SCALE, spY = 110 * NODE_SCALE;

    // BFS layout: place nodes level by level
    for (let i = 0; i < count; i++) {
        const level  = Math.floor(Math.log2(i + 1));
        const posInLevel = i - (Math.pow(2, level) - 1);
        const totalInLevel = Math.pow(2, level);
        const x = cx + (posInLevel - (totalInLevel - 1) / 2) * spX * (Math.pow(2, 4) / totalInLevel);
        const y = cy - ((Math.floor(Math.log2(count)) / 2) * spY) + level * spY;
        nodes.push(mkNode(x, y, `VM-${i + 1}`));

        // Edge to parent
        if (i > 0) {
            const parentIdx = Math.floor((i - 1) / 2);
            edges.push({ id: `e${Date.now()}-${i}`, from: nodes[parentIdx].id, to: nodes[i].id });
        }
    }
    return { nodes, edges };
};

// ── Bus ───────────────────────────────────────────────────────────────────────
// One central bus node connected to all others (star/hub topology)
export const buildBus = (count, cx, cy) => {
    const sp = 140 * NODE_SCALE;
    const totalW = (count - 1) * sp;

    // All nodes on a horizontal line; bus node is separate (center-top)
    const leaves = Array.from({ length: count }, (_, i) =>
        mkNode(cx - totalW / 2 + i * sp, cy + 80 * NODE_SCALE, `VM-${i + 1}`)
    );
    const busNode = mkNode(cx, cy - 60 * NODE_SCALE, "BUS");

    const nodes = [busNode, ...leaves];
    const edges = leaves.map((leaf, i) => ({
        id: `e${Date.now()}-${i}`, from: busNode.id, to: leaf.id,
    }));
    return { nodes, edges };
};

// --- SLICE METADATA HELPERS --------------------------------------------------

export const computeSliceMeta = (nodes) => {
    const totalRam = nodes.reduce((s, n) => s + n.ram, 0);
    return {
        nodeCount: nodes.length,
        vcpus:    nodes.reduce((s, n) => s + n.vcores, 0),
        ramLabel: totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)}G` : `${totalRam}M`,
    };
};

export const mkSlice = (name, status, nodes, edges) => ({
    id: `s${_nid++}`, name, status, nodes, edges,
    edgeCount: edges.length,
    ...computeSliceMeta(nodes),
});

export const refreshMeta = (sl) => ({
    ...sl,
    nodeCount: sl.nodes.length,
    edgeCount: sl.edges.length,
    ...computeSliceMeta(sl.nodes),
});
