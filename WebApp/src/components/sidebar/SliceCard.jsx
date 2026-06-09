import { T, btnBase } from "../../theme/tokens";
import { Badge } from "../ui/Badge";
import { Trash2, Zap } from "../ui/Icon";

export const SliceCard = ({ slice, active, onClick, onDestroy, onDeploy }) => (
    <div
        onClick={onClick}
        style={{
            padding: "12px 14px", borderRadius: 10, cursor: "pointer",
            border: `1.5px solid ${active ? T.accent : T.border}`,
            background: active ? T.accentLight : T.surface,
            transition: "all 0.15s",
            boxShadow: active ? `0 0 0 3px ${T.accent}18` : T.shadow,
        }}>
        {/* Name + badge + destroy btn */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6 }}>
            <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{slice.name}</div>
                <Badge status={slice.status} />
            </div>
            {slice.status !== "TERMINATED" && (
                <button
                    onClick={e => { e.stopPropagation(); onDestroy(slice.id); }}
                    style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4, display: "flex" }}
                    title="Destroy slice">
                    <Trash2 size={15} />
                </button>
            )}
        </div>

        {/* Stats grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 5, marginTop: 6 }}>
            {[["VMs", slice.nodeCount], ["Links", slice.edgeCount], ["vCPU", slice.vcpus], ["RAM", slice.ramLabel]].map(([l, v]) => (
                <div key={l} style={{ textAlign: "center", background: T.surfaceElevated, borderRadius: 6, padding: "5px 2px", border: `1px solid ${T.border}` }}>
                    <div style={{ fontSize: 13, fontWeight: 800, color: T.accent }}>{v}</div>
                    <div style={{ fontSize: 8, color: T.textMuted, textTransform: "uppercase" }}>{l}</div>
                </div>
            ))}
        </div>

        {/* Deploy button (draft only) */}
        {slice.status === "Draft" && (
            <button
                onClick={e => { e.stopPropagation(); onDeploy(slice.id); }}
                style={btnBase({ width: "100%", marginTop: 10, background: T.accent, color: "#fff", border: "none", padding: "7px 0", fontSize: 12,
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                <Zap size={13} /> Request Deployment
            </button>
        )}
    </div>
);
