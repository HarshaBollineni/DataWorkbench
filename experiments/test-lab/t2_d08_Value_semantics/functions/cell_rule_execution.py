"""Experimental cell-level executor for the t2_d08 value-semantics KB.

The executor deliberately maps reviewed KB entry identifiers to safe Python
primitives.  It never evaluates free-form YAML expressions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd


TAG_PRECEDENCE = {"NOT_APPLICABLE": 0, "CENSORED": 1, "STALE_FROZEN": 2}
ASSESSMENT_COLUMNS = [
    "row_reference", "input_variable", "matched_role", "rule", "entry",
    "assessment_grain", "grain_key", "status", "proposed_tag", "reason_code",
    "input_value", "is_populated",
]
CLAIM_COLUMNS = [
    "row_reference", "input_variable", "matched_role", "rule", "entry", "tag",
    "reason_code", "input_value", "is_populated",
]
GROUP_COLUMNS = [
    "entry", "input_variable", "assessment_grain", "grain_key", "row_count",
    "minimum_row_count", "assessed", "tagged", "outcome",
]


@dataclass
class CellExecutionResult:
    execution_plan: pd.DataFrame
    assessment_ledger: pd.DataFrame
    raw_cell_claims: pd.DataFrame
    resolved_cell_tags: pd.DataFrame
    group_assessments: pd.DataFrame


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _is_populated(value: Any) -> bool:
    return not pd.isna(value) and str(value).strip() != ""


def _norm(value: Any) -> str:
    if not _is_populated(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value)).upper()
    return str(value).strip().upper()


def _quarter_ordinal(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{4})\s*[- ]?Q([1-4])", str(value).strip(), re.I)
    return int(match.group(1)) * 4 + int(match.group(2)) - 1 if match else None


def _month_ordinal(value: Any) -> int | None:
    match = re.match(r"(\d{4})-(\d{2})", str(value).strip())
    return int(match.group(1)) * 12 + int(match.group(2)) - 1 if match else None


def _period_ordinal(value: Any) -> int | None:
    return _quarter_ordinal(value) if "Q" in str(value).upper() else _month_ordinal(value)


def _horizon_steps(value: Any, period_sample: Any) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*(quarters?|months?)\s*", str(value), re.I)
    if not match:
        return None
    count, unit = int(match.group(1)), match.group(2).lower()
    quarterly = "Q" in str(period_sample).upper()
    if quarterly and unit.startswith("quarter"):
        return count
    if not quarterly and unit.startswith("month"):
        return count
    return None


def _role_index(bindings: Mapping[str, list[str]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for column, roles in bindings.items():
        for role in roles:
            index.setdefault(role, []).append(column)
    return index


def _single(index: Mapping[str, list[str]], role: str) -> str | None:
    columns = index.get(role, [])
    return columns[0] if len(columns) == 1 else None


def _sentinels(dictionary: pd.DataFrame, declarations: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(declarations.get("field_specific_sentinel_definitions") or {})
    if "column_name" in dictionary and "sentinel_value" in dictionary:
        for row in dictionary.to_dict(orient="records"):
            if _is_populated(row.get("sentinel_value")):
                result.setdefault(str(row["column_name"]), row["sentinel_value"])
    return result


def _sentinel_norms(value: Any) -> set[str]:
    """Normalize one or more declared sentinel values without evaluating text."""
    if isinstance(value, (list, tuple, set)):
        values = value
    elif isinstance(value, str) and "|" in value:
        values = value.split("|")
    else:
        values = [value]
    return {_norm(item) for item in values if _is_populated(item)}


def _validate_inputs(data: pd.DataFrame, dictionary: pd.DataFrame,
                     bindings: Mapping[str, list[str]], row_reference_column: str) -> None:
    if row_reference_column not in data:
        raise ValueError(f"Row-reference column is absent from data: {row_reference_column}")
    if data[row_reference_column].isna().any() or data[row_reference_column].astype(str).duplicated().any():
        raise ValueError("Row references must be populated and unique")
    dictionary_columns = set(dictionary["column_name"].astype(str))
    missing_dictionary = sorted(set(data.columns) - dictionary_columns)
    if missing_dictionary:
        raise ValueError(f"Data columns are absent from dictionary: {missing_dictionary}")
    missing_data = sorted(set(bindings) - set(data.columns))
    if missing_data:
        raise ValueError(f"Bound columns are absent from data: {missing_data}")


class _Executor:
    def __init__(self, data: pd.DataFrame, dictionary: pd.DataFrame,
                 bindings: Mapping[str, list[str]], declarations: Mapping[str, Any],
                 kb: Mapping[str, Any], row_reference_column: str):
        self.data = data.reset_index(drop=True).copy()
        self.dictionary = dictionary.copy()
        self.bindings = {str(key): list(value) for key, value in bindings.items()}
        self.declarations = dict(declarations)
        self.kb = kb
        self.row_ref = row_reference_column
        self.roles = _role_index(self.bindings)
        self.sentinels = _sentinels(dictionary, declarations)
        self.assessments: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.plan: list[dict[str, Any]] = []

    def add_assessments(self, rule: Mapping[str, Any], entry: Mapping[str, Any], target: str,
                        tag_mask: pd.Series, reason_code: str | pd.Series, *, grain: str = "row_and_cell",
                        grain_keys: pd.Series | None = None,
                        unclassified_mask: pd.Series | None = None) -> None:
        tag_mask = tag_mask.reindex(self.data.index, fill_value=False).astype(bool)
        unclassified = (unclassified_mask.reindex(self.data.index, fill_value=False).astype(bool)
                        if unclassified_mask is not None else pd.Series(False, index=self.data.index))
        for idx, row in self.data.iterrows():
            tagged = bool(tag_mask.loc[idx])
            unknown = bool(unclassified.loc[idx]) and not tagged
            tagged_reason = reason_code.loc[idx] if isinstance(reason_code, pd.Series) else reason_code
            self.assessments.append({
                "row_reference": row[self.row_ref], "input_variable": target,
                "matched_role": entry["target_role"], "rule": rule["rule"], "entry": entry["entry"],
                "assessment_grain": grain,
                "grain_key": "" if grain_keys is None else grain_keys.loc[idx],
                "status": "APPLIED_TAG" if tagged else ("UNCLASSIFIED" if unknown else "NO_TAG"),
                "proposed_tag": rule["tag_assigned"] if tagged else "",
                "reason_code": tagged_reason if tagged else ("indeterminate_evidence" if unknown else "condition_not_met"),
                "input_value": row[target], "is_populated": _is_populated(row[target]),
            })

    def add_unscoped(self, rule: Mapping[str, Any], entry: Mapping[str, Any], target: str,
                     missing_roles: list[str], missing_declarations: list[str]) -> None:
        reason = ";".join([
            *(f"missing_role:{value}" for value in missing_roles),
            *(f"missing_declaration:{value}" for value in missing_declarations),
        ])
        self.assessments.append({
            "row_reference": "", "input_variable": target, "matched_role": entry["target_role"],
            "rule": rule["rule"], "entry": entry["entry"],
            "assessment_grain": entry.get("assessment_grain", "row_and_cell"), "grain_key": "",
            "status": "UNSCOPED", "proposed_tag": "", "reason_code": reason,
            "input_value": None, "is_populated": False,
        })

    def required(self, rule: Mapping[str, Any], entry: Mapping[str, Any], target: str,
                 roles: list[str], declarations: list[str]) -> bool:
        missing_roles = [role for role in roles if not self.roles.get(role)]
        missing_declarations = [name for name in declarations if name not in self.declarations]
        ready = not missing_roles and not missing_declarations
        self.plan.append({
            "input_variable": target, "matched_role": entry["target_role"], "rule": rule["rule"],
            "entry": entry["entry"], "assessment_grain": entry.get("assessment_grain", "row_and_cell"),
            "routing_status": "READY_FOR_EVALUATION" if ready else "UNSCOPED",
            "missing_roles": ";".join(missing_roles),
            "missing_declarations": ";".join(missing_declarations),
        })
        if not ready:
            self.add_unscoped(rule, entry, target, missing_roles, missing_declarations)
        return ready

    def unfinished_outcome(self, rule, entry, target):
        if not self.required(rule, entry, target, ["outcome_state"],
                             ["finished_outcome_states", "outcome_state_domain", "interim_outcome_value_policy"]):
            return
        state = self.data[_single(self.roles, "outcome_state")]
        domain = {_norm(value) for value in self.declarations["outcome_state_domain"]}
        finished = {_norm(value) for value in self.declarations["finished_outcome_states"]}
        normalized = state.map(_norm)
        self.add_assessments(rule, entry, target, normalized.isin(domain - finished), "unfinished_outcome",
                             unclassified_mask=~normalized.isin(domain))

    def forward_window(self, rule, entry, target):
        if not self.required(rule, entry, target, ["period"],
                             ["panel_end_period", "forward_horizon_by_label"]):
            return
        period_col = _single(self.roles, "period")
        periods = self.data[period_col].map(_period_ordinal)
        panel_end = _period_ordinal(self.declarations["panel_end_period"])
        horizon_raw = (self.declarations["forward_horizon_by_label"] or {}).get(target)
        sample = next((value for value in self.data[period_col] if _is_populated(value)), "")
        horizon = _horizon_steps(horizon_raw, sample)
        if panel_end is None or horizon is None:
            self.add_assessments(rule, entry, target, pd.Series(False, index=self.data.index),
                                 "incomplete_forward_window",
                                 unclassified_mask=pd.Series(True, index=self.data.index))
            return
        boundary = pd.Series(panel_end, index=self.data.index)
        exit_col = _single(self.roles, "exit_indicators")
        entity_col = _single(self.roles, "entity_id")
        if exit_col and entity_col:
            exits = self.data[exit_col].map(_norm).isin({_norm(v) for v in self.declarations.get("declared_exit_events", [])})
            exit_period = periods.where(exits).groupby(self.data[entity_col]).transform("min")
            boundary = pd.concat([boundary, exit_period], axis=1).min(axis=1, skipna=True)
        unknown = periods.isna()
        self.add_assessments(rule, entry, target, (periods + horizon > boundary) & ~unknown,
                             "incomplete_forward_window", unclassified_mask=unknown)

    def ead_unobserved(self, rule, entry, target):
        if not self.required(rule, entry, target, ["default_event", "period"],
                             ["ead_target_observation_policy", "ead_measurement_point_by_target", "default_event_domain"]):
            return
        event_col, period_col = _single(self.roles, "default_event"), _single(self.roles, "period")
        event_values = {_norm(key) for key in self.declarations["default_event_domain"]}
        event = self.data[event_col].map(_norm).isin(event_values)
        periods = self.data[period_col].map(_period_ordinal)
        entity_col = _single(self.roles, "entity_id")
        if entity_col:
            order = pd.DataFrame({"entity": self.data[entity_col], "period": periods, "event": event}, index=self.data.index)
            observed = pd.Series(False, index=self.data.index)
            for _, group in order.sort_values(["entity", "period"]).groupby("entity"):
                observed.loc[group.index] = group["event"].cummax().values
        else:
            observed = event
        self.add_assessments(rule, entry, target, ~observed & periods.notna(),
                             "ead_measurement_not_observed", unclassified_mask=periods.isna())

    def _segment_variance(self, rule, entry, target, base_mask: pd.Series) -> pd.Series:
        segment_col, period_col = _single(self.roles, "segment"), _single(self.roles, "period")
        window = int(self.declarations["variance_window"])
        minimum = int(self.declarations["minimum_row_count"])
        tagged = pd.Series(False, index=self.data.index)
        periods = self.data[period_col].map(_period_ordinal)
        for segment, segment_idx in self.data.groupby(segment_col).groups.items():
            available = sorted(periods.loc[segment_idx].dropna().unique())
            for end in range(window - 1, len(available)):
                selected_periods = available[end - window + 1:end + 1]
                idx = self.data.index[(self.data[segment_col] == segment) & periods.isin(selected_periods) & base_mask]
                values = self.data.loc[idx, target]
                sentinel = self.sentinels.get(target)
                valid = values[values.map(_is_populated)]
                if sentinel is not None:
                    valid = valid[~valid.map(_norm).isin(_sentinel_norms(sentinel))]
                assessed = len(valid) >= minimum
                frozen = assessed and valid.nunique(dropna=True) == 1
                key = f"segment={segment};periods={selected_periods[0]}..{selected_periods[-1]}"
                self.groups.append({"entry": entry["entry"], "input_variable": target,
                                    "assessment_grain": entry["assessment_grain"], "grain_key": key,
                                    "row_count": len(valid), "minimum_row_count": minimum,
                                    "assessed": assessed, "tagged": frozen,
                                    "outcome": "TAGGED" if frozen else ("NO_TAG" if assessed else "BELOW_MINIMUM")})
                if frozen:
                    tagged.loc[valid.index] = True
        return tagged

    def _entity_variance(self, rule, entry, target, base_mask: pd.Series) -> pd.Series:
        entity_col, period_col = _single(self.roles, "entity_id"), _single(self.roles, "period")
        activity_col = _single(self.roles, "exposure_change_indicators")
        window = int(self.declarations["variance_window"])
        minimum = int(self.declarations["minimum_row_count"])
        tagged = pd.Series(False, index=self.data.index)
        periods = self.data[period_col].map(_period_ordinal)
        for entity, entity_idx in self.data.groupby(entity_col).groups.items():
            ordered = list(periods.loc[entity_idx].sort_values().index)
            for end in range(window - 1, len(ordered)):
                idx = pd.Index(ordered[end - window + 1:end + 1])
                idx = idx[base_mask.loc[idx].values]
                values = self.data.loc[idx, target]
                valid = values[values.map(_is_populated)]
                activity = pd.to_numeric(self.data.loc[idx, activity_col], errors="coerce").fillna(0).abs().sum()
                assessed = len(valid) >= minimum and activity > 0
                frozen = assessed and valid.nunique(dropna=True) == 1
                key = f"entity={entity};periods={periods.loc[idx].min()}..{periods.loc[idx].max()}"
                self.groups.append({"entry": entry["entry"], "input_variable": target,
                                    "assessment_grain": entry["assessment_grain"], "grain_key": key,
                                    "row_count": len(valid), "minimum_row_count": minimum,
                                    "assessed": assessed, "tagged": frozen,
                                    "outcome": "TAGGED" if frozen else ("NO_TAG" if assessed else "BELOW_MINIMUM")})
                if frozen:
                    tagged.loc[valid.index] = True
        return tagged

    def stale(self, rule, entry, target):
        grain = entry["assessment_grain"]
        roles = list(entry.get("indicator_roles", []))
        declarations = ["variance_window", "minimum_row_count", *entry.get("required_declarations", [])]
        if grain == "segment_and_declared_review_cycle" and "lower_frequency_fields_and_review_cycles" not in self.declarations:
            if target in self.sentinels:
                roles, declarations = [], []
            else:
                self.required(rule, entry, target, roles, declarations)
                return
        if not self.required(rule, entry, target, roles, declarations):
            return
        sentinel_mask = pd.Series(False, index=self.data.index)
        if target in self.sentinels:
            sentinel_mask = self.data[target].map(_norm).isin(_sentinel_norms(self.sentinels[target]))
        base = pd.Series(True, index=self.data.index)
        if entry["entry"].endswith("tracking_rate"):
            rate_col = _single(self.roles, "rate_basis")
            base = self.data[rate_col].map(_norm).str.contains("FLOAT|VARIABLE|TRACK", regex=True)
        if entry["entry"].endswith(("undrawn_commitment", "available_commitment")):
            facility_col = _single(self.roles, "facility_type")
            mapping = {_norm(k): _norm(v) for k, v in self.declarations["commitment_applicability_by_facility_type"].items()}
            base = self.data[facility_col].map(_norm).map(mapping).eq("APPLICABLE")
        if grain.startswith("segment_"):
            variance_mask = self._segment_variance(rule, entry, target, base)
        else:
            variance_mask = self._entity_variance(rule, entry, target, base)
        reasons = pd.Series("zero_variance_at_declared_grain", index=self.data.index)
        reasons.loc[sentinel_mask] = "declared_field_sentinel"
        self.add_assessments(rule, entry, target, sentinel_mask | variance_mask, reasons, grain=grain)

    def not_applicable(self, rule, entry, target):
        entry_id = entry["entry"]
        roles = list(entry.get("indicator_roles", []))
        declarations = list(entry.get("required_declarations", [])) + list(entry.get("indicator_declarations", []))
        if not self.required(rule, entry, target, roles, declarations):
            return
        unknown = pd.Series(False, index=self.data.index)
        if entry_id.endswith("lgd_no_realisation"):
            values = self.data[_single(self.roles, "return_to_performing")].map(_norm)
            domain = {_norm(key) for key in self.declarations.get("return_to_performing_domain", {})}
            mask = values.isin(domain)
            unknown = values.eq("")
            reason = "cured_without_realisation"
        elif entry_id.endswith("lgd_regime_stamp"):
            values = self.data[_single(self.roles, "non_arrears_trigger")].map(_norm)
            non_arrears = {_norm(key) for key in self.declarations.get("non_arrears_trigger_domain", {})}
            mask = values.ne("") & ~values.isin(non_arrears)
            unknown = values.eq("")
            reason = "arrears_based_event"
        elif entry_id.endswith("lgd_arrears"):
            values = self.data[_single(self.roles, "non_arrears_trigger")].map(_norm)
            non_arrears = {_norm(key) for key in self.declarations.get("non_arrears_trigger_domain", {})}
            mask = values.isin(non_arrears)
            unknown = values.eq("")
            reason = "non_arrears_trigger"
        elif entry_id.endswith("pd_construction_constant"):
            mask = pd.Series(bool(self.declarations.get("panel_scope_exclusions")), index=self.data.index)
            reason = "panel_scope_exclusion"
        elif entry_id.endswith("pd_fixed_rate_spread"):
            values = self.data[_single(self.roles, "rate_basis")].map(_norm)
            mask = values.str.contains("FIXED")
            unknown = values.eq("")
            reason = "fixed_rate_no_benchmark"
        elif entry_id.endswith("pd_before_maturity"):
            maturity = pd.to_datetime(self.data[_single(self.roles, "maturity_date")], errors="coerce")
            period = self.data[_single(self.roles, "period")].map(_quarter_ordinal)
            period_end_year = period.floordiv(4)
            period_end_quarter = period.mod(4) + 1
            period_end = pd.to_datetime(period_end_year.astype("Int64").astype(str) + "-" +
                                        (period_end_quarter * 3).astype("Int64").astype(str) + "-01", errors="coerce") + pd.offsets.MonthEnd(0)
            mask = period_end.lt(maturity)
            unknown = period_end.isna() | maturity.isna()
            reason = "before_contractual_maturity"
        elif entry_id.endswith("pd_non_amortising"):
            values = self.data[_single(self.roles, "amortisation_basis")].map(_norm)
            mask = values.str.contains("INTEREST.?ONLY|NON.?AMORT", regex=True)
            unknown = values.eq("")
            reason = "non_amortising_contract"
        else:
            facility = self.data[_single(self.roles, "facility_type")].map(_norm)
            status = self.data[_single(self.roles, "commitment_status")].map(_norm)
            mapping = {_norm(k): _norm(v) for k, v in self.declarations["commitment_applicability_by_facility_type"].items()}
            applicability = facility.map(mapping)
            mask = applicability.eq("NOT APPLICABLE") | status.str.contains("NO_COMMITMENT|CLOSED", regex=True)
            unknown = facility.eq("") | status.eq("")
            reason = "no_commitment_concept"
        self.add_assessments(rule, entry, target, mask & ~unknown, reason, unclassified_mask=unknown)

    def run(self) -> CellExecutionResult:
        handlers = {
            "t2_d08_valsim_censored_lgd_unfinished_outcome": self.unfinished_outcome,
            "t2_d08_valsim_censored_pd_incomplete_forward_window": self.forward_window,
            "t2_d08_valsim_censored_ead_unobserved_exposure_at_default": self.ead_unobserved,
            "t2_d08_valsim_censored_ead_unobserved_future_drawdown": self.ead_unobserved,
            "t2_d08_valsim_censored_ead_unobserved_conversion_factor": self.ead_unobserved,
        }
        for rule in self.kb["rules"]:
            for entry in rule["entries"]:
                targets = self.roles.get(entry["target_role"], [])
                if not targets:
                    continue
                for target in targets:
                    entry_id = entry["entry"]
                    if entry_id in handlers:
                        handlers[entry_id](rule, entry, target)
                    elif rule["tag_assigned"] == "STALE_FROZEN":
                        self.stale(rule, entry, target)
                    elif rule["tag_assigned"] == "NOT_APPLICABLE":
                        self.not_applicable(rule, entry, target)
                    else:
                        raise NotImplementedError(f"No execution primitive for {entry_id}")

        ledger = pd.DataFrame(self.assessments, columns=ASSESSMENT_COLUMNS)
        claims = ledger.loc[ledger["status"].eq("APPLIED_TAG")].rename(columns={"proposed_tag": "tag"})
        claims = claims[CLAIM_COLUMNS].reset_index(drop=True) if not claims.empty else _empty(CLAIM_COLUMNS)
        resolved = resolve_precedence(claims)
        return CellExecutionResult(
            execution_plan=pd.DataFrame(self.plan), assessment_ledger=ledger,
            raw_cell_claims=claims, resolved_cell_tags=resolved,
            group_assessments=pd.DataFrame(self.groups, columns=GROUP_COLUMNS),
        )


def resolve_precedence(raw_claims: pd.DataFrame) -> pd.DataFrame:
    columns = [*CLAIM_COLUMNS, "suppressed_tags", "claim_count"]
    if raw_claims.empty:
        return _empty(columns)
    work = raw_claims.copy()
    work["_precedence"] = work["tag"].map(TAG_PRECEDENCE)
    work = work.sort_values(["row_reference", "input_variable", "_precedence", "entry"])
    rows = []
    for _, group in work.groupby(["row_reference", "input_variable"], sort=False):
        winner = group.iloc[0].drop(labels="_precedence").to_dict()
        winner["suppressed_tags"] = ";".join(group.iloc[1:]["tag"].tolist())
        winner["claim_count"] = len(group)
        rows.append(winner)
    return pd.DataFrame(rows, columns=columns)


def execute_value_semantics(data: pd.DataFrame, dictionary: pd.DataFrame,
                            bindings: Mapping[str, list[str]], declarations: Mapping[str, Any],
                            kb: Mapping[str, Any], *, row_reference_column: str = "ROW_ID") -> CellExecutionResult:
    """Execute every supported KB entry against reviewed physical-role bindings."""
    _validate_inputs(data, dictionary, bindings, row_reference_column)
    return _Executor(data, dictionary, bindings, declarations, kb, row_reference_column).run()
