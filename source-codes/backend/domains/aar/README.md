# Analysis Artifact Repository (AAR)

This domain owns immutable analytical evidence storage, artifact-type validation, exact reuse,
lineage, integrity checks, and governed Data Sourcing profile projections. Analysis contracts,
snapshot loading, target resolution, and supporting-capability registration remain in
`analysis_runtime` because they are runtime infrastructure rather than repository ownership.

Former `analysis_runtime.artifacts`, `analysis_runtime.artifact_types`, and
`analysis_runtime.data_sourcing_artifacts` imports remain exact compatibility aliases.

Dataset Structure staged selections are held in `dataset_structure_staged_reviews`
before finalization. They seed matching materialized review candidates without
becoming confirmed authority. D06, D08 and D11 consume confirmed structure through
their diagnostic-owned scope contracts; see [the technical design](../../../TSD.md).

RCA uses immutable `rca_case_context` and `rca_evidence_event` artifacts with
`rca_aar_links` as its ordered continuation projection. Chat turns retain supplied
evidence, planning, execution and answers alongside investigations. Large generated
results are separate downloadable artifacts. Explicit Start afresh deletes
RCA-owned artifacts with the derived case state while preserving source diagnostic
artifacts. See [the RCA guide](../rca/README.md) for the current contract.
