import { useMaterialClasses, useMaterialTypes } from "@/services/queries";

/**
 * Returns a stable lookup function that maps a material-class id (string) to the
 * default unit id (string) of that class's material type, or "" when no default
 * exists (e.g. "Прочее") or data hasn't loaded yet.
 *
 * Usage:
 *   const getDefaultUnitId = useDefaultUnitId();
 *   // inside an onChange:
 *   setForm(f => ({ ...f, material_class_id: id, unit_id: getDefaultUnitId(id) }));
 */
export function useDefaultUnitId(): (classId: string) => string {
  const classesQ = useMaterialClasses();
  const typesQ = useMaterialTypes();

  return (classId: string): string => {
    if (!classId) return "";
    const mc = (classesQ.data ?? []).find((c) => String(c.id) === classId);
    if (!mc) return "";
    const mt = (typesQ.data ?? []).find((t) => t.code === mc.material_type);
    const defId = mt?.default_unit?.id;
    return defId != null ? String(defId) : "";
  };
}
