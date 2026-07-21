import { useState, useEffect } from "react";
import { T, btnBase, inp } from "../../theme/tokens";
import { Label } from "../ui/Label";
import { Plus, Save, Trash2, Cpu, MemoryStick, HardDrive, Globe, Users, Lock, Loader } from "../ui/Icon";
import { useConfirm } from "../../hooks/useConfirm";

const VISIBILITY_META = {
    global:  { icon: Globe, label: "Global",   color: "#ff8200" },
    project: { icon: Users, label: "Proyecto", color: "#0ea5e9" },
    private: { icon: Lock,  label: "Privado",  color: "#71717a" },
};

const emptyForm = { name: "", vcpus: 1, ram_mb: 1024, disk_gb: 10, visibility: "private", project_id: "" };

export const FlavorPanel = ({ apiFetch, user, flash }) => {
    const [askConfirm, confirmDialog] = useConfirm();
    const [flavors,   setFlavors]   = useState([]);
    const [loading,   setLoading]   = useState(true);
    const [projects,  setProjects]  = useState([]); // proyectos donde el usuario puede crear flavors 'project'
    const [showCreate, setShowCreate] = useState(false);
    const [creating,  setCreating]  = useState(false);
    const [form,      setForm]      = useState(emptyForm);

    const canGlobal  = user?.role === "admin" || user?.role === "superAdmin";
    const canProject = canGlobal || user?.role === "jefeProyecto";

    const loadFlavors = () => {
        if (!apiFetch) return;
        setLoading(true);
        apiFetch("/slices/utils/flavors")
            .then(r => r.ok ? r.json() : [])
            .then(data => setFlavors(Array.isArray(data) ? data : []))
            .catch(() => {})
            .finally(() => setLoading(false));
    };
    useEffect(loadFlavors, [apiFetch]);

    // Proyectos donde el usuario puede crear un flavor 'project' (jefe ahí, o admin = todos)
    useEffect(() => {
        if (!apiFetch || !canProject) return;
        apiFetch("/projects/eligible-for-deploy")
            .then(r => r.ok ? r.json() : [])
            .then(data => { if (Array.isArray(data)) setProjects(data.filter(p => p.direct_deploy)); })
            .catch(() => {});
    }, [apiFetch, canProject]);

    const resetForm = () => { setForm(emptyForm); setShowCreate(false); };

    const createFlavor = () => {
        if (!apiFetch || !form.name.trim() || creating) return;
        if (form.visibility === "project" && !form.project_id) {
            flash("Selecciona un proyecto para el flavor.", "error"); return;
        }
        setCreating(true);
        const body = {
            name: form.name.trim(),
            vcpus: Number(form.vcpus) || 1,
            ram_mb: Number(form.ram_mb) || 128,
            disk_gb: Number(form.disk_gb) || 1,
            visibility: form.visibility,
            project_id: form.visibility === "project" ? Number(form.project_id) : null,
        };
        apiFetch("/slices/utils/flavors", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
            .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); })
            .then(created => {
                setFlavors(prev => [...prev, created]);
                flash(`Flavor '${created.name}' creado correctamente.`);
                resetForm();
            })
            .catch(e => flash(e?.detail || "No se pudo crear el flavor.", "error"))
            .finally(() => setCreating(false));
    };

    const deleteFlavor = (fl) => {
        if (!apiFetch) return;
        askConfirm({
            title: "Eliminar flavor",
            msg: `¿Eliminar el flavor «${fl.name}»? Las VMs que ya lo usaron conservan sus recursos.`,
            onOk: () => apiFetch(`/slices/utils/flavors/${fl.id}`, { method: "DELETE" })
                .then(r => { if (!r.ok) return r.json().then(e => Promise.reject(e)); return r.json(); })
                .then(data => {
                    setFlavors(prev => prev.filter(x => x.id !== fl.id));
                    flash(data.message);
                })
                .catch(e => flash(e?.detail || "No se pudo eliminar el flavor.", "error")),
        });
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
            {/* Action buttons */}
            <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
                <button onClick={() => setShowCreate(v => !v)}
                    style={btnBase({ width: "100%", fontSize: 11, padding: "6px 8px", background: T.accent, color: "#fff", border: "none",
                        display: "flex", alignItems: "center", justifyContent: "center", gap: 5 })}>
                    <Plus size={12} /> {showCreate ? "Cancelar" : "Nuevo Flavor"}
                </button>
            </div>

            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
                {/* Create form */}
                {showCreate && (
                    <div style={{ padding: "10px 12px", borderBottom: `1px solid ${T.border}`, background: T.accentLight, flexShrink: 0 }}>
                        <Label>Nombre del flavor</Label>
                        <input value={form.name}
                            onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                            placeholder="ej: mini, mediano-8gb" style={{ ...inp, marginBottom: 8 }} />

                        <Label>Recursos</Label>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 8 }}>
                            {[["vcpus","vCPU",1,128,1],["ram_mb","RAM MB",128,262144,128],["disk_gb","Disk GB",1,2000,1]].map(([k,l,mn,mx,st]) => (
                                <div key={k}>
                                    <div style={{ fontSize: 9, fontWeight: 700, color: T.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 3 }}>{l}</div>
                                    <input type="number" value={form[k]} min={mn} max={mx} step={st}
                                        onChange={e => setForm(p => ({ ...p, [k]: e.target.value }))}
                                        style={{ ...inp, padding: "6px 4px", textAlign: "center", fontWeight: 800, color: T.accent, fontSize: 13 }} />
                                </div>
                            ))}
                        </div>

                        <Label>Visibilidad</Label>
                        <select value={form.visibility}
                            onChange={e => setForm(p => ({ ...p, visibility: e.target.value, project_id: "" }))}
                            style={{ ...inp, marginBottom: 8 }}>
                            <option value="private">Privado (solo yo)</option>
                            {canProject && <option value="project">De proyecto (miembros del proyecto)</option>}
                            {canGlobal  && <option value="global">Global (todos los usuarios)</option>}
                        </select>

                        {form.visibility === "project" && (
                            <>
                                <Label>Proyecto</Label>
                                <select value={form.project_id}
                                    onChange={e => setForm(p => ({ ...p, project_id: e.target.value }))}
                                    style={{ ...inp, marginBottom: 8 }}>
                                    <option value="" disabled>Selecciona un proyecto…</option>
                                    {projects.map(p => (
                                        <option key={p.project_id} value={p.project_id}>{p.project_name}</option>
                                    ))}
                                </select>
                                {projects.length === 0 && (
                                    <div style={{ fontSize: 9.5, color: T.textFaint, marginTop: -4, marginBottom: 8 }}>
                                        No lideras ningún proyecto todavía.
                                    </div>
                                )}
                            </>
                        )}

                        <button onClick={createFlavor} disabled={!form.name.trim() || creating}
                            style={btnBase({ width: "100%", background: T.accent, color: "#fff", border: "none", opacity: (!form.name.trim() || creating) ? 0.6 : 1,
                                display: "flex", alignItems: "center", justifyContent: "center", gap: 6 })}>
                            {creating ? <><Loader size={13} style={{ animation: "spin 0.8s linear infinite" }} /> Creando...</> : <><Save size={13} /> Crear Flavor</>}
                        </button>
                    </div>
                )}

                {/* Flavor list */}
                <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}>
                    {loading && (
                        <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>Cargando flavors…</div>
                    )}
                    {!loading && flavors.length === 0 && (
                        <div style={{ color: T.textFaint, fontSize: 12, textAlign: "center", padding: 16 }}>Sin flavors registrados</div>
                    )}
                    {flavors.map(fl => {
                        const meta = VISIBILITY_META[fl.visibility] || VISIBILITY_META.private;
                        const VisIcon = meta.icon;
                        return (
                            <div key={fl.id} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: "9px 10px", boxShadow: T.shadow }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: 12, fontWeight: 700, color: T.text, display: "flex", alignItems: "center", gap: 5 }}>
                                            {fl.name}
                                            <span style={{
                                                fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 20,
                                                letterSpacing: "0.04em", flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 3,
                                                background: `${meta.color}22`, color: meta.color, border: `1px solid ${meta.color}44`,
                                            }}>
                                                <VisIcon size={9} /> {meta.label}
                                            </span>
                                        </div>
                                        <div style={{ fontSize: 10, color: T.textMuted, marginTop: 4, display: "flex", alignItems: "center", gap: 10 }}>
                                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}><Cpu size={10} /> {fl.vcpus}c</span>
                                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}><MemoryStick size={10} /> {Math.round(fl.ram_mb)}MB</span>
                                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}><HardDrive size={10} /> {Math.round(fl.disk_gb)}GB</span>
                                        </div>
                                        <div style={{ fontSize: 9, color: T.textFaint, marginTop: 3 }}>
                                            {fl.provider_flavor_id
                                                ? "☁ Ya materializado en OpenStack Nova"
                                                : "⏳ Se creará en Nova recién al desplegarse en OpenStack"}
                                        </div>
                                    </div>
                                    {fl.editable && (
                                        <button onClick={() => deleteFlavor(fl)} title="Eliminar flavor"
                                            style={btnBase({ padding: "4px 8px", fontSize: 13, marginLeft: 6, flexShrink: 0,
                                                background: T.redLight, color: T.red, border: `1px solid ${T.red}44`,
                                                display: "flex", alignItems: "center" })}>
                                            <Trash2 size={13} />
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
            {confirmDialog}
        </div>
    );
};
