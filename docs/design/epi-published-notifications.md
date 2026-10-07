# ePI published notifications: the consumer contract

- Status: the topic definition and the store's notification setting exist; no subscriber does.
  The `dev` topic had no subscription on 2026-10-06 (`gcloud pubsub topics list-subscriptions`),
  and no message from it has been read by this repository. What a message holds rests on Google's
  documentation, cited below, not on an observed message.
- Date: 2026-10-06
- Related: `scripts/fhir/generate-artifacts.ts` (the topic), `scripts/gcp/reconcile-fhir-stores.sh`
  (the store), `infra/main.tf` (`google_pubsub_topic.fhir_changes`), `docs/architecture.md`

## The event

`https://khs.dev/fhir/SubscriptionTopic/epi-published`, in our package `dev.khs.fhir.epi#0.5.0`,
names it: an ePI document was published or superseded. Its one trigger is a `Bundle` whose `type`
is `document`, created or updated in the validated store. The official validator checks the
resource against base R5 in CI (`scripts/ci/emit-validation-set.ts` validates everything in
`fhir/generated/`): no error, and two allowlisted warnings that are the validator's own defect
(`docs/validation/changes/2026-10-06-product-graph-terminology-and-epi-topic.md`, item 4).

- **Published**: the first version of a document Bundle is written.
- **Superseded**: a later version is written to the same Bundle id. It supersedes the version
  before it. The Bundle's id and identifier are the same for every version of an ePI; each version
  has its own `Composition.identifier` (`docs/design/version-identity.md`). A new version need not
  differ in content: the deploy's smoke run republishes the smoke product on every deploy, the
  same content unless its fixture changed.

The worker writes the document Bundle in one transaction with its List, its entries and, for a
`document` run, its Provenance, every entry a `PUT` (`buildPersistTransaction`,
`src/gcp/healthcare.ts`). The Bundle's `PUT` carries the run's precondition: `ifNoneMatch: "*"`
when the store held no version, `ifMatch` on the version the run read otherwise. The worker never
deletes a Bundle.

## Why not R5 Subscriptions

The store has no topic-based delivery. Its CapabilityStatement (read from the `dev` validated
store on 2026-10-06) lists `Subscription`, `SubscriptionTopic` and `SubscriptionStatus` with
create, read, update, delete and search only, and no `$status` or `$events` operation. Google says
the store's Pub/Sub notifications are not "intended to replace FHIR Subscription" functionality
([FHIR conformance statement][conformance]). So the topic is a definition, and the delivery is
Pub/Sub.

## How the store is configured

The validated store has one entry in `notificationConfigs`, set by
`scripts/gcp/reconcile-fhir-stores.sh`:

| Field                          | Value                                                   |
| ------------------------------ | ------------------------------------------------------- |
| `pubsubTopic`                  | `projects/<project>/topics/ema-flow-<env>-fhir-changes` |
| `sendFullResource`             | `false`                                                 |
| `sendPreviousResourceOnDelete` | `false`                                                 |

The single `notificationConfig` field is deprecated and "Not supported in R5. Use
notificationConfigs instead" ([FhirStore reference][rest]). The source store holds narrative and
sends no notification: every reconcile sets its `notificationConfigs` to none, so one added by hand
does not outlive the next deploy (it had none in `dev` on 2026-10-06). Read back from `dev` on
2026-10-06 (`gcloud healthcare fhir-stores describe`): one entry,
the topic above, both flags at their default, `false`. `test/fhir-artifacts.test.ts` holds the
reconciler to names only: a notification never carries narrative, which is also why the topic is
not on a customer-managed key (`docs/design/cmek-rollout.md`).

Terraform owns the topic (`google_pubsub_topic.fhir_changes`, 7 days' message retention), the
Healthcare service agent's `roles/pubsub.publisher` on it, and the output `fhir_changes_topic`.

## What a message holds

From [FHIR Pub/Sub notifications][pubsub], for a store that sends names only:

| Field                        | Value                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `attributes.action`          | `CreateResource`, `UpdateResource`, `PatchResource` or `DeleteResource`              |
| `attributes.resourceType`    | The resource type written, for this topic `Bundle`                                   |
| `attributes.payloadType`     | `NameOnly`                                                                           |
| `attributes.storeName`       | `projects/<project>/locations/<region>/datasets/<dataset>/fhirStores/<store>`        |
| `attributes.versionId`       | The id of the resource's most recent version                                         |
| `attributes.lastUpdatedTime` | When the resource was last modified, RFC 1123                                        |
| `data`                       | Base64 of the resource name: `<storeName>/fhir/Bundle/<id>`                          |
| `messageId`, `publishTime`   | Pub/Sub's own: an id unique within the topic, and when Pub/Sub published the message |

No content: no narrative and no identifier. Notifications are sent for resources written
through `fhir.executeBundle`, which the worker uses, and not for resources imported from Cloud
Storage ([FHIR import options][import]).

## What a consumer must do

1. **Filter.** The topic carries a message for every resource the store writes: the List, the
   Composition, the product graph and the Provenance as well as the Bundle. Subscribe with the
   filter `attributes.resourceType = "Bundle"` ([filter syntax][filter]).
2. **Read the version.** Decode `data` to the Bundle's name and read that version,
   `<name>/_history/<versionId>`, from the validated store. Check `type` is `document`, the
   topic's criterion.
3. **Expect repeats and any order.** Pub/Sub delivers at least once, with no ordering guarantee
   unless messages carry an ordering key ([subscriptions][delivery]); Google does not say the store
   sets one. Process each `(name, versionId)` once. To know whether a version is current, read the
   Bundle without a version and compare `meta.versionId`; a later version has its own message.
4. **Tell published from superseded by the history, not by `action` or by count.** The worker
   writes with `PUT`, and the documentation does not say which action a `PUT` that creates a
   resource reports. Nor does the number of versions help: a late, repeated or replayed message for
   the first version can arrive after later versions exist. The notified version is the first publication
   if and only if it is the oldest version in the Bundle's `_history`; any other version is a
   supersession, since an older one exists. The history comes newest first and in pages, 100 to a
   page by default ([history][history]), so the oldest is the last entry of the last page.
5. **A `DeleteResource` is outside the topic.** The pipeline never deletes. One would mean someone
   deleted the document from the store by hand.
6. **A notification is not an approval.** It says a version was written to the validated store.
   While `APPROVAL_ENFORCEMENT` is off, the worker verifies no signed approval before it writes,
   so no stored version is under a verified signed approval. It is off by default
   (`var.approval_enforcement`), and off in `dev`, the one deployed environment (the worker's
   environment, read 2026-10-06). The `fixture` and `healthcare-api` run sources then write
   without the approval gate where the deployment allows them (`docs/architecture.md`, "Zone A
   structuring boundary"). With it on, only a `document` run persists, under its verified approval
   (`src/pipeline.ts`). A document run's Provenance is written in the same transaction; the query
   service's `get_provenance` answers it.

A consumer needs its own subscription, with `roles/pubsub.subscriber` on it, and
`roles/healthcare.fhirResourceReader` on the validated store. Every read it makes is a Data Access audit log entry: `DATA_READ` and `DATA_WRITE` are on for
`healthcare.googleapis.com` (`google_project_iam_audit_config.regulated_data_access`,
`infra/security.tf`; read back from `dev` with `gcloud projects get-iam-policy` on 2026-10-06), and
the sink keeps those entries in the retained audit bucket.

A subscription created later can replay what the topic still holds, up to 7 days, by seeking to a
time ([seek][seek]). That has not been tried here.

## Not built

No subscription, no subscriber and no IAM for one. Each is added, in Terraform, with the first
consumer.

[conformance]: https://cloud.google.com/healthcare-api/docs/fhir "FHIR conformance statement, last updated 2026-10-05"
[rest]: https://cloud.google.com/healthcare-api/docs/reference/rest/v1/projects.locations.datasets.fhirStores "REST resource fhirStores, last updated 2026-04-17"
[pubsub]: https://cloud.google.com/healthcare-api/docs/fhir-pubsub "FHIR Pub/Sub notifications, last updated 2026-10-05"
[import]: https://cloud.google.com/healthcare-api/docs/concepts/fhir-import "FHIR import options, last updated 2026-10-05"
[filter]: https://cloud.google.com/pubsub/docs/subscription-message-filter "Filter messages from a subscription"
[delivery]: https://cloud.google.com/pubsub/docs/subscription-overview "Subscriptions overview"
[seek]: https://cloud.google.com/pubsub/docs/replay-overview "Replay and purge messages with seek"
[history]: https://cloud.google.com/healthcare-api/docs/reference/rest/v1/projects.locations.datasets.fhirStores.fhir/history "Method: fhir.history, last updated 2025-07-23"
