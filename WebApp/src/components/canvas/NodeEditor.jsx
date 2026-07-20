import { useState, useEffect, useRef } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import {
    Settings, Settings2, X, Save, Trash2, ClipboardList, Shield, Globe, Key,
    Network, Lock, CheckCircle, AlertTriangle, Info, Terminal, BarChart2, Plus,
} from "../ui/Icon";

const SZ = 13; // standard icon size inside the panel

const mkRule = () => ({ id: `r${Date.now()}`, protocol: "TCP", port: "" });

const Tab = ({ label, icon: Icon, active, onClick }) => (
    <button onClick={onClick} style={{
        flex: 1, padding: "8px 4px", fontSize: 11, fontWeight: 700, fontFamily: "inherit",
        background: "transparent", border: "none",
        borderBottom: active ? `2px solid ${T.accent}` : "2px solid transparent",
        color: active ? T.accent : T.textMuted,
        cursor: "pointer", transition: "all 0.15s",
        display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
    }}>
        <Icon size={13} /> {label}
    </button>
);

export const NodeEditor = ({ node, availableImages, sliceStatus, editMode = false, sliceId, zoneId, edges = [], ifaceMap = {}, onSave, onDelete, onClose, onOpenConsole, apiFetch }) => {
    const defaultImg = availableImages?.[0];

    const initForm = (n) => ({
        ...n,
        image_id:        n.image_id        || defaultImg?.id   || "",
        image:           n.image           || defaultImg?.name || "",
        internet_access: n.internet_access || 0,
        external_ip:     n.external_ip     || "",
        firewall_rules:  n.firewall_rules  || [],
        ingress_rules:   n.ingress_rules   || [],
        link_ips:        n.link_ips        || {},
    });

    const [activeTab, setActiveTab] = useState("props");
    const [f, setF]                 = useState(() => initForm(node));
    const [availableIps, setIps]    = useState([]);
    const [flavors, setFlavors]     = useState([]);
    // Distingue "todavía no elegiste nada" (muestra el placeholder) de "elegiste
    // Personalizado a propósito" (muestra esa opción) — ambos casos guardan
    // flavor_id=null en el backend, así que esta distinción es solo de UI y se
    // reinicia al abrir el editor de nuevo.
    const [pickedCustom, setPickedCustom] = useState(false);
    const [telemetry,    setTelemetry]    = useState(null);   // null=no pedida, {loading}, o el resultado
    const u = (k, v) => setF(p => ({ ...p, [k]: v }));

    // ── IPs manuales por interfaz de enlace ──────────────────────────────
    const CIDR_RE = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;
    const myEdges = (edges || []).filter(e => e.from === node.id || e.to === node.id);
    const setLinkIp = (edgeId, val) =>
        setF(p => ({ ...p, link_ips: { ...(p.link_ips || {}), [edgeId]: val } }));
    const ifaceLabelFor = (e) => {
        const mine = e.from === node.id;
        const im = ifaceMap[e.id] || {};
        return mine ? (e.fromIface || im.fromIface || "ens?") : (e.toIface || im.toIface || "ens?");
    };

    const loadTelemetry = async () => {
        if (!apiFetch || !sliceId) return;
        setTelemetry({ loading: true });
        try {
            const res  = await apiFetch(`/slices/${sliceId}/vms/${node.id}/telemetry`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al consultar telemetría");
            setTelemetry(data);
        } catch (e) {
            setTelemetry({ available: false, reason: e.message });
        }
    };

    // Una VM está "desplegada" si trae campos que solo el servidor inyecta.
    // En Modo Edición, las VMs NUEVAS (aún no desplegadas) son totalmente
    // editables; las ya desplegadas permanecen de solo lectura (REQ-US-14).
    const isDeployed = !!(node.worker_ip || node.vnc_port || node.provider_instance_id || node.vnc_url);
    const isNewInEdit = editMode && !isDeployed;
    const isReadOnly = sliceStatus && sliceStatus !== "DRAFT" && !isNewInEdit;
    const selectedImage = availableImages?.find(i => i.id === f.image_id);
    const zoneIdNum = zoneId ? Number(zoneId) : null;

    useEffect(() => { setF(initForm(node)); setActiveTab("props"); setPickedCustom(false); setTelemetry(null); }, [node.id]);

    useEffect(() => {
        if (f.internet_access !== 1 || !apiFetch) return;
        const qs = zoneId ? `?zone_id=${zoneId}` : "";
        apiFetch(`/slices/utils/available-ips${qs}`)
            .then(r => r.json())
            .then(data => setIps(
                f.external_ip && !data.includes(f.external_ip)
                    ? [f.external_ip, ...data] : data
            ))
            .catch(() => {});
    }, [f.internet_access, apiFetch, zoneId]);

    // Catálogo de flavors visibles para el usuario
    const loadFlavors = () => {
        if (!apiFetch) return;
        apiFetch("/slices/utils/flavors")
            .then(r => r.json())
            .then(data => setFlavors(Array.isArray(data) ? data : []))
            .catch(() => {});
    };
    useEffect(() => { loadFlavors(); }, [apiFetch]);

    // Aplicar un flavor: copia sus specs y guarda el flavor_id (snapshot en backend)
    const applyFlavor = (fl) => {
        if (!fl) { setF(p => ({ ...p, flavor_id: null })); return; }  // "Personalizado"
        setF(p => ({ ...p, flavor_id: fl.id, vcores: fl.vcpus, ram: fl.ram_mb, disk: fl.disk_gb }));
    };

    const selectedFlavor = flavors.find(x => x.id === f.flavor_id);
    const usingFlavor = !!f.flavor_id;

    const addRule    = () => setF(p => ({ ...p, firewall_rules: [...p.firewall_rules, mkRule()] }));
    const updateRule = (id, key, val) => setF(p => ({ ...p, firewall_rules: p.firewall_rules.map(r => r.id === id ? { ...r, [key]: val } : r) }));
    const deleteRule = (id) => setF(p => ({ ...p, firewall_rules: p.firewall_rules.filter(r => r.id !== id) }));

    // Reglas de entrada desde Internet (AWS-style): a diferencia de firewall_rules
    // (VM↔VM dentro del slice), estas solo se evalúan en el camino IP externa/VPN
    // → VM, y son deny-by-default — sin reglas, ni siquiera el SSH que promete el
    // checkbox de abajo entra. Por eso se auto-siembra TCP/22 al asignar la IP.
    const addIngressRule    = () => setF(p => ({ ...p, ingress_rules: [...p.ingress_rules, mkRule()] }));
    const updateIngressRule = (id, key, val) => setF(p => ({ ...p, ingress_rules: p.ingress_rules.map(r => r.id === id ? { ...r, [key]: val } : r) }));
    const deleteIngressRule = (id) => setF(p => ({ ...p, ingress_rules: p.ingress_rules.filter(r => r.id !== id) }));

    // Drag
    const panelRef  = useRef();
    const dragState = useRef(null);
    const [pos, setPos] = useState(null);

    const onHeaderDown = (e) => {
        if (e.button !== 0) return;
        if (e.target.closest("button, input, select, a")) return;
        e.stopPropagation();
        const rect = panelRef.current.getBoundingClientRect();
        dragState.current = { mx: e.clientX, my: e.clientY, px: rect.left, py: rect.top };
        panelRef.current.setPointerCapture(e.pointerId);
    };
    const onPanelMove = (e) => {
        if (!dragState.current) return;
        const { mx, my, px, py } = dragState.current;
        setPos({ x: Math.max(0, px + e.clientX - mx), y: Math.max(0, py + e.clientY - my) });
    };
    const onPanelUp = () => { dragState.current = null; };

    const posStyle = pos ? { left: pos.x, top: pos.y } : { right: 12, top: 66 };

    return (
        <div ref={panelRef} onPointerMove={onPanelMove} onPointerUp={onPanelUp}
            style={{
                position: "fixed", zIndex: 500, ...posStyle,
                width: 284, maxHeight: "calc(100vh - 80px)",
                display: "flex", flexDirection: "column",
                background: T.surface, borderRadius: 14,
                border: `1.5px solid ${T.accent}44`,
                boxShadow: "0 8px 32px rgba(20,60,22,0.18)",
                overflow: "hidden",
            }}>

            {/* Header */}
            <div onPointerDown={onHeaderDown} style={{
                background: T.accentLight, padding: "10px 14px",
                borderBottom: `1px solid ${T.border}`,
                display: "flex", alignItems: "center", justifyContent: "space-between",
                cursor: "move", flexShrink: 0, userSelect: "none",
            }}>
                <div style={{ display: "flex", alignItems: "center", gap: 7, pointerEvents: "none" }}>
                    <span style={{ fontSize: 9, color: T.textFaint, letterSpacing: 2 }}>⠿⠿</span>
                    {isReadOnly
                        ? <Settings2 size={14} color={T.accent} />
                        : <Settings size={14} color={T.accent} />}
                    <span style={{ fontSize: 12, fontWeight: 800, color: T.accent }}>
                        {isReadOnly ? "Control de Nodo" : "Propiedades del Nodo"}
                    </span>
                </div>
                <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: "2px", display: "flex", alignItems: "center" }}>
                    <X size={16} />
                </button>
            </div>

            {/* Tab bar */}
            <div style={{ display: "flex", borderBottom: `1px solid ${T.border}`, flexShrink: 0, background: T.surfaceElevated }}>
                <Tab label="Propiedades" icon={ClipboardList} active={activeTab === "props"} onClick={() => setActiveTab("props")} />
                <Tab label="Red y Seguridad"  icon={Shield}        active={activeTab === "net"}   onClick={() => setActiveTab("net")}   />
            </div>

            {/* Scrollable body */}
            <div style={{ flex: 1, overflowY: "auto", padding: "13px 14px 6px" }}>

                {/* TAB 1: PROPIEDADES */}
                {activeTab === "props" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
                        <div>
                            <Label>Nombre de la VM</Label>
                            <input value={f.label} disabled={isReadOnly} onChange={e => u("label", e.target.value)} style={inp} />
                        </div>
                        <div>
                            <Label>Imagen de S.O.</Label>
                            <select value={f.image_id || ""} disabled={isReadOnly}
                                onChange={e => {
                                    const id = Number(e.target.value);
                                    const name = availableImages.find(i => i.id === id)?.name;
                                    setF(p => ({ ...p, image_id: id, image: name }));
                                }} style={inp}>
                                <option value="" disabled>Seleccione un S.O.…</option>
                                {availableImages?.map(i => (
                                    <option key={i.id} value={i.id}>
                                        {i.name} {i.az_name ? `— [${i.az_name.toLowerCase().includes("openstack") || i.az_name.toLowerCase().includes("cloud") ? "☁ Cloud" : "🖥 Linux"}]` : ""}
                                    </option>
                                ))}
                            </select>
                        </div>

                        {selectedImage && !selectedImage.cloud_init_support && (
                            <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "9px 12px", border: `1px solid ${T.border}` }}>
                                <Label>Credenciales <span style={{ fontWeight: 400, color: T.textFaint }}>(fijas — sin cloud-init)</span></Label>
                                <div style={{ fontSize: 11, color: T.text }}>
                                    {(selectedImage.default_username || selectedImage.default_password)
                                        ? <>
                                            <span style={{ fontWeight: 700 }}>Usuario:</span> {selectedImage.default_username || "(desconocido)"} |{" "}
                                            <span style={{ fontWeight: 700 }}>Pass:</span> {selectedImage.default_password || "(desconocido)"}
                                          </>
                                        : "Se desconocen las credenciales por defecto de esta imagen."}
                                </div>
                                <div style={{ fontSize: 9, color: T.textFaint, marginTop: 4 }}>
                                    Esta imagen no soporta cloud-init: las credenciales vienen fijas y no se pueden personalizar.
                                </div>
                            </div>
                        )}

                        {!!selectedImage?.cloud_init_support && !isReadOnly && (
                            <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "10px 12px", border: `1px solid ${T.border}` }}>
                                <Label>Credenciales VM <span style={{ fontWeight: 400, color: T.textFaint }}>(cloud-init)</span></Label>
                                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                                    {[["vm_user","Usuario", f.image?.toLowerCase().split("-")[0]||"ubuntu"],
                                      ["vm_password","Contraseña","pucp2026"]].map(([k,lbl,ph]) => (
                                        <div key={k}>
                                            <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>{lbl}</div>
                                            <input value={f[k]||""} placeholder={ph} onChange={e => u(k, e.target.value)} style={{ ...inp, fontSize: 12 }} />
                                        </div>
                                    ))}
                                </div>
                                <div style={{ fontSize: 9, color: T.textFaint, marginTop: 6 }}>
                                    Vacío = nombre de imagen como usuario y "pucp2026" como contraseña.
                                </div>
                            </div>
                        )}

                        {!!selectedImage?.cloud_init_support && isReadOnly && (node.vm_user || node.vm_password) && (
                            <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "9px 12px", border: `1px solid ${T.border}` }}>
                                <Label>Credenciales</Label>
                                <div style={{ fontSize: 11, color: T.text }}>
                                    <span style={{ fontWeight: 700 }}>Usuario:</span> {node.vm_user||"(imagen)"} |{" "}
                                    <span style={{ fontWeight: 700 }}>Pass:</span> {node.vm_password||"pucp2026"}
                                </div>
                            </div>
                        )}

                        <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "10px 12px", border: `1px solid ${T.border}` }}>
                            <Label>Recursos</Label>

                            {/* Selector de flavor */}
                            {!isReadOnly && (
                                <div style={{ marginBottom: 9 }}>
                                    <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>Flavor</div>
                                    <select value={f.flavor_id || (pickedCustom ? "custom" : "")}
                                        onChange={e => {
                                            const v = e.target.value;
                                            setPickedCustom(v === "custom");
                                            applyFlavor(v && v !== "custom" ? flavors.find(x => x.id === Number(v)) : null);
                                        }}
                                        style={{ ...inp, fontSize: 12, cursor: "pointer" }}>
                                        <option value="" disabled hidden>Selecciona un flavor…</option>
                                        {flavors.map(fl => (
                                            <option key={fl.id} value={fl.id}>
                                                {fl.name} — {fl.vcpus}c / {Math.round(fl.ram_mb)}MB / {Math.round(fl.disk_gb)}GB
                                                {fl.visibility === "global" ? " 🌐" : fl.visibility === "project" ? " 👥" : ""}
                                            </option>
                                        ))}
                                        <option value="custom">⚙ Personalizado (recursos libres)</option>
                                    </select>
                                </div>
                            )}

                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                                {[["vcores","vCPU",1,16,1],["ram","RAM MB",128,16384,128],["disk","Disk GB",1,500,1]].map(([k,l,mn,mx,st]) => (
                                    <div key={k}>
                                        <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>{l}</div>
                                        <input type="number" value={f[k]} min={mn} max={mx} step={st} disabled={isReadOnly || usingFlavor}
                                            onChange={e => u(k, Number(e.target.value))}
                                            style={{ ...inp, padding: "6px 4px", textAlign: "center", fontWeight: 800, color: T.accent, fontSize: 13, opacity: usingFlavor ? 0.65 : 1 }} />
                                    </div>
                                ))}
                            </div>
                            {usingFlavor && !isReadOnly && (
                                <div style={{ fontSize: 9, color: T.textFaint, marginTop: 7, lineHeight: 1.5, display: "flex", gap: 5 }}>
                                    <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                    Recursos definidos por el flavor «{selectedFlavor?.name}». Elige «Personalizado» para editarlos manualmente.
                                </div>
                            )}
                            {isDeployed && editMode && (
                                <div style={{ fontSize: 9, color: T.textFaint, marginTop: 7, lineHeight: 1.5, display: "flex", gap: 5 }}>
                                    <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                    Los recursos de una VM ya desplegada no se pueden redimensionar en caliente. Para cambiarlos, destruye y vuelve a desplegar el slice.
                                </div>
                            )}
                        </div>
                        <div style={{ height: 2 }} />
                    </div>
                )}

                {/* TAB 2: RED Y SEGURIDAD */}
                {activeTab === "net" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

                        {/* Firewall */}
                        <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                    <Shield size={13} color={T.accent} />
                                    <Label style={{ marginBottom: 0 }}>Firewall Interno</Label>
                                </div>
                                {!isReadOnly && (
                                    <button onClick={addRule} style={{
                                        fontSize: 10, fontWeight: 700, fontFamily: "inherit",
                                        background: T.accentLight, color: T.accent,
                                        border: `1px solid ${T.accent}44`, borderRadius: 6,
                                        padding: "3px 9px", cursor: "pointer",
                                        display: "flex", alignItems: "center", gap: 4,
                                    }}>
                                        <Plus size={10} /> Añadir Regla
                                    </button>
                                )}
                            </div>

                            {f.firewall_rules.length > 0 && (
                                <div style={{ display: "grid", gridTemplateColumns: "90px 1fr 28px", gap: 6, marginBottom: 6 }}>
                                    {["Protocolo","Puerto",""].map(h => (
                                        <div key={h} style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.06em" }}>{h}</div>
                                    ))}
                                </div>
                            )}

                            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                                {f.firewall_rules.map(rule => (
                                    <div key={rule.id} style={{ display: "grid", gridTemplateColumns: "90px 1fr 28px", gap: 6, alignItems: "center" }}>
                                        <select value={rule.protocol} disabled={isReadOnly}
                                            onChange={e => updateRule(rule.id, "protocol", e.target.value)}
                                            style={{ ...inp, fontSize: 11, padding: "5px 4px", cursor: "pointer" }}>
                                            {["TCP","UDP","ICMP"].map(p => <option key={p}>{p}</option>)}
                                        </select>
                                        <input
                                            type={rule.protocol === "ICMP" ? "text" : "number"}
                                            value={rule.protocol === "ICMP" ? "—" : rule.port}
                                            disabled={isReadOnly || rule.protocol === "ICMP"}
                                            placeholder={rule.protocol === "ICMP" ? "—" : "ej. 22"}
                                            min={1} max={65535}
                                            onChange={e => updateRule(rule.id, "port", e.target.value)}
                                            style={{ ...inp, fontSize: 12, textAlign: "center", background: rule.protocol === "ICMP" ? T.surfaceElevated : undefined }}
                                        />
                                        {!isReadOnly ? (
                                            <button onClick={() => deleteRule(rule.id)} style={{
                                                background: T.redLight, border: `1px solid ${T.red}22`,
                                                borderRadius: 6, cursor: "pointer", color: T.red,
                                                display: "flex", alignItems: "center", justifyContent: "center", padding: "5px",
                                            }}>
                                                <X size={11} />
                                            </button>
                                        ) : <div />}
                                    </div>
                                ))}
                                {f.firewall_rules.length === 0 && (
                                    <div style={{ fontSize: 11, color: T.textFaint, textAlign: "center", padding: "10px 0" }}>
                                        {isReadOnly ? "Sin reglas configuradas" : "Sin reglas — haz clic en \"Añadir Regla\""}
                                    </div>
                                )}
                            </div>
                            <div style={{ fontSize: 9, color: T.textFaint, marginTop: 8, lineHeight: 1.5, display: "flex", gap: 5 }}>
                                <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                El tráfico no listado es bloqueado por defecto.
                            </div>
                        </div>

                        {/* Direcciones IP de interfaces de enlace — requiere cloud-init con network-config v2 */}
                        <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                                <Network size={13} color={T.accent} />
                                <Label style={{ marginBottom: 0 }}>Direcciones IP de enlaces</Label>
                            </div>

                            {selectedImage && !selectedImage.cloud_init_support ? (
                                <div style={{ fontSize: 10, color: T.textFaint, background: T.surface, border: `1px dashed ${T.border}`, borderRadius: 6, padding: "8px 10px", lineHeight: 1.5, display: "flex", gap: 6, alignItems: "flex-start" }}>
                                    <Info size={11} style={{ flexShrink: 0, marginTop: 1 }} />
                                    <span>
                                        Esta imagen no soporta configuración automática de red por cloud-init.
                                        Las interfaces de enlace quedarán sin IP; configúralas dentro de la VM
                                        con <span style={{ fontFamily: "monospace", color: T.accent }}>sudo ip addr add …</span>.
                                    </span>
                                </div>
                            ) : (
                                <>
                                    {myEdges.length === 0 && (
                                        <div style={{ fontSize: 11, color: T.textFaint, textAlign: "center", padding: "10px 0" }}>
                                            Esta VM no tiene enlaces conectados.
                                        </div>
                                    )}

                                    {myEdges.map(e => {
                                        const val = (f.link_ips || {})[e.id] || "";
                                        const invalid = val.trim() !== "" && !CIDR_RE.test(val.trim());
                                        return (
                                            <div key={e.id} style={{ display: "grid", gridTemplateColumns: "64px 1fr", gap: 8, alignItems: "center", marginBottom: 7 }}>
                                                <div style={{ fontSize: 11, fontWeight: 800, color: T.accent, fontFamily: "monospace" }}>{ifaceLabelFor(e)}</div>
                                                <input
                                                    type="text"
                                                    value={val}
                                                    disabled={isReadOnly}
                                                    placeholder="sin IP (ej. 192.168.10.1/24)"
                                                    onChange={ev => setLinkIp(e.id, ev.target.value)}
                                                    style={{ ...inp, fontSize: 12, borderColor: invalid ? T.red : undefined }}
                                                />
                                            </div>
                                        );
                                    })}

                                    {myEdges.length > 0 && (
                                        <div style={{ fontSize: 9, color: T.textFaint, marginTop: 6, lineHeight: 1.5, display: "flex", gap: 5 }}>
                                            <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                            Vacío = la interfaz queda sin IP (solo capa 2). La IP se aplica al desplegar; en enlaces añadidos a una VM ya activa, configúrala dentro de la VM.
                                        </div>
                                    )}
                                </>
                            )}
                        </div>

                        {/* Acceso de Red */}
                        <div style={{ background: T.surfaceElevated, borderRadius: 9, padding: "11px 12px", border: `1px solid ${T.border}` }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
                                <Globe size={13} color={T.accent} />
                                <Label style={{ marginBottom: 0 }}>Acceso de Red</Label>
                            </div>

                            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: isReadOnly ? "default" : "pointer" }}>
                                <input type="checkbox" disabled={isReadOnly}
                                    checked={f.internet_access === 1}
                                    onChange={e => setF(prev => ({
                                        ...prev,
                                        internet_access: e.target.checked ? 1 : 0,
                                        external_ip: e.target.checked ? prev.external_ip : "",
                                    }))}
                                    style={{ accentColor: T.accent, width: 14, height: 14, marginTop: 2, flexShrink: 0 }} />
                                <div>
                                    <div style={{ fontSize: 12, fontWeight: 700, color: T.text }}>Habilitar salida a Internet (NAT)</div>
                                    <div style={{ fontSize: 10, color: T.textMuted, marginTop: 2, lineHeight: 1.4 }}>
                                        La VM podrá iniciar conexiones salientes (apt-get, curl, pip, etc.)
                                    </div>
                                </div>
                            </label>

                            {f.internet_access === 1 && (
                                <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px dashed ${T.border}` }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                                        <Key size={12} color={T.accent} />
                                        <span style={{ fontSize: 11, fontWeight: 700, color: T.text }}>Acceso SSH externo (IP VPN)</span>
                                        <span style={{ fontSize: 8, color: T.textMuted, background: T.surfaceElevated, border: `1px solid ${T.border}`, borderRadius: 4, padding: "1px 5px" }}>OPCIONAL</span>
                                    </div>
                                    <div style={{ fontSize: 11, color: T.textMuted, lineHeight: 1.5, marginBottom: 10 }}>
                                        Al asignar una IP del pool, podrás conectarte a esta VM por SSH desde tu equipo local a través de la VPN de la universidad.
                                    </div>
                                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 5 }}>
                                        <div style={{ fontSize: 10, fontWeight: 700, color: T.textMuted }}>IP del Pool {zoneIdNum === 2 ? "(10.60.16.X)" : "(10.60.15.X)"}</div>
                                        {!f.external_ip && <span style={{ fontSize: 8, color: T.textMuted, fontStyle: "italic" }}>Sin acceso inbound si no se asigna</span>}
                                    </div>
                                    <select value={f.external_ip||""} disabled={isReadOnly}
                                        onChange={e => {
                                            const val = e.target.value;
                                            setF(prev => {
                                                const next = { ...prev, external_ip: val };
                                                // Al asignar la IP por primera vez, sembrar TCP/22 en las
                                                // reglas de entrada — sin esto el SSH prometido acá abajo
                                                // quedaría bloqueado por el deny-by-default de esas reglas.
                                                if (val && !prev.external_ip) {
                                                    const hasSsh = prev.ingress_rules.some(r => r.protocol === "TCP" && String(r.port) === "22");
                                                    if (!hasSsh) next.ingress_rules = [...prev.ingress_rules, { id: `r${Date.now()}`, protocol: "TCP", port: 22 }];
                                                }
                                                return next;
                                            });
                                        }}
                                        style={{ ...inp, fontSize: 12, cursor: "pointer" }}>
                                        <option value="">— Sin IP VPN (solo NAT saliente) —</option>
                                        <option value="random">— IP aleatoria (el sistema elige del pool) —</option>
                                        {availableIps.map(ip => <option key={ip} value={ip}>{ip}</option>)}
                                    </select>
                                    {f.external_ip && (
                                        <div style={{ fontSize: 10, color: T.accent, marginTop: 6, background: T.accentLight, borderRadius: 6, padding: "6px 9px", lineHeight: 1.5 }}>
                                            <CheckCircle size={11} style={{ display: "inline", marginRight: 4 }} />
                                            IP VPN: <strong>{f.external_ip === "random" ? "Asignación automática" : f.external_ip}</strong><br />
                                            <code style={{ fontFamily: "monospace", fontSize: 10 }}>ssh usuario@{f.external_ip === "random" ? "<IP_ASIGNADA>" : f.external_ip}</code>
                                        </div>
                                    )}
                                </div>
                            )}

                            {f.internet_access === 1 && f.external_ip && (
                                <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px dashed ${T.border}` }}>
                                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                            <Shield size={12} color={T.accent} />
                                            <span style={{ fontSize: 11, fontWeight: 700, color: T.text }}>Reglas de Entrada desde Internet</span>
                                        </div>
                                        {!isReadOnly && (
                                            <button onClick={addIngressRule} style={{
                                                fontSize: 10, fontWeight: 700, fontFamily: "inherit",
                                                background: T.accentLight, color: T.accent,
                                                border: `1px solid ${T.accent}44`, borderRadius: 6,
                                                padding: "3px 9px", cursor: "pointer",
                                                display: "flex", alignItems: "center", gap: 4,
                                            }}>
                                                <Plus size={10} /> Añadir Regla
                                            </button>
                                        )}
                                    </div>

                                    {f.ingress_rules.length > 0 && (
                                        <div style={{ display: "grid", gridTemplateColumns: "90px 1fr 28px", gap: 6, marginBottom: 6 }}>
                                            {["Protocolo","Puerto",""].map(h => (
                                                <div key={h} style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.06em" }}>{h}</div>
                                            ))}
                                        </div>
                                    )}

                                    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                                        {f.ingress_rules.map(rule => (
                                            <div key={rule.id} style={{ display: "grid", gridTemplateColumns: "90px 1fr 28px", gap: 6, alignItems: "center" }}>
                                                <select value={rule.protocol} disabled={isReadOnly}
                                                    onChange={e => updateIngressRule(rule.id, "protocol", e.target.value)}
                                                    style={{ ...inp, fontSize: 11, padding: "5px 4px", cursor: "pointer" }}>
                                                    {["TCP","UDP","ICMP"].map(p => <option key={p}>{p}</option>)}
                                                </select>
                                                <input
                                                    type={rule.protocol === "ICMP" ? "text" : "number"}
                                                    value={rule.protocol === "ICMP" ? "—" : rule.port}
                                                    disabled={isReadOnly || rule.protocol === "ICMP"}
                                                    placeholder={rule.protocol === "ICMP" ? "—" : "ej. 80"}
                                                    min={1} max={65535}
                                                    onChange={e => updateIngressRule(rule.id, "port", e.target.value)}
                                                    style={{ ...inp, fontSize: 12, textAlign: "center", background: rule.protocol === "ICMP" ? T.surfaceElevated : undefined }}
                                                />
                                                {!isReadOnly ? (
                                                    <button onClick={() => deleteIngressRule(rule.id)} style={{
                                                        background: T.redLight, border: `1px solid ${T.red}22`,
                                                        borderRadius: 6, cursor: "pointer", color: T.red,
                                                        display: "flex", alignItems: "center", justifyContent: "center", padding: "5px",
                                                    }}>
                                                        <X size={11} />
                                                    </button>
                                                ) : <div />}
                                            </div>
                                        ))}
                                        {f.ingress_rules.length === 0 && (
                                            <div style={{ fontSize: 11, color: T.textFaint, textAlign: "center", padding: "10px 0" }}>
                                                {isReadOnly ? "Sin reglas configuradas" : "Sin reglas — haz clic en \"Añadir Regla\""}
                                            </div>
                                        )}
                                    </div>
                                    <div style={{ fontSize: 9, color: T.textFaint, marginTop: 8, lineHeight: 1.5, display: "flex", gap: 5 }}>
                                        <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                        Solo estos puertos son alcanzables desde la IP externa/VPN. Todo lo demás
                                        se deniega — distinto del Firewall Interno, que solo controla el tráfico
                                        entre VMs del propio slice.
                                    </div>
                                </div>
                            )}
                            {f.internet_access === 0 && (
                                <div style={{ fontSize: 10, color: T.textFaint, marginTop: 8, lineHeight: 1.4, display: "flex", gap: 5, alignItems: "flex-start" }}>
                                    <Info size={10} style={{ flexShrink: 0, marginTop: 1 }} />
                                    Activa el NAT para poder asignar una IP VPN de acceso externo.
                                </div>
                            )}
                        </div>

                        <div style={{ height: 2 }} />
                    </div>
                )}
            </div>

            {/* Sticky footer */}
            <div style={{ flexShrink: 0, padding: "9px 14px 12px", borderTop: `1px solid ${T.border}`, background: T.surface }}>
                {isReadOnly ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <button
                            onClick={() => onOpenConsole({ workerIp: node.worker_ip, workerPort: node.worker_port, vncPort: node.vnc_port, vnc_url: node.vnc_url, sliceId, vmId: node.id, apiFetch })}
                            disabled={sliceStatus !== "ACTIVE"}
                            style={btnBase({ width: "100%", background: "#18181b", color: "#ffffff", border: "none", padding: "8px 0", opacity: sliceStatus === "ACTIVE" ? 1 : 0.45,
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                            <Terminal size={14} color="#ffffff" /> Abrir Consola Web
                        </button>
                        <button onClick={loadTelemetry}
                            disabled={sliceStatus !== "ACTIVE" || telemetry?.loading}
                            style={btnBase({ width: "100%", background: T.surface, color: T.accent, border: `1px solid ${T.accent}`,
                                opacity: sliceStatus === "ACTIVE" ? 1 : 0.45,
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                            <BarChart2 size={14} /> {telemetry?.loading ? "Consultando…" : "Ver Telemetría"}
                        </button>

                        {telemetry && !telemetry.loading && (
                            <div style={{
                                fontSize: 11, borderRadius: 8, padding: "8px 10px",
                                background: telemetry.available ? T.accentLight : T.redLight,
                                border: `1px solid ${telemetry.available ? T.accent + "33" : T.red + "33"}`,
                                color: telemetry.available ? T.text : T.red,
                            }}>
                                {telemetry.available ? (
                                    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                                        {telemetry.cpu_pct != null && (
                                            <div><b>CPU:</b> {telemetry.cpu_pct}%</div>
                                        )}
                                        {telemetry.ram_pct != null && (
                                            <div><b>RAM:</b> {telemetry.ram_pct}%{telemetry.ram_used_mb != null ? ` (${telemetry.ram_used_mb} MB)` : ""}
                                                {telemetry.ram_max_mb != null ? ` / ${telemetry.ram_max_mb} MB` : ""}</div>
                                        )}
                                        {telemetry.cpu_pct == null && telemetry.num_cpus != null && (
                                            <div style={{ fontSize: 9.5, color: T.textFaint }}>
                                                Nova no reporta %CPU instantáneo, solo tiempo acumulado ({telemetry.num_cpus} vCPU).
                                            </div>
                                        )}
                                        {telemetry.uptime_seconds != null && (
                                            <div><b>Uptime:</b> {Math.floor(telemetry.uptime_seconds / 3600)}h {Math.floor((telemetry.uptime_seconds % 3600) / 60)}m</div>
                                        )}
                                        <div style={{ fontSize: 9, color: T.textFaint, marginTop: 2 }}>
                                            Fuente: {telemetry.source === "nova_diagnostics" ? "Nova (OpenStack)" : "QEMU vía SSH"}
                                        </div>
                                    </div>
                                ) : (
                                    <div style={{ display: "flex", gap: 5 }}>
                                        <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />
                                        <span>{telemetry.reason || "Telemetría no disponible para esta VM."}</span>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* En Modo Edición: permitir eliminar esta VM ya desplegada (REQ-US-14) */}
                        {editMode && (
                            <button onClick={() => { onDelete(node.id); onClose(); }}
                                style={btnBase({ width: "100%", background: T.redLight, color: T.red, border: `1px solid ${T.red}44`, boxShadow: "none",
                                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                                <Trash2 size={14} /> Eliminar del Slice
                            </button>
                        )}
                    </div>
                ) : (
                    <div style={{ display: "flex", gap: 8 }}>
                        <button onClick={() => { onSave(f); onClose(); }}
                            style={btnBase({ flex: 1, background: T.accent, color: "#fff", border: "none",
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                            <Save size={14} /> Guardar
                        </button>
                        <button onClick={() => { onDelete(node.id); onClose(); }}
                            style={btnBase({ background: T.redLight, color: T.red, border: `1px solid ${T.red}33`,
                                display: "flex", alignItems: "center", justifyContent: "center", padding: "8px 12px" })}>
                            <Trash2 size={14} />
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};
