import { T, btnBase } from "../../theme/tokens";
import { Overlay } from "./Overlay";
import { Globe, Cloud, Server } from "../ui/Icon";

/**
 * SelectZoneModal — se muestra al crear un lienzo nuevo (o al limpiarlo).
 * Obliga a elegir la Zona de Disponibilidad objetivo ANTES de poder agregar
 * nodos, evitando que se mezclen VMs con imágenes incompatibles entre zonas.
 */
export const SelectZoneModal = ({ azList, onSelect }) => {
    const options = (azList && azList.length > 0) ? azList : [
        { id: 1, name: "Linux Cluster" },
        { id: 2, name: "OpenStack" },
    ];

    return (
        <Overlay>
            <div style={{
                background: T.surface, border: `1px solid ${T.border}`,
                borderRadius: 16, padding: 28, maxWidth: 420, width: "90%",
                boxShadow: T.shadowMd,
            }}>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.text, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <Globe size={18} color={T.accent} /> Elige la Zona de Disponibilidad
                </div>
                <div style={{ fontSize: 12, color: T.textMuted, marginBottom: 18, lineHeight: 1.5 }}>
                    Todas las VMs de este lienzo usarán esta zona. No podrás cambiarla después
                    sin limpiar el lienzo, para evitar combinar imágenes incompatibles.
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {options.map(az => (
                        <button key={az.id} onClick={() => onSelect(String(az.id))}
                            style={btnBase({
                                width: "100%", padding: "12px 14px", textAlign: "left",
                                background: T.surfaceElevated, border: `1px solid ${T.border}`,
                                display: "flex", alignItems: "center", gap: 10,
                            })}
                            onMouseEnter={e => e.currentTarget.style.borderColor = T.accent}
                            onMouseLeave={e => e.currentTarget.style.borderColor = T.border}>
                            {az.name.toLowerCase().includes("openstack") || az.name.toLowerCase().includes("cloud")
                                ? <Cloud size={16} color={T.accent} />
                                : <Server size={16} color={T.accent} />}
                            <span style={{ fontSize: 13, fontWeight: 700, color: T.text }}>{az.name}</span>
                        </button>
                    ))}
                </div>

                <button onClick={() => onSelect("")}
                    style={btnBase({ width: "100%", marginTop: 16, fontSize: 11, color: T.textMuted, background: "transparent", border: "none" })}>
                    No estoy seguro — decidir más tarde (sin restricción)
                </button>
            </div>
        </Overlay>
    );
};
