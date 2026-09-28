// The broad grants audit B04 replaces, kept for one deploy while the narrow ones are applied and
// proved (phase 1), then deleted (phase 2). The least-privilege tests prove the set that remains
// once these are cut, so phase 2 is a deletion these tests already cover; phase 2 also empties
// this list.
export const TRANSITIONAL_GRANTS: [type: string, name: string][] = [
  ["google_healthcare_dataset_iam_member", "worker_fhir_editor"],
  ["google_bigquery_dataset_iam_member", "worker_ledger_writer"],
  ["google_healthcare_dataset_iam_member", "query_fhir_reader"],
];
