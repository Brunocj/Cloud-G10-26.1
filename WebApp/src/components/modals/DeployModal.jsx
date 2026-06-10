import { useState } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Overlay } from "./Overlay";
import { Zap } from "../ui/Icon";

export const DeployModal = ({ nodes, edges, onDeploy, onClose }) => {
    const [name, setName] = useState(`slice-${Math.random().toString(36).slice(2, 6)}`);
    const totalRam  = nodes.reduce((s, n) => s + n.ram, 0);
    const ramLabel  = totalRam >= 1024 ? `${(totalRam / 1024).toFixed(1)} GB` : `${totalRam} MB`;

    return (
        <Overlay>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16, padding: 28, maxWidth: 400, width: "90%", boxShadow: T.shadowMd }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <Zap size={18} color={T.accent} /> Desplegar Slice
                </div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 20 }}>Asigne un nombre a su slice y confirme el despliegue.</div>
                <Label>Nombre del Slice</Label>
                <input value={name} onChange={e => setName(e.target.value)} style={inp} />

                {/* Summary */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, margin: "18px 0", background: T.accentLight, borderRadius: 10, padding: "14px 12px", border: `1px solid ${T.accent}33` }}>
                    {[["VMs", nodes.length], ["Enlaces", edges.length], ["vCPU", nodes.reduce((s, n) => s + n.vcores, 0)], ["RAM", ramLabel]].map(([l, v]) => (
                        <div key={l} style={{ textAlign: "center" }}>
                            <div style={{ fontSize: 20, fontWeight: 900, color: T.accent }}>{v}</div>
                            <div style={{ fontSize: 9, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                        </div>
                    ))}
                </div>

                <div style={{ display: "flex", gap: 10 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancelar</button>
                    <button
                        onClick={() => onDeploy(name)}
                        disabled={!name.trim() || nodes.length === 0}
                        style={btnBase({ flex: 2, background: T.accent, color: "#fff", border: "none", opacity: (!name.trim() || nodes.length === 0) ? 0.5 : 1,
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                        <Zap size={14} /> Desplegar Ahora
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
