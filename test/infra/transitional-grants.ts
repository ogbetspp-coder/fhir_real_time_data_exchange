// The broad grants audit B04 replaces, kept while the narrow ones are applied (phase 1), then
// deleted (phase 2). While they are bound, a deploy's smoke run cannot tell whether the narrow ones
// work (audit B08, L2): phase 2's own smoke run is the first proof, with restoring these as its
// rollback (infra/security.tf). The least-privilege tests prove the set that remains once these
// are cut, so phase 2 is a deletion these tests already cover; phase 2 also empties this list.
export const TRANSITIONAL_GRANTS: [type: string, name: string][] = [
  ["google_healthcare_dataset_iam_member", "worker_fhir_editor"],
  ["google_bigquery_dataset_iam_member", "worker_ledger_writer"],
  ["google_healthcare_dataset_iam_member", "query_fhir_reader"],
];
