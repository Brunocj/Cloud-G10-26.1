/**
 * useConfirm — sustituye a window.confirm por el ConfirmModal de la app.
 *
 * window.confirm es SÍNCRONO: se podía escribir `if (!window.confirm(...)) return;`
 * en mitad de un handler. Un modal de React no puede bloquear la ejecución, así
 * que hay que partir la acción en dos: pedir la confirmación y, cuando el
 * usuario acepta, ejecutar el trabajo desde el callback.
 *
 * Uso:
 *   const [askConfirm, confirmDialog] = useConfirm();
 *
 *   const borrar = (x) => askConfirm({
 *       title: "Eliminar cosa",
 *       msg:   `¿Seguro que quieres eliminar "${x.name}"?`,
 *       onOk:  async () => { ...el trabajo que antes iba tras el if... },
 *   });
 *
 *   return (<>  ...  {confirmDialog}</>);
 */
import { useState } from "react";
import { ConfirmModal } from "../components/modals/ConfirmModal";

export const useConfirm = () => {
    const [pending, setPending] = useState(null);

    const ask = (config) => setPending(config);

    const dialog = pending ? (
        <ConfirmModal
            title={pending.title}
            msg={pending.msg}
            confirmLabel={pending.confirmLabel}
            onOk={() => {
                const run = pending.onOk;
                setPending(null);   // cerrar antes de ejecutar: si onOk lanza,
                run?.();            // el modal no se queda colgado en pantalla
            }}
            onCancel={() => setPending(null)}
        />
    ) : null;

    return [ask, dialog];
};
