import { useState } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Overlay } from "./Overlay";
import { Save } from "../ui/Icon";

export const SaveDraftModal = ({ nodes, edges, onSave, onClose }) => {
    const [name, setName] = useState(`draft-${Math.random().toString(36).slice(2, 6)}`);
    return (
        <Overlay>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16, padding: 28, maxWidth: 380, width: "90%", boxShadow: T.shadowMd }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <Save size={17} color={T.accent} /> Save as Draft
                </div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 18 }}>Your topology will be saved. You can deploy it later.</div>
                <Label>Slice Name</Label>
                <input value={name} onChange={e => setName(e.target.value)} style={inp} />
                <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
                    <button onClick={onClose} style={btnBase({ flex: 1 })}>Cancel</button>
                    <button onClick={() => onSave(name)} disabled={!name.trim()}
                        style={btnBase({ flex: 2, background: T.accent, color: "#fff", border: "none", opacity: !name.trim() ? 0.5 : 1,
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                        <Save size={14} /> Save Draft
                    </button>
                </div>
            </div>
        </Overlay>
    );
};
