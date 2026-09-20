export {
  NORMALIZATION_VERSION,
  NormalizationError,
  countWords,
  findForbiddenCharacter,
  normalizeText,
} from "./normalize.js";
export { XhtmlError, xhtmlToText, type XhtmlErrorCode } from "./xhtml.js";
export {
  FidelityError,
  collectNarrativeSections,
  computeNarrativeBinding,
  normalizeNarrative,
  verifyNarrativeFidelity,
  verifyReportHash,
  type DiffHint,
  type FidelityInput,
  type FidelityReport,
  type NarrativeBinding,
  type NarrativeSection,
  type SectionLike,
  type SectionResult,
  type SectionStatus,
  type SourceDocumentText,
  type SourcePage,
} from "./verify.js";
