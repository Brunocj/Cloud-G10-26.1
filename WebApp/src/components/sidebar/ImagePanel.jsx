import { useState, useRef } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Upload, RefreshCcw, Trash2, CheckCircle, Circle, Lock, Package, Loader } from "../ui/Icon";

export const ImagePanel = ({ fullImages, onRefresh, flash, refreshImageList, apiFetch, user }) => {
    const [uploading,      setUploading]      = useState(false);
    const [gcRunning,      setGcRunning]      = useState(false);
    const [uploadForm,     setUploadForm]     = useState({ name: "", file: null, isGeneral: false, azId: 1, cloudInit: true, defaultUsername: "", defaultPassword: "" });
    const [showUpload,     setShowUpload]     = useState(false);
    const [azList,         setAzList]         = useState([{ id: 1, name: "Linux Cluster" }, { id: 2, name: "OpenStack" }]);
    const [uploadProgress, setUploadProgress] = useState(null); // null=idle, 0-100=subiendo
    const fileRef  = useRef(null);
    const abortRef = useRef(null); // AbortController activo durante la subida

    // Fetch AZ list when upload panel is opened
    useState(() => {
        if (!apiFetch) return;
        apiFetch("/slices/utils/availability-zones")
            .then(r => r.ok ? r.json() : null)
            .then(data => { if (Array.isArray(data) && data.length > 0) setAzList(data); })
            .catch(() => {});
    }, [apiFetch]);

    const handleUpload = async () => {
        if (!uploadForm.file || !uploadForm.name.trim()) {
            flash("Completa el nombre y selecciona un archivo", "error"); return;
        }
        if (!uploadForm.cloudInit && !(uploadForm.defaultUsername.trim() && uploadForm.defaultPassword.trim())) {
            flash("Esta imagen no soporta cloud-init: indica el usuario y contraseña por defecto", "error"); return;
        }

        const controller = new AbortController();
        abortRef.current = controller;
        setUploading(true);
        setUploadProgress(0);

        const fd = new FormData();
        fd.append("name",                uploadForm.name.trim());
        fd.append("is_general",          uploadForm.isGeneral ? 1 : 0);
        fd.append("availability_zone_id", uploadForm.azId);
        fd.append("cloud_init_support",  uploadForm.cloudInit ? 1 : 0);
        if (!uploadForm.cloudInit) {
            fd.append("default_username", uploadForm.defaultUsername.trim());
            fd.append("default_password", uploadForm.defaultPassword.trim());
        }
        fd.append("file", uploadForm.file);

        try {
            const res = await apiFetch("/slices/utils/images/upload", {
                method: "POST",
                body: fd,
                onUploadProgress: (pct) => setUploadProgress(pct),
                timeout: 300,          // 5 minutos — suficiente para Glance via SOCKS5
                signal: controller.signal,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al subir");
            flash(`Imagen '${uploadForm.name}' subida correctamente.`);
            setShowUpload(false);
            setUploadForm({ name: "", file: null, isGeneral: false, azId: 1, cloudInit: true, defaultUsername: "", defaultPassword: "" });
            onRefresh();
            if (refreshImageList) refreshImageList();
        } catch (e) {
            if (e.message !== "Subida cancelada") flash(e.message, "error");
        } finally {
            setUploading(false);
            setUploadProgress(null);
            abortRef.current = null;
        }
    };

    const handleCancelUpload = () => {
        if (abortRef.current) abortRef.current.abort();
    };

    const handleDelete = async (img) => {
        if (!window.confirm(`¿Eliminar la imagen '${img.name}'?`)) return;
        try {
            const res  = await apiFetch(`/slices/utils/images/${img.id}`, { method: "DELETE" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al eliminar");
            flash(data.message);
            onRefresh();
        } catch (e) { flash(e.message, "error"); }
    };

    const handleGC = async () => {
        setGcRunning(true);
        try {
            const res  = await apiFetch("/slices/utils/images/gc/run", { method: "POST" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error GC");
            flash(data.message);
            setTimeout(() => { onRefresh(); if (refreshImageList) refreshImageList(); }, 3000);
        } catch (e) { flash(e.message, "error"); }
        finally { setTimeout(() => setGcRunning(false), 3500); }
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
            {/* Action buttons */}
            <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, display: "flex", gap: 6, flexShrink: 0 }}>
                <button onClick={() => setShowUpload(v => !v)}
                    style={btnBase({ flex: 1, fontSize: 11, padding: "6px 8px", background: T.accent, color: "#fff", border: "none",
                        display: "flex", alignItems: "center", justifyContent: "center", gap: 5 })}>
                    <Upload size={12} /> Subir Imagen
                </button>
                {(user?.role === "admin" || user?.role === "superAdmin") && (
                    <button onClick={handleGC} disabled={gcRunning}
                        style={btnBase({ flex: 1, fontSize: 11, padding: "6px 8px", background: gcRunning ? T.surfaceElevated : T.yellowLight, color: gcRunning ? T.textMuted : T.yellow, border: `1px solid ${T.yellow}44`,
                            display: "flex", alignItems: "center", justifyContent: "center", gap: 5 })}>
                        {gcRunning
                            ? <><Loader size={11} style={{ animation: "spin 0.8s linear infinite" }} /> Limpiando...</>
                            : <><RefreshCcw size={11} /> Ejecutar GC</>}
                    </button>
                )}
            </div>

            {/* Scrollable Container for both Form and List */}
            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
                {/* Upload form (collapsible) */}
                {showUpload && (
                    <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, background: T.accentLight, flexShrink: 0 }}>
                        <Label>Nombre de la imagen</Label>
                        <input value={uploadForm.name}
                            onChange={e => setUploadForm(p => ({ ...p, name: e.target.value }))}
                            placeholder="ej: ubuntu-custom-v1" style={{ ...inp, marginBottom: 8 }} />
                        <Label>Archivo (.qcow2 / .img / .iso)</Label>
                        <input type="file" accept=".qcow2,.img,.iso" ref={fileRef}
                            onChange={e => setUploadForm(p => ({ ...p, file: e.target.files[0] }))}
                            style={{ fontSize: 11, color: T.text, marginBottom: 8, width: "100%" }} />
                        
                        <Label>Zona de Disponibilidad</Label>
                        <select value={uploadForm.azId}
                            onChange={e => setUploadForm(p => ({ ...p, azId: Number(e.target.value) }))}
                            style={{ ...inp, marginBottom: 8 }}>
                            {azList.map(az => <option key={az.id} value={az.id}>{az.name}</option>)}
                        </select>
                        
                        {(user?.role === "admin" || user?.role === "superAdmin") && (
                            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: T.text, marginBottom: 8, cursor: "pointer" }}>
                                <input type="checkbox" checked={uploadForm.isGeneral}
                                    onChange={e => setUploadForm(p => ({ ...p, isGeneral: e.target.checked }))} />
                                ¿Hacer imagen pública general del sistema?
                            </label>
                        )}

                        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: T.text, marginBottom: 8, cursor: "pointer" }}>
                            <input type="checkbox" checked={uploadForm.cloudInit}
                                onChange={e => setUploadForm(p => ({ ...p, cloudInit: e.target.checked }))} />
                            ¿Esta imagen soporta cloud-init?
                        </label>

                        {!uploadForm.cloudInit && (
                            <div style={{ background: T.surfaceElevated, borderRadius: 8, padding: "8px 10px", marginBottom: 8, border: `1px solid ${T.border}` }}>
                                <div style={{ fontSize: 9.5, color: T.textFaint, marginBottom: 6 }}>
                                    Sin cloud-init, las credenciales vienen fijas en la imagen — indícalas para mostrarlas en el panel de cada VM.
                                </div>
                                <Label>Usuario por defecto</Label>
                                <input value={uploadForm.defaultUsername}
                                    onChange={e => setUploadForm(p => ({ ...p, defaultUsername: e.target.value }))}
                                    placeholder="ej: cirros" style={{ ...inp, marginBottom: 8 }} />
                                <Label>Contraseña por defecto</Label>
                                <input value={uploadForm.defaultPassword}
                                    onChange={e => setUploadForm(p => ({ ...p, defaultPassword: e.target.value }))}
                                    placeholder="ej: gocubsgo" style={{ ...inp, marginBottom: 8 }} />
                            </div>
                        )}

                        {/* Barra de progreso — visible mientras se sube */}
                        {uploadProgress !== null && (
                            <div style={{ marginBottom: 8 }}>
                                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: T.textMuted, marginBottom: 4 }}>
                                    <span>
                                        {uploadForm.azId === 2
                                            ? "☁ Subiendo a OpenStack Glance..."
                                            : "🖥 Copiando al NFS..."}
                                    </span>
                                    <span style={{ fontWeight: 700, color: T.accent }}>{uploadProgress}%</span>
                                </div>
                                <div style={{ height: 6, background: T.border, borderRadius: 99, overflow: "hidden" }}>
                                    <div style={{
                                        height: "100%",
                                        width: `${uploadProgress}%`,
                                        background: `linear-gradient(90deg, ${T.accent}, #7c3aed)`,
                                        borderRadius: 99,
                                        transition: "width 0.3s ease",
                                    }} />
                                </div>
                                {uploadProgress === 100 && (
                                    <div style={{ fontSize: 9.5, color: T.textMuted, marginTop: 4, textAlign: "center" }}>
                                        Procesando en el servidor...
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Botones Confirmar / Cancelar */}
                        <div style={{ display: "flex", gap: 6 }}>
                            <button onClick={handleUpload} disabled={uploading}
                                style={btnBase({ flex: 1, background: T.accent, color: "#fff", border: "none", opacity: uploading ? 0.6 : 1,
                                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                                {uploading
                                    ? <><Loader size={13} style={{ animation: "spin 0.8s linear infinite" }} /> Subiendo...</>
                                    : <><CheckCircle size={13} /> Confirmar Subida</>}
                            </button>
                            {uploading && (
                                <button onClick={handleCancelUpload}
                                    style={btnBase({ padding: "6px 12px", background: T.redLight, color: T.red,
                                        border: `1px solid ${T.red}44`, display: "flex", alignItems: "center", gap: 5 })}>
                                    ✕ Cancelar
                                </button>
                            )}
                        </div>
                    </div>
                )}

                {/* Image list */}
                <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
                    {fullImages.length === 0 && (
                        <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>Sin imágenes registradas</div>
                    )}
                    {fullImages.map(img => (
                        <div key={img.id} style={{ background: T.surface, border: `1px solid ${img.in_use ? T.accent + "44" : T.border}`, borderRadius: 10, padding: "9px 10px", boxShadow: T.shadow }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: 12, fontWeight: 700, color: T.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                                            display: "flex", alignItems: "center", gap: 5 }}>
                                        {img.is_general
                                            ? <Lock size={11} color={T.textMuted} />
                                            : <Package size={11} color={T.accent} />}
                                        {img.name}
                                        {/* Badge de Zona de Disponibilidad */}
                                        {img.az_name && (
                                            <span style={{
                                                fontSize: 9, fontWeight: 700,
                                                padding: "1px 6px", borderRadius: 20,
                                                letterSpacing: "0.04em", flexShrink: 0,
                                                ...(img.az_name.toLowerCase().includes("openstack") || img.az_name.toLowerCase().includes("cloud")
                                                    ? { background: "#ff820022", color: "#ff8200", border: "1px solid #ff820044" }
                                                    : { background: "#0ea5e922", color: "#0ea5e9", border: "1px solid #0ea5e944" }
                                                ),
                                            }}>
                                                {img.az_name.toLowerCase().includes("openstack") || img.az_name.toLowerCase().includes("cloud")
                                                    ? "☁ Cloud"
                                                    : "🖥 Linux"}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: 10, color: T.textMuted, marginTop: 2, display: "flex", alignItems: "center", gap: 4 }}>
                                        {img.in_use
                                            ? <><CheckCircle size={10} color={T.accent} /><span style={{ color: T.accent, fontWeight: 600 }}>En uso ({img.active_vm_count} VM{img.active_vm_count > 1 ? "s" : ""})</span></>
                                            : <><Circle size={10} color={img.is_general ? T.textMuted : T.yellow} /><span style={{ color: img.is_general ? T.textMuted : T.yellow }}>Sin VMs activas</span></>}
                                    </div>
                                    <div style={{ fontSize: 9, color: T.textFaint, marginTop: 2, fontFamily: "monospace" }}>{img.path}</div>
                                </div>
                                {(img.is_general !== 1 || user?.role === "admin" || user?.role === "superAdmin") && (
                                    <button
                                        onClick={() => handleDelete(img)}
                                        disabled={img.in_use}
                                        title={img.in_use ? "No se puede eliminar: tiene VMs activas" : "Eliminar imagen"}
                                        style={btnBase({ padding: "4px 8px", fontSize: 13, marginLeft: 6, flexShrink: 0, cursor: img.in_use ? "not-allowed" : "pointer",
                                            background: img.in_use ? T.surfaceElevated : T.redLight,
                                            color: img.in_use ? T.textFaint : T.red,
                                            border: `1px solid ${img.in_use ? T.border : T.red + "44"}`,
                                            display: "flex", alignItems: "center" })}>
                                        <Trash2 size={13} />
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};
