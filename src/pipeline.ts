import { randomUUID } from "node:crypto";

import type { AppConfig } from "./config.js";
import {
  AUTHORITY_IMPORT_PREFIX,
  CERTIFIED_WORD_PREFIX,
  CANONICAL_SUBMISSION_VERSION,
  DEVELOPMENT_RUNTIME,
  RUN_MANIFEST_VERSION,
  RunManifestSchema,
  SubmissionRejectedError,
  Uuid,
  verifyDocumentSubmission,
  type DocumentGateResult,
  type DocumentSubmissionInput,
  type IngestionEvidence,
  type ManifestPersistence,
  type ManifestRuntime,
} from "./contracts/index.js";
import { OfficialFhirValidatorClient } from "./fhir/official-validator.js";
import {
  hasValidationErrors,
  validateEmaPreflight,
  validateCanonicalPreflight,
} from "./fhir/preflight.js";
import { PROVENANCE_PROFILE, toProvenanceResource } from "./fhir/provenance.js";
import { sourceIdentifierValue, transformType2ToEma, type EmaPackage } from "./fhir/transform.js";
import { mappingReference, type EmaMapping } from "./fhir/mapping.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "./fhir/types.js";
import {
  EMA_EPI_PACKAGE_ID,
  EU_PRODUCT_IDENTITY_PROFILE,
  GLOBAL_EPI_PACKAGE_ID,
  GLOBAL_TYPE2_BUNDLE_PROFILE,
  KHS_CANONICAL,
  QRD_TEMPLATE_VERSION,
} from "./fhir/standards.js";
import { pinnedPackage, pinnedPackages } from "./fhir/standards-lock.js";
import { GcpEvidenceStore, type RunManifest, type SignedManifest } from "./gcp/evidence.js";
import {
  HealthcareApiClient,
  buildPersistTransaction,
  persistedVersion,
  type PersistedVersion,
} from "./gcp/healthcare.js";
import { GcpLineagePublisher } from "./gcp/lineage.js";
import { defaultFetcher, type AuthorityFetcher } from "./authority/fetch.js";
import { verifyAuthorityImport, type AuthorityGateResult } from "./authority/gate.js";
import { verifyCertifiedWordImport } from "./certified-word/gate.js";
import { certifiedWordSources, type CertifiedWordSources } from "./certified-word/recompute.js";
import { sha256Bytes } from "./authority/import.js";
import { approvalLinkProvenance } from "./approval/link.js";
import { recordFacts } from "./approval/review.js";
import {
  ApprovalRefusedError,
  checkStatement,
  readHead,
  type HeadSource,
  type KeySource,
  type VerifiedStatement,
} from "./approval/statement.js";
import { GcsApprovalObjects, kmsKeySource } from "./gcp/approval-store.js";
import { sha256 } from "./lib/hash.js";
import { log } from "./lib/logger.js";

const GLOBAL_TYPE2_PROFILE = GLOBAL_TYPE2_BUNDLE_PROFILE;

type BaseInput = {
  runId?: string;
  sourceResource: string;
};

export type PipelineInput =
  | (BaseInput & {
      sourceKind: "fixture" | "healthcare-api";
      source: FhirBundle;
    })
  // The three document parts stay `unknown` all the way to the gate: nothing upstream of
  // `verifyDocumentSubmission` may assume a shape it has not proved.
  | (BaseInput & { sourceKind: "document" } & DocumentSubmissionInput);

export type PipelineResult = {
  runId: string;
  // `validated` for a dry run; `persisted` once the transaction committed and the ledger recorded
  // it. The signed manifest itself says `authorised`: it was signed before the transaction.
  status: "validated" | "persisted";
  emaList: FhirResource;
  emaBundle: FhirBundle;
  mappingDecisions: ReturnType<typeof transformType2ToEma>["mappingDecisions"];
  outcomes: {
    sourcePreflight: OperationOutcome;
    emaPreflight: OperationOutcome;
    official: OperationOutcome[];
    cloud: OperationOutcome[];
  };
  evidence: SignedManifest;
  artifactUris: string[];
  // The version of the EMA document Bundle the transaction wrote, as its response named it;
  // absent for a dry run, or when the response did not say.
  persistedBundle?: PersistedVersion;
};

// What a run sends to the official HL7 validator and to the Cloud Healthcare API's $validate, each
// resource with the profiles it is validated against, in the order they are sent: the source
// against the Global ePI Bundle profile, then the EMA List, document Bundle and Composition against
// the mapping's profiles, and a document run's Provenance against base R5 with the repository's
// own package defining its extension and code systems. The source and the EMA document Bundle are
// also validated against the package's EU number invariants (EU_PRODUCT_IDENTITY_PROFILE). The
// Provenance and the package's own profiles go to the official validator only: the store has no
// definition of them (its profile import takes the four HL7 and EMA packages), so $validate is not
// asked about them. CI's "Official validation" job validates exactly this set
// (scripts/ci/emit-validation-set.ts), so a resource or a profile added here reaches that gate too.
export type OfficialValidationTarget = {
  name: "source" | "ema-list" | "ema-bundle" | "ema-composition" | "provenance";
  resource: FhirResource;
  profiles: string[];
};

export function officialValidationTargets(
  source: FhirBundle,
  transformed: EmaPackage,
  mapping: EmaMapping,
  provenance?: FhirResource,
): OfficialValidationTarget[] {
  const composition = transformed.documentBundle.entry[0]?.resource;
  if (composition === undefined) throw new Error("Transformed Composition is missing");
  return [
    {
      name: "source",
      resource: source,
      profiles: [GLOBAL_TYPE2_PROFILE, EU_PRODUCT_IDENTITY_PROFILE],
    },
    { name: "ema-list", resource: transformed.list, profiles: [mapping.profiles.list] },
    {
      name: "ema-bundle",
      resource: transformed.documentBundle,
      profiles: [mapping.profiles.bundle, EU_PRODUCT_IDENTITY_PROFILE],
    },
    { name: "ema-composition", resource: composition, profiles: mapping.profiles.composition },
    ...(provenance === undefined
      ? []
      : [{ name: "provenance" as const, resource: provenance, profiles: [PROVENANCE_PROFILE] }]),
  ];
}

function countErrors(outcomes: OperationOutcome[]): number {
  return outcomes.reduce(
    (total, outcome) =>
      total +
      outcome.issue.filter(({ severity }) => severity === "fatal" || severity === "error").length,
    0,
  );
}

// Hashes, counts, enumerations, and identifiers only; never narrative (ADR 0002).
function ingestionEvidence(
  gate: DocumentGateResult,
  provenanceResourceId: string,
  allowSyntheticSources: boolean,
  authority: AuthorityRun | undefined,
): IngestionEvidence {
  const { provenance, approval, submissionId, graphType } = gate.submission;
  const { parser, model, promptTemplate, extractionRunId } = provenance.extraction;
  const source = provenance.sourceDocument;
  // The signed manifest states the report's own status, never an assumed one; the gate refuses
  // any other, and this holds even if that check were lost.
  const { status } = gate.report;
  if (status !== "passed") throw new Error("Only a passed fidelity report reaches run evidence");

  return {
    submissionId,
    contractVersion: CANONICAL_SUBMISSION_VERSION,
    sourceKind: source.kind,
    graphType,
    allowSyntheticSources,
    sourceDocumentSha256: source.kind === "drawn" ? source.sha256 : source.document.sha256,
    extractionRunId,
    parser: `${parser.name}@${parser.version}`,
    ...(model === undefined ? {} : { modelId: model.id }),
    ...(promptTemplate === undefined ? {} : { promptTemplateVersion: promptTemplate.version }),
    fidelity: {
      status,
      normalizationVersion: gate.report.normalizationVersion,
      sectionsChecked: gate.report.summary.total,
      sectionsMatched: gate.report.summary.verified,
      reportSha256: gate.report.reportHash,
      narrativeBindingSha256: gate.report.narrativeBindingSha256,
      coverage: { ...gate.report.coverage },
    },
    approval,
    ...(authority === undefined
      ? {}
      : {
          authority: {
            importerVersion: authority.importerVersion,
            fetched: authority.fetched.map(({ url, bytes, fetchedAt }) => ({
              url,
              sha256: sha256Bytes(bytes),
              byteLength: bytes.length,
              fetchedAt,
            })),
          },
        }),
    provenanceResourceId,
  };
}

type DocumentInput = Extract<PipelineInput, { sourceKind: "document" }>;

// What the gate learnt about an authority import: the fetched files and the importer that ran.
type AuthorityRun = Omit<AuthorityGateResult, "gate">;

// The source kind a submission, before it is parsed, says it has: only that decides which gate it
// goes through; the gate itself parses and checks everything.
function claimedSourceKind(submission: unknown): unknown {
  return (submission as { provenance?: { sourceDocument?: { kind?: unknown } } } | null | undefined)
    ?.provenance?.sourceDocument?.kind;
}

async function documentGate(
  input: DocumentInput,
  mapping: EmaMapping,
  config: AppConfig,
  runId: string,
  dependencies: PipelineDependencies,
): Promise<{ gate: DocumentGateResult; authority?: AuthorityRun }> {
  const options = { allowSyntheticSources: config.ALLOW_SYNTHETIC_SOURCES };
  try {
    const claimed = claimedSourceKind(input.submission);
    if (claimed === "certified-word") {
      return {
        gate: await verifyCertifiedWordImport(
          input,
          mapping,
          { ...options, dryRun: config.DRY_RUN },
          dependencies.certifiedWord ?? certifiedWordSources(config),
        ),
      };
    }
    if (claimed === "authority-publication") {
      const fetcher =
        dependencies.authorityFetcher ?? defaultFetcher(mapping, config.ALLOW_SYNTHETIC_SOURCES);
      const { gate, ...authority } = await verifyAuthorityImport(
        input,
        mapping,
        { ...options, dryRun: config.DRY_RUN },
        fetcher,
      );
      return { gate, authority };
    }
    return { gate: verifyDocumentSubmission(input, mapping.sourceCodeSystem, options) };
  } catch (error) {
    if (error instanceof SubmissionRejectedError) {
      log("warning", "Canonical submission rejected", {
        runId,
        stage: "document-gate",
        errorCount: error.issues.length,
      });
    }
    throw error;
  }
}

// What a run may be given instead of its production default; tests use it.
export type PipelineDependencies = {
  authorityFetcher?: AuthorityFetcher;
  // Where the certified Word gate reads an upload and runs the recompute; from the configuration
  // by default (src/certified-word/recompute.ts).
  certifiedWord?: CertifiedWordSources;
  // Where a document run reads its head statement and the approval key's public keys; Cloud
  // Storage and Cloud KMS from the configuration by default.
  approvals?: { heads: HeadSource; keys: KeySource };
};

// The document's head statement, verified, and held to the record this run is about to publish
// (docs/design/approval.md, D8 and "The flow", step 6): the signature against the environment's
// approval key, the environment, and the document, submission, approved content, mapping and every
// published section's hash. A head that is another submission's is `not-head`: replaying an older
// signed submission cannot make its text current. Throws ApprovalRefusedError with a closed code.
async function verifiedApproval(
  gate: DocumentGateResult,
  transformed: EmaPackage,
  mapping: EmaMapping,
  config: AppConfig,
  dependencies: PipelineDependencies,
): Promise<VerifiedStatement> {
  const environment = config.APPROVAL_ENVIRONMENT;
  const bucket = config.APPROVAL_HEADS_BUCKET;
  const keyVersion = config.APPROVAL_SIGNING_KEY_VERSION;
  if (environment === undefined || bucket === undefined || keyVersion === undefined) {
    throw new Error("Approval verification is not configured");
  }
  const approvals = dependencies.approvals ?? {
    heads: new GcsApprovalObjects(bucket),
    keys: kmsKeySource(keyVersion),
  };
  const facts = recordFacts(gate, transformed, mapping);
  const head = await readHead(approvals.heads, approvals.keys, facts.document);
  if (typeof head === "string") throw new ApprovalRefusedError(head);
  const { document, sections, mappingVersion, approvedContentSha256, submissionId } = facts;
  const mismatch = checkStatement(head.statement, {
    environment,
    document,
    mappingVersion,
    documentBundleSha256: facts.documentBundleSha256,
  });
  if (mismatch !== undefined) throw new ApprovalRefusedError(mismatch);
  // The head names another submission or other content: this one is not the document's current
  // approved text.
  if (
    head.statement.submissionId !== submissionId ||
    head.statement.approvedContentSha256 !== approvedContentSha256
  ) {
    throw new ApprovalRefusedError("not-head");
  }
  const sectionMismatch = checkStatement(head.statement, { environment, sections });
  if (sectionMismatch !== undefined) throw new ApprovalRefusedError(sectionMismatch);
  return head;
}

// The code and images the manifest names, read through the configuration, which has already
// refused a value outside its grammar (src/config.ts); `development` where none is set, as off
// Cloud Run. Cloud Run sets K_REVISION on the container; WORKFLOW_REVISION is never set by the
// deploy (infra/run.tf).
export function manifestRuntime(config: AppConfig): ManifestRuntime {
  return {
    sourceCommit: config.GIT_COMMIT ?? DEVELOPMENT_RUNTIME,
    imageDigest: config.IMAGE_DIGEST ?? DEVELOPMENT_RUNTIME,
    validatorImageDigest: config.VALIDATOR_IMAGE_DIGEST ?? DEVELOPMENT_RUNTIME,
    workflowRevision: config.WORKFLOW_REVISION ?? config.K_REVISION ?? DEVELOPMENT_RUNTIME,
  };
}

export async function runPipeline(
  input: PipelineInput,
  mapping: EmaMapping,
  config: AppConfig,
  dependencies: PipelineDependencies = {},
): Promise<PipelineResult> {
  if (input.runId !== undefined && !Uuid.safeParse(input.runId).success) {
    throw new Error("runId must be a UUID");
  }
  // The run-source allowlist (ADR 0002 consequences), here as well as at the HTTP surface, so no
  // caller of the pipeline (scripts/dev/run-pipeline.ts, a test, a later entry point) can run a
  // source the deployment disabled: a no-synthetic deployment never signs fixture content.
  if (!config.ENABLED_RUN_SOURCES.includes(input.sourceKind)) {
    throw new Error("Run source is disabled");
  }
  // With APPROVAL_ENFORCEMENT on, every persisted run publishes under a verified head approval,
  // and only a document run has a submission an approval can name: the ungated sources (fixture,
  // healthcare-api) persist nothing, refused before any read or write.
  if (config.APPROVAL_ENFORCEMENT && !config.DRY_RUN && input.sourceKind !== "document") {
    throw new ApprovalRefusedError("ungated-source");
  }
  const runId = input.runId ?? randomUUID();
  const startedAt = new Date().toISOString();
  const stageStarted = Date.now();
  log("info", "ePI interoperability run started", {
    runId,
    stage: "pipeline",
    dryRun: config.DRY_RUN,
  });

  let gate: DocumentGateResult | undefined;
  let authority: AuthorityRun | undefined;
  let source: FhirBundle;
  if (input.sourceKind === "document") {
    ({ gate, authority } = await documentGate(input, mapping, config, runId, dependencies));
    source = gate.bundle;
  } else {
    source = input.source;
  }

  const sourcePreflight = validateCanonicalPreflight(source, gate?.submission.graphType ?? "type2");
  if (hasValidationErrors(sourcePreflight)) {
    log("warning", "Canonical preflight rejected", {
      runId,
      stage: "source-preflight",
      errorCount: countErrors([sourcePreflight]),
    });
    throw new Error("Canonical preflight failed");
  }

  // The authority-import namespace is written only by the importer: only a submission the gate
  // recomputed from the authority's files may carry it, and every other route, the ungated
  // fixture and healthcare-api sources included, is refused (docs/design/authority-import-contract.md,
  // D7).
  if (
    authority === undefined &&
    sourceIdentifierValue(source).startsWith(AUTHORITY_IMPORT_PREFIX)
  ) {
    throw new Error("Source identifier is in the reserved authority-import namespace");
  }
  // So is the certified-word namespace: only a submission its gate passed may carry it.
  const sourceKind = gate?.submission.provenance.sourceDocument.kind;
  if (
    sourceKind !== "certified-word" &&
    sourceIdentifierValue(source).startsWith(CERTIFIED_WORD_PREFIX)
  ) {
    throw new Error("Source identifier is in the reserved certified-word namespace");
  }
  // A certified Word source's titles are its label's heading lines, carried as written: the
  // crosswalk does not put the template's title in their place (ADR 0006 decision 4, D6).
  const titles = sourceKind === "certified-word" ? "as-written" : "template";

  // The standards the manifest names, read from the locks the image ships before anything is
  // written (only the document gate, whose authority fetch reads, runs before): a missing or
  // malformed lock fails the run here, not after its writes.
  const packages = pinnedPackages();
  const standards: RunManifest["standards"] = {
    fhir: "5.0.0",
    globalEpiPackage: pinnedPackage(packages, GLOBAL_EPI_PACKAGE_ID),
    emaPackage: pinnedPackage(packages, EMA_EPI_PACKAGE_ID) as "EUePI#1.0.0",
    qrdTemplate: QRD_TEMPLATE_VERSION,
    mappingVersion: mapping.mappingVersion,
    packages,
  };

  const transformed = transformType2ToEma(source, mapping, QRD_TEMPLATE_VERSION, titles);
  const emaPreflight = validateEmaPreflight(
    transformed.list,
    transformed.documentBundle,
    mapping,
    titles,
  );
  if (hasValidationErrors(emaPreflight)) {
    log("warning", "EMA preflight rejected", {
      runId,
      stage: "ema-preflight",
      errorCount: countErrors([emaPreflight]),
    });
    throw new Error("EMA structural preflight failed");
  }

  const emaBundleId = transformed.documentBundle.id;
  const emaCompositionId = transformed.documentBundle.entry[0]?.resource.id;
  if (emaBundleId === undefined || emaCompositionId === undefined) {
    throw new Error("The EMA document Bundle and Composition require ids");
  }

  // With APPROVAL_ENFORCEMENT on, a persisted document run publishes only under the document's
  // verified head statement, checked before anything is validated, signed or written, and links
  // the version it wrote to it after the commit. A dry run persists nothing and is not asked. Off
  // (the default until the design's step 6), a document run publishes as it did before.
  const approval =
    gate === undefined || config.DRY_RUN || !config.APPROVAL_ENFORCEMENT
      ? undefined
      : await verifiedApproval(gate, transformed, mapping, config, dependencies);
  const provenanceResource =
    gate === undefined
      ? undefined
      : toProvenanceResource(gate.submission, gate.report, {
          bundleId: emaBundleId,
          compositionId: emaCompositionId,
          ...(authority?.fetched[0] === undefined
            ? {}
            : { fetchedAt: authority.fetched[0].fetchedAt }),
        });

  let ingestion: IngestionEvidence | undefined;
  if (gate !== undefined) {
    const provenanceResourceId = provenanceResource?.id;
    if (provenanceResourceId === undefined) {
      throw new Error("Ingestion Provenance requires an id");
    }
    ingestion = ingestionEvidence(
      gate,
      provenanceResourceId,
      config.ALLOW_SYNTHETIC_SOURCES,
      authority,
    );
  }

  const targets = officialValidationTargets(source, transformed, mapping, provenanceResource);
  const profiles = targets.flatMap((target) => target.profiles);
  // A dry run's manifest is `validated`; a persist-mode run's is `authorised`, signed before its
  // transaction and naming it. Only the ledger row, written after the commit, says `persisted`.
  const composeManifest = (
    completedAt: string,
    validation: RunManifest["validation"],
    outcome:
      | { status: "validated"; dryRun: true }
      | { status: "authorised"; dryRun: false; persistence: ManifestPersistence },
  ): RunManifest => ({
    schemaVersion: RUN_MANIFEST_VERSION,
    runId,
    startedAt,
    completedAt,
    ...outcome,
    source: {
      kind: input.sourceKind,
      resource: input.sourceResource,
      hash: transformed.inputHash,
    },
    standards,
    validation,
    transformation: {
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
      decisions: transformed.mappingDecisions.length,
    },
    runtime: manifestRuntime(config),
    ...(ingestion === undefined ? {} : { ingestion }),
  });

  const cloudOutcomes: OperationOutcome[] = [];
  const officialOutcomes: OperationOutcome[] = [];
  const artifactUris: string[] = [];
  const validation = (): RunManifest["validation"] => ({
    preflightErrors: countErrors([sourcePreflight, emaPreflight]),
    officialValidationExecuted: !config.DRY_RUN,
    officialProfileErrors: countErrors(officialOutcomes),
    cloudValidationExecuted: !config.DRY_RUN,
    cloudProfileErrors: countErrors(cloudOutcomes),
    profiles,
  });

  const finish = (
    status: PipelineResult["status"],
    signed: SignedManifest,
    persistedBundle: PersistedVersion | undefined,
  ): PipelineResult => {
    log("info", "ePI interoperability run completed", {
      runId,
      stage: "pipeline",
      outcome: status,
      durationMs: Date.now() - stageStarted,
      mappingDecisions: transformed.mappingDecisions.length,
    });

    return {
      runId,
      status,
      emaList: transformed.list,
      emaBundle: transformed.documentBundle,
      mappingDecisions: transformed.mappingDecisions,
      outcomes: {
        sourcePreflight,
        emaPreflight,
        official: officialOutcomes,
        cloud: cloudOutcomes,
      },
      evidence: signed,
      artifactUris,
      ...(persistedBundle === undefined ? {} : { persistedBundle }),
    };
  };

  if (config.DRY_RUN) {
    const manifest = composeManifest(new Date().toISOString(), validation(), {
      status: "validated",
      dryRun: true,
    });
    RunManifestSchema.parse(manifest);
    return finish("validated", { manifest, manifestHash: sha256(manifest) }, undefined);
  }

  const validatorUrl = config.FHIR_VALIDATOR_URL;
  if (validatorUrl === undefined) throw new Error("FHIR_VALIDATOR_URL is required");
  const officialValidator = new OfficialFhirValidatorClient(validatorUrl);
  // One request per resource, with all its profiles: the validator takes several at once.
  for (const target of targets) {
    officialOutcomes.push(await officialValidator.validate(target.resource, target.profiles));
  }
  if (officialOutcomes.some(hasValidationErrors)) {
    throw new Error("Official HL7 FHIR profile validation failed");
  }

  // One request per resource and profile: $validate takes one profile at a time. The store knows
  // the HL7 and EMA profiles only.
  const healthcare = new HealthcareApiClient(config);
  for (const target of targets.filter(({ name }) => name !== "provenance")) {
    for (const profile of target.profiles.filter((url) => !url.startsWith(`${KHS_CANONICAL}/`))) {
      cloudOutcomes.push(await healthcare.validate(target.resource, profile, runId));
    }
  }
  if (cloudOutcomes.some(hasValidationErrors)) {
    throw new Error("Cloud Healthcare API profile validation failed");
  }

  // The commit order (docs/architecture.md): evidence, then the signed manifest, then the FHIR
  // transaction, then the commit object and the ledger row that record it. The manifest says
  // `authorised`, names the exact transaction by hash, and is signed before anything reaches the
  // FHIR store, so no version is ever live without a signed manifest over its transaction. A
  // failure before the transaction, or the transaction's own refusal, leaves nothing live. The
  // transaction commits when the store accepts it; a failure after that (the commit object or the
  // ledger insert, each retried within a bound) leaves a committed run with no record, answered
  // as `committed-unrecorded` and logged with the run's manifest hash. So a signed manifest with
  // no ledger row is one of the two, and the target store tells them apart: only the second left
  // a Bundle version, written by the `persist-transaction` object's transaction. Nothing is written
  // before the manifest is proved against its schema.
  // The version of the document the store holds now is the transaction's precondition, read
  // before the manifest is signed so the signed hash covers it: a transaction refused because the
  // document changed since leaves nothing written (docs/design/version-identity.md).
  const stored = await healthcare.readStoredVersion("Bundle", emaBundleId, runId);
  const transaction = buildPersistTransaction(
    transformed.list,
    transformed.documentBundle,
    runId,
    stored,
    provenanceResource === undefined ? [] : [provenanceResource],
  );
  const transactionSha256 = sha256(transaction);
  const manifest = composeManifest(new Date().toISOString(), validation(), {
    status: "authorised",
    dryRun: false,
    persistence: { targetStore: config.TARGET_FHIR_STORE_ID ?? "unknown", transactionSha256 },
  });
  RunManifestSchema.parse(manifest);

  // Every write is conditional on its object not existing, so the first one claims the runId: a
  // runId already used is refused here, before the FHIR store is touched.
  const store = new GcpEvidenceStore(config);
  artifactUris.push(await store.writeJson(runId, "source-type2", source));
  artifactUris.push(await store.writeJson(runId, "ema-list", transformed.list));
  artifactUris.push(
    await store.writeJson(runId, "ema-document-bundle", transformed.documentBundle),
  );
  artifactUris.push(
    await store.writeJson(runId, "mapping-decisions", transformed.mappingDecisions),
  );
  artifactUris.push(
    await store.writeJson(runId, "validation-outcomes", {
      sourcePreflight,
      emaPreflight,
      officialOutcomes,
      cloudOutcomes,
    }),
  );
  if (gate !== undefined && provenanceResource !== undefined) {
    artifactUris.push(await store.writeJson(runId, "canonical-submission", gate.submission));
    artifactUris.push(
      await store.writeJson(runId, "ingestion-provenance", gate.submission.provenance),
    );
    artifactUris.push(await store.writeJson(runId, "fidelity-report", gate.report));
    artifactUris.push(await store.writeJson(runId, "provenance-resource", provenanceResource));
  }
  artifactUris.push(await store.writeJson(runId, "persist-transaction", transaction));
  const evidence = await store.signManifest(manifest);
  artifactUris.push(await store.writeJson(runId, "signed-manifest", evidence));

  const transactionResponse = await healthcare.executeTransaction(transaction, runId);
  const committedAt = new Date().toISOString();
  const persistedBundle = persistedVersion(transactionResponse, "Bundle", emaBundleId);
  if (persistedBundle === undefined) {
    log("warning", "Transaction response names no version of the document Bundle", {
      runId,
      stage: "persist",
    });
  }
  try {
    // Hashes and identifiers only: what the transaction answered, bound to what was signed.
    artifactUris.push(
      await store.writeJson(
        runId,
        "commit",
        {
          runId,
          manifestHash: evidence.manifestHash,
          transactionSha256,
          transactionResponseSha256: sha256(transactionResponse),
          committedAt,
          targetBundle: {
            id: emaBundleId,
            versionId: persistedBundle?.versionId ?? null,
            lastUpdated: persistedBundle?.lastUpdated ?? null,
          },
        },
        { afterCommit: true },
      ),
    );
    await store.writeLedger(evidence, {
      committedAt,
      bundleVersionId: persistedBundle?.versionId ?? null,
    });
  } catch (error) {
    // The transaction is live. What reconciles it: this line, the signed manifest and the
    // `persist-transaction` object, and the Bundle version in the target store.
    log("error", "A committed run could not be recorded", {
      runId,
      stage: "commit",
      manifestHash: evidence.manifestHash,
      errorType: error instanceof Error ? error.name : typeof error,
    });
    throw new Error("The run committed but its record could not be written", { cause: error });
  }

  // The stored version is linked to its approval after it is written (docs/design/approval.md,
  // D5): a Provenance whose target is this version, created only if absent, carrying the signed
  // statement. Without it the query service refuses the version; so a run that cannot link says
  // so, as a committed run with no approval, rather than succeed.
  if (approval !== undefined) {
    try {
      if (persistedBundle === undefined) {
        throw new Error("Transaction response names no version of the document Bundle");
      }
      const link = approvalLinkProvenance(emaBundleId, persistedBundle.versionId, approval.signed);
      await healthcare.createResource(link, runId);
      artifactUris.push(
        await store.writeJson(
          runId,
          "approval-link",
          {
            statementSha256: approval.statementSha256,
            sequence: approval.statement.sequence,
            provenanceId: link.id,
            targetBundle: { id: emaBundleId, versionId: persistedBundle.versionId },
          },
          { afterCommit: true },
        ),
      );
    } catch (error) {
      log("error", "A committed run's version could not be linked to its approval", {
        runId,
        stage: "approval-link",
        statementSha256: approval.statementSha256,
        errorType: error instanceof Error ? error.name : typeof error,
      });
      throw new Error("The run committed but its approval could not be linked", { cause: error });
    }
  }

  // Lineage is published after the commit and is best effort: a Data Lineage failure no longer
  // turns a committed run into a 500 that invites a retry.
  try {
    const project = config.GOOGLE_CLOUD_PROJECT ?? "unknown";
    const dataset = config.HEALTHCARE_DATASET_ID ?? "unknown";
    const sourceStore = config.SOURCE_FHIR_STORE_ID ?? "unknown";
    const targetStore = config.TARGET_FHIR_STORE_ID ?? "unknown";
    const lineage = new GcpLineagePublisher(config);
    const importSource =
      gate?.submission.provenance.sourceDocument.kind === "authority-publication"
        ? gate.submission.provenance.sourceDocument
        : undefined;
    // An import's lineage names the authority's document it came from
    // (docs/design/authority-import-contract.md, D12).
    const sourceFqn =
      input.sourceKind === "healthcare-api"
        ? `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${sourceStore}.${input.sourceResource.replace("/", ".")}`
        : gate === undefined
          ? `custom:ema-flow.${input.sourceResource}`
          : importSource !== undefined
            ? `custom:authority-import.${importSource.authority === "EMA" ? "ema" : "synthetic"}.${importSource.document.id}`
            : `custom:zone-a.${gate.submission.submissionId}`;
    const lineageResources = await lineage.publish({
      runId,
      startedAt,
      completedAt: manifest.completedAt,
      mapping: mappingReference(mapping),
      sourceFqn,
      targetFhirFqn: `healthcare:${project}.${config.GCP_LOCATION}.${dataset}.${targetStore}.Bundle.${emaBundleId}`,
      targetBigQueryFqn: `bigquery:${project}.${config.FHIR_ANALYTICS_DATASET ?? "unknown"}.Bundle`,
      inputHash: transformed.inputHash,
      outputHash: transformed.outputHash,
    });
    artifactUris.push(await store.writeJson(runId, "lineage-resources", lineageResources));
  } catch (error) {
    log("warning", "Lineage was not published for a committed run", {
      runId,
      stage: "lineage",
      errorType: error instanceof Error ? error.name : typeof error,
    });
  }

  return finish("persisted", evidence, persistedBundle);
}
