export const DICTIONARY_HEADER_FIELDS = [
  { name: "column_name", label: "Column name", required: true },
  { name: "logical_type", label: "Column type", note: "Maps to continuous, categorical, binary, date, period, identifier, free text, or other." },
  { name: "role", label: "Role", note: "Maps to identifier, target, mandatory, optional, unmarked, or free text." },
  { name: "valid_values", label: "Valid values" },
  { name: "missing_value_codes", label: "Special / missing values", note: "Sentinel codes that represent missing or exceptional values for each column." },
  { name: "description", label: "Description" },
  { name: "business_context", label: "Comments" },
];

export const SOURCING_STAGES = [
  ["create_target", "Create target"], ["upload", "Upload"], ["read_data", "Read data"],
  ["retain_load", "Retain load"], ["normalize_dictionary", "Normalize dictionary"],
  ["profile_data", "Profile data"], ["validate_schema", "Validate schema"],
  ["finalize", "Finalize"], ["complete", "Complete"],
];
