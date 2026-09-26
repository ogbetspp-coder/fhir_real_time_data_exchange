import { transformSection } from "../../src/authority/t/transform.js";

// Run by test/authority/t-cost.test.ts in a child process with a bounded heap: T on a 4.28 MB
// section of 19 990 runs (the first code review of PR 3c-B measured it out of memory at 384 MB
// once the model's bookkeeping was recorded on the import's own path). Not for clinical use.
const word = "abcdefghij ".repeat(20).slice(0, 200);
const inner = Array.from({ length: 19990 }, () => `<span>${word}</span>`).join(" ");
const div = `<div xmlns="http://www.w3.org/1999/xhtml"><p>${inner}</p><p>not for clinical use</p></div>`;
const { div: output } = transformSection(div);
process.stdout.write(`${div.length} ${output.length}\n`);
