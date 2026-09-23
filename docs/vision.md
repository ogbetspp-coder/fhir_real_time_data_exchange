# North star

_Set 2026-09-22. This is what the work is for. Every roadmap item is judged against it: does it
bring the record, the engine or the proof closer, or is it a surface?_

## The sentence

**Every statement a regulated pharmaceutical company makes about a product — in its label, its
packaging, its quality documentation, and in any answer an assistant gives about them — can be
traced to one canonical, versioned, approved record, quoted exactly, and proved.**

The failure this replaces was demonstrated on the day this was written. A general assistant, asked
what section 4.4 of a label said, answered twice from its own memory: fluently, correctly
formatted, citing a real document, a plausible version and correct-looking hashes, all invented.
No reader could have told. Once the assistant was made to answer through the record, it could only
quote the approved text or say that it could not. The model stayed nondeterministic. The system
stopped being. **Taming nondeterministic behaviour in regulated work** is the thesis. It will be
true for one document once the first live turn shows the answer post-checked — find, fetch and
verify under one turn id; until then the answers are quoted from the record, but not yet
re-checked after they are composed.

## Three layers, one model

```
            ┌─────────────────────────────────────────────────────────┐
            │  PROOF     verified answers · signed approval · audit    │   sold first
            ├─────────────────────────────────────────────────────────┤
            │  RECORD    canonical FHIR: one MedicinalProductDefinition│   the platform
            │            → label (ePI) · packaging · quality (PQI)     │
            │            crosswalks by StructureDefinition + ConceptMap│
            ├─────────────────────────────────────────────────────────┤
            │  ENGINE    documents → canonical record, deterministic   │   the moat, later
            │            where possible, proposed-then-proved elsewhere│
            └─────────────────────────────────────────────────────────┘
```

**The record** is FHIR, in a canonical shape the pipeline owns, mapped to each authority's
profiles by mapping files and proved by the official validator. One medicinal product definition
carries identity and version across three document worlds that today never agree: the label, the
packaging, and the chemistry, manufacturing and controls documentation. Regulatory operations sits
on the version chain; labelling on the narrative; quality on the structured data. The same
product, the same version, the same proof.

**The proof** is what makes the record usable by people and by assistants. A fidelity check that
decides, mechanically, whether two texts are the same; a content hash on every narrative; evidence
signed in hardware; an approval that binds a named person to a hash; an audit record for every
question that names the person, the version and the turn. The model proposes and never asserts.
Refusal is a feature: truncated, unentitled and unverified are answers.

**The engine** converts what companies actually have — documents — into the record. Its
architecture was settled by the extractor spike: characters from the document's own text layer,
structure from a layout model, the model's text never in the record. It is built narrow first, for
one document set, and it is the last layer built, because the proof layer is what makes an
imperfect engine shippable: every span it produces is checkable against its source.

## Principles that do not move

1. **Deterministic core.** Transformation, validation and hashing are functions, not prompts.
2. **The model proposes, mathematics proves, a person decides.** Anything a model produces is a
   proposal until a check confirms it or a person approves it, and it says which.
3. **No model text in the record.** Ever. The record holds what an author wrote and what a person
   approved.
4. **Refusal over plausibility.** An answer that cannot be verified is not shown as an answer.
5. **A record that outlives the model.** Person, version, hash, time. Any model, any vendor.
6. **Google's chassis, a portable core.** Managed components for everything that is not the
   product. The contracts and the fidelity check import no cloud. The pipeline and the query
   service do: they reach Google through adapters (`src/gcp/`; the query service's store reader
   and token verification).
7. **No custom interface where a Google surface serves.** The client's own assistant, Looker over
   the ledger, the approver's decision taken in Google Chat.
8. **Foundations before features, evidence before claims.** Delivered means it ran, and there is a
   record to point at.

## The order, and why

1. **Verified answers over approved labels, inside the client's assistant.** Built. The buyer
   already has the assistant; the proof layer is the only part they cannot get elsewhere.
2. **Approval.** A named person, a signature over the hash, answers only from signed versions.
   Turns a demonstration into a governed process.
3. **A real label, through the cheapest door.** A client's structured export, or an authority's
   published ePI, before any parser exists. The first genuine test of value.
4. **Role agents over one source.** Medical information, promotional review, pharmacovigilance
   look-ups. Configuration, not code, each with an adversarial evaluation set.
5. **The engine, for one document set.** English, born-digital summaries of product
   characteristics, sectioned by the quality-review template, narrative verbatim. Then typed
   fields, proposed and proved. Then quality documentation, where the tables live and the larger
   prize is.
6. **The shared core model across label, packaging and quality.** The platform proper.

Each step is sellable on its own, and each makes the next one cheaper to prove.

## The commercial thesis, with its caveats

**The need is real and dated.** Every holder of a European marketing authorisation will have to
produce structured product information; every medical-information function already answers from
the approved label by hand; every quality function is being asked for structured submissions.
General assistants are arriving in all three, with exactly the failure demonstrated above.

**What can be owned, and what cannot.** The standards are public and nobody owns them; that is
also what makes them worth building on. What can be owned is the reference implementation that is
right about the text; the crosswalks between canonical and authority shapes; the evaluation sets
built from real labels under agreement; the proof layer; and the trust that follows from being
correct first. None of that is declared. It is earned, one real document at a time.

**What would make it worth a great deal.** One client's real products in the record, answers
served to their staff with proof, an approval signed in it, and the engine converting their next
label. At that point the offer is a platform with a demonstrated moat, and the same core sells
three times over: labelling, regulatory operations and quality.

**What would kill it.** Generalising the engine before proving it on one document set. Letting
model text into the record for convenience. Selling "AI" or "FHIR" instead of "answers you can
defend in an inspection". Spending on surfaces before approval and a real label.

## How we know we are moving toward it

- Every roadmap item names the evidence that would show it delivered, and the evidence is a run,
  a record or an object, never a description.
- The audit record for a live answer shows find, fetch **and verify** under one turn id.
- A real product's label is in the record, and its owner's staff have asked it questions.
- An approval exists that a person signed and that the query service enforces.
- The engine's output on its first document set is measured against a labelled evaluation set,
  and the measurement is in the repository.

## What this is not

Not a labelling authoring tool. Not a document management system. Not a general assistant. Not a
patient-facing product. Not a generator of label text. Each of those is someone else's product or
a later one, and mixing them in would undermine the one claim that makes this sellable: what it
shows you is what was approved, provably.
