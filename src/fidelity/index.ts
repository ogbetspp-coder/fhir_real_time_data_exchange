export {
  NORMALIZATION_VERSION,
  NormalizationError,
  countWords,
  findForbiddenCharacter,
  isGap,
  isWordCharacter,
  normalizeText,
} from "./normalize.js";
export {
  XhtmlError,
  hasDrawnText,
  isReservedCodePoint,
  xhtmlToText,
  type XhtmlErrorCode,
} from "./xhtml.js";
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
