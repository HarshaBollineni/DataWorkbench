// Route-only contract for the resumable DSC metadata-correction loop.  No
// source values or DSC candidate pins are ever carried in the browser URL.
export function metadataCorrectionTarget(searchParams) {
  if (searchParams.get("return") !== "dataset-structure") return null;
  const itemId = searchParams.get("item");
  if (!itemId) return null;
  return {
    itemId,
    table: searchParams.get("table") || "",
    column: searchParams.get("column") || "",
  };
}

export function metadataCorrectionParams(itemId, metadata = {}) {
  return {
    structure: null,
    item: itemId,
    table: metadata.table || null,
    column: metadata.column || null,
    return: "dataset-structure",
  };
}

export function structureReviewParams(itemId) {
  return { structure: itemId, item: null, table: null, column: null, return: null };
}
