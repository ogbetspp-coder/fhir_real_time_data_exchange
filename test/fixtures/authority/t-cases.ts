// T's cases (docs/design/authority-import-t.md): each rule's both sides and each refusal reason,
// among them the design reviews' repros. `expected` is T(div) or the reason T refuses with; the
// div given is the root's content. `evidence` is the plus-sign waiver's (T5's second pass).
// test/authority/t.test.ts checks them; scripts/authority/generate-vectors.ts records them.

export type TCase = {
  name: string;
  inner: string;
  // The root's start tag, where a case needs one other than the plain XHTML `div`.
  root?: string;
  evidence?: string[];
  expected: { div: string } | { refused: string };
};

const ok = (div: string): { div: string } => ({ div });
const no = (refused: string): { refused: string } => ({ refused });

export const T_CASES: TCase[] = [
  // --- accepted: presentation removed, words kept ---
  {
    name: "styles-and-classes-deleted",
    inner:
      '<p class="MsoNormal" style="margin: 0cm 0cm 0.0001pt; font-size: 11pt; font-family: \'Times New Roman\', serif; color: #231f20;">Take one tablet.</p>',
    expected: ok("<p>Take one tablet.</p>"),
  },
  {
    name: "span-u-and-link-unwrapped",
    inner:
      '<p><span style="color: black;"><u>Posology</u> see <a href="https://www.ema.europa.eu" style="color: blue; text-decoration: underline;">www.ema.europa.eu</a></span></p>',
    expected: ok("<p>Posology see www.ema.europa.eu</p>"),
  },
  {
    name: "hanging-indent-kept-in-its-box",
    inner:
      '<p style="margin: 0cm 0cm 0.0001pt 1cm; text-indent: -1cm;">1&#160;&#160; Pneumonia</p>',
    expected: ok("<p>1&#160;&#160; Pneumonia</p>"),
  },
  {
    name: "raised-digit-folded-to-sup",
    inner:
      '<p style="font-size: 11pt;">platelets &gt; 100 x 10<span style="font-size: 7.0pt; position: relative; top: -5.0pt;">9</span>/l</p>',
    expected: ok("<p>platelets &gt; 100 x 10<sup>9</sup>/l</p>"),
  },
  {
    name: "lowered-run-folded-to-sub",
    inner:
      '<p style="font-size: 11pt;">(CI<span style="font-size: 7.0pt; position: relative; top: 1.5pt;">95%</span>)</p>',
    expected: ok("<p>(CI<sub>95%</sub>)</p>"),
  },
  {
    name: "small-shift-deleted",
    inner:
      '<p style="font-size: 11pt;"><span style="position: relative; top: .5pt;">Table 2</span> response</p>',
    expected: ok("<p>Table 2 response</p>"),
  },
  {
    name: "table-attributes-deleted-spans-kept",
    inner:
      '<table class="MsoNormalTable" style="border-collapse: collapse;" border="0" cellspacing="0" cellpadding="0"><tbody><tr><td style="width: 125.9pt; border: solid windowtext 1.0pt; padding: 0cm 5.4pt;" colspan="2" valign="top"><p>Dose</p></td></tr><tr><td style="border: solid windowtext 1.0pt; padding: 0cm 5.4pt;" valign="top"><p>1</p></td><td style="border: solid windowtext 1.0pt; padding: 0cm 5.4pt;" valign="top"><p>5 mg</p></td></tr></tbody></table>',
    expected: ok(
      '<table><tbody><tr><td colspan="2"><p>Dose</p></td></tr><tr><td><p>1</p></td><td><p>5 mg</p></td></tr></tbody></table>',
    ),
  },
  {
    name: "ordered-list-keeps-type-and-start",
    inner:
      '<ol type="a" start="2" style="margin-top: 0px; margin-bottom: 0cm;"><li style="font-size: 11pt;">first</li><li style="font-size: 11pt;">second</li></ol>',
    expected: ok('<ol type="a" start="2"><li>first</li><li>second</li></ol>'),
  },
  {
    name: "ltr-and-language-deleted",
    inner: '<p dir="ltr"><span lang="EN-GB">Dose</span></p>',
    expected: ok("<p>Dose</p>"),
  },
  {
    name: "inline-shading-at-normal-line-height",
    inner:
      '<p style="line-height: normal;"><span style="background: lightgrey;">via the national reporting system</span></p>',
    expected: ok("<p>via the national reporting system</p>"),
  },
  {
    name: "plus-in-underlined-subheading-waived-on-evidence",
    inner: "<p><u>Posology for Ph+ ALL in children</u></p>",
    evidence: ["Ph+"],
    expected: ok("<p>Posology for Ph+ ALL in children</p>"),
  },
  // --- refused ---
  { name: "unknown-element", inner: "<p><font>x</font></p>", expected: no("element") },
  { name: "code-element", inner: "<p><code>x</code></p>", expected: no("element") },
  {
    name: "bgcolor-attribute",
    inner: '<table bgcolor="black"><tr><td>x</td></tr></table>',
    expected: no("attribute"),
  },
  {
    name: "right-to-left-dir",
    inner: '<p dir="rtl">10 mg or 20 mg</p>',
    expected: no("attribute"),
  },
  {
    name: "floated-table",
    inner: '<table align="left"><tr><td>x</td></tr></table>',
    expected: no("attribute"),
  },
  {
    name: "abbreviation-title",
    inner: '<p>CrCl <abbr title="x">&lt;</abbr> 30</p>',
    expected: no("attribute"),
  },
  {
    name: "important",
    inner: '<p style="color: red !important">x</p>',
    expected: no("css-grammar"),
  },
  {
    name: "bracket-in-any-value",
    inner: '<p style="font-size:24pt; mso-x:(; font-size:12pt; mso-y:)">x</p>',
    expected: no("css-grammar"),
  },
  {
    name: "brace-in-any-value",
    inner: '<p style="color:#fff;mso-x:{;color:#000;mso-y:}">x</p>',
    expected: no("css-grammar"),
  },
  { name: "display-none", inner: '<p style="display: none">x</p>', expected: no("css-property") },
  {
    name: "letter-spacing",
    inner: '<p style="letter-spacing: -2pt">x</p>',
    expected: no("css-property"),
  },
  {
    name: "number-ending-in-a-point",
    inner: '<p style="font-size: 3pt"><span style="font-size: 12.pt">x</span></p>',
    expected: no("css-value"),
  },
  {
    name: "five-value-margin",
    inner: '<p style="margin: 0 0 0 0 5pt">x</p>',
    expected: no("css-value"),
  },
  {
    name: "position-absolute",
    inner: '<p><span style="position: absolute; top: 0">x</span></p>',
    expected: no("css-value"),
  },
  { name: "faint-text", inner: '<p style="color: #cccccc">x</p>', expected: no("contrast") },
  {
    name: "white-text-pulled-out-of-its-black-box",
    inner:
      '<p style="margin-left: 1cm; text-indent: -1cm; background: black; color: white">Do not exceed</p>',
    expected: no("contrast"),
  },
  {
    name: "negative-margin-in-a-cell",
    inner: '<table><tr><td style="padding: 0"><p style="margin-left: -3pt">x</p></td></tr></table>',
    expected: no("offset"),
  },
  {
    name: "positive-indent-in-a-cell",
    inner: '<table><tr><td><p style="text-indent: 80pt">5 mg</p></td></tr></table>',
    expected: no("offset"),
  },
  {
    name: "margin-on-inline",
    inner: '<p>CrCl &gt;<span style="margin-left: -1.65pt">_</span></p>',
    expected: no("offset"),
  },
  { name: "em-on-a-margin", inner: '<p style="margin-left: 1em">x</p>', expected: no("css-value") },
  {
    name: "margin-beyond-bound",
    inner: '<p style="margin-left: 3000pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "line-under-its-font-size",
    inner: '<p style="font-size: 12pt; line-height: 0.9em">x</p>',
    expected: no("line-height"),
  },
  {
    name: "text-under-five-points",
    inner: '<p style="font-size: 4pt">x</p>',
    expected: no("font-size"),
  },
  {
    name: "text-under-half-its-block",
    inner: '<p style="font-size: 24pt">Dose <span style="font-size: 10pt">not</span></p>',
    expected: no("font-size"),
  },
  {
    name: "font-without-a-pinned-substitute",
    inner: '<p style="font-family: Verdana">x</p>',
    expected: no("font"),
  },
  {
    name: "quoted-generic",
    inner: "<p style=\"font-family: 'serif'\">x</p>",
    expected: no("font"),
  },
  {
    name: "list-without-its-marker-padding",
    inner: '<ol style="padding-left: 0"><li>x</li></ol>',
    expected: no("list"),
  },
  {
    name: "list-item-pulled-left",
    inner: '<ol><li style="margin-left: -30pt">x</li></ol>',
    expected: no("list"),
  },
  {
    name: "cells-without-space-or-border",
    inner:
      '<table cellspacing="0" cellpadding="0"><tr><td><p style="text-align: right">Dose 1</p></td><td><p>5 mg</p></td></tr></table>',
    expected: no("table-edge"),
  },
  {
    name: "shift-between-the-bounds",
    inner:
      '<p style="font-size: 12pt">10<span style="position: relative; top: -1.8pt">2</span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "whole-line-raised",
    inner: '<p><span style="position: relative; top: -3.5pt">Take 200 mg twice daily.</span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "raise-into-the-line-above",
    inner:
      '<p style="font-size: 12pt">10<span style="font-size: 7pt; position: relative; top: -14pt">9</span>/l</p>',
    expected: no("baseline-shift"),
  },
  {
    name: "sup-vertical-align-replaced",
    inner: '<p>10<sup style="vertical-align: baseline">6</sup></p>',
    expected: no("css-value"),
  },
  { name: "underlined-less-than", inner: "<p>CrCl <u>&lt;</u> 30</p>", expected: no("underline") },
  { name: "underlined-ordinal", inner: "<p>1<u>a</u> dose</p>", expected: no("underline") },
  { name: "dash-that-reads-as-equals", inner: "<p><u>a⸗b</u></p>", expected: no("underline") },
  {
    name: "plus-without-evidence",
    inner: "<p><u>Posology for Ph+ ALL in children</u></p>",
    expected: no("underline"),
  },
  { name: "block-inside-a-paragraph", inner: "<p>a<div>b</div>c</p>", expected: no("markup") },
  {
    name: "definition-inside-a-paragraph",
    inner: "<p>Take <dd>2 tablets</dd>daily</p>",
    expected: no("markup"),
  },
  {
    name: "span-directly-in-a-table",
    inner: "<table><span><tr><td>x</td></tr></span></table>",
    expected: no("markup"),
  },
  {
    name: "self-closing-span",
    inner: '<p>Take <span style="font-size:1pt"/>4 tablets</p>',
    expected: no("markup"),
  },
  {
    name: "upper-case-attribute",
    inner: '<p STYLE="font-size:11pt">x</p>',
    expected: no("markup"),
  },
  // --- CSS grammar and values ---
  {
    name: "function-other-than-rgb",
    inner: '<p style="color: calc(1)">x</p>',
    expected: no("css-grammar"),
  },
  {
    name: "line-break-in-a-quoted-family",
    inner: "<p style=\"font-family: 'Times\nNew'\">x</p>",
    expected: no("css-grammar"),
  },
  {
    name: "declaration-without-a-colon",
    inner: '<p style="color">x</p>',
    expected: no("css-grammar"),
  },
  {
    name: "property-name-not-an-identifier",
    inner: '<p style="1color: red">x</p>',
    expected: no("css-grammar"),
  },
  {
    name: "quote-outside-font-family",
    inner: "<p style=\"color: 'red'\">x</p>",
    expected: no("css-grammar"),
  },
  { name: "global-keyword", inner: '<p style="color: inherit">x</p>', expected: no("css-grammar") },
  {
    name: "current-colour",
    inner: '<table><tr><td style="border: 1pt solid currentcolor">x</td></tr></table>',
    expected: no("css-grammar"),
  },
  {
    name: "family-by-reference",
    inner: '<p style="font-family: &quot;Times New Roman&quot;, serif">x</p>',
    expected: ok("<p>x</p>"),
  },
  { name: "unitless-length", inner: '<p style="margin-left: 5">x</p>', expected: no("css-value") },
  {
    name: "short-hex-and-rgb-colours",
    inner: '<p style="color: #333"><span style="color: rgb(35, 31, 32)">x</span></p>',
    expected: ok("<p>x</p>"),
  },
  {
    name: "rgb-channel-over-255",
    inner: '<p style="color: rgb(300, 0, 0)">x</p>',
    expected: no("css-value"),
  },
  { name: "unknown-colour", inner: '<p style="color: blurple">x</p>', expected: no("css-value") },
  {
    name: "transparent-background",
    inner: '<p style="background: transparent">x</p>',
    expected: ok("<p>x</p>"),
  },
  { name: "heading-size", inner: "<h2>Dose</h2>", expected: ok("<h2>Dose</h2>") },
  {
    name: "word-properties",
    inner:
      '<p style="mso-bidi-font-size: 11.0pt; tab-stops: 35.4pt; text-autospace: none; page-break-after: avoid">x</p>',
    expected: ok("<p>x</p>"),
  },
  {
    name: "text-autospace-other",
    inner: '<p style="text-autospace: ideograph-alpha">x</p>',
    expected: no("css-value"),
  },
  { name: "zero-font-size", inner: '<p style="font-size: 0pt">x</p>', expected: no("font-size") },
  {
    name: "relative-font-size",
    inner:
      '<p style="font-size: 12pt">a <span style="font-size: 90%">b</span> <span style="font-size: 0.8em">c</span></p>',
    expected: ok("<p>a b c</p>"),
  },
  {
    name: "zero-relative-font-size",
    inner: '<p><span style="font-size: 0em">x</span></p>',
    expected: no("font-size"),
  },
  {
    name: "font-style-and-weight",
    inner: '<p style="font-style: italic; font-weight: bold">x</p>',
    expected: ok("<p>x</p>"),
  },
  {
    name: "font-style-other",
    inner: '<p style="font-style: slanted">x</p>',
    expected: no("css-value"),
  },
  {
    name: "font-weight-other",
    inner: '<p style="font-weight: heavy">x</p>',
    expected: no("css-value"),
  },
  {
    name: "transparent-text",
    inner: '<p style="color: transparent">x</p>',
    expected: no("css-value"),
  },
  {
    name: "number-and-length-line-heights",
    inner: '<p style="line-height: 1.2">a</p><p style="line-height: 13pt; font-size: 11pt">b</p>',
    expected: ok("<p>a</p><p>b</p>"),
  },
  {
    name: "line-height-length-under-a-larger-child",
    inner:
      '<p style="line-height: 13pt; font-size: 11pt">a <span style="font-size: 14pt">b</span></p>',
    expected: no("line-height"),
  },
  {
    name: "text-align-other",
    inner: '<p style="text-align: middle">x</p>',
    expected: no("css-value"),
  },
  {
    name: "width-outside-a-table",
    inner: '<p style="width: 100pt">x</p>',
    expected: no("css-property"),
  },
  {
    name: "width-in-em",
    inner: '<table><tr><td style="width: 3em">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "negative-width",
    inner: '<table><tr><td style="width: -3pt">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "collapse-on-a-cell",
    inner: '<table><tr><td style="border-collapse: collapse">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-image-other",
    inner: '<table><tr><td style="border-image: stretch">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-outside-a-table",
    inner: '<p style="border: 1pt solid black">x</p>',
    expected: no("css-property"),
  },
  {
    name: "border-with-four-parts",
    inner: '<table><tr><td style="border: 1pt solid black double">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-with-two-styles",
    inner: '<table><tr><td style="border: solid double">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-with-two-widths",
    inner: '<table><tr><td style="border: 1pt 2pt solid">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-with-a-negative-width",
    inner: '<table><tr><td style="border: -1pt solid black">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-with-two-colours",
    inner: '<table><tr><td style="border: solid black white">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-too-wide",
    inner: '<table><tr><td style="border: 4pt solid black">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-longhands",
    inner:
      '<table><tr><td style="border-top-width: 1pt; border-top-style: solid; border-top-color: black; border-bottom: thin dashed #231F20">x</td></tr></table>',
    expected: ok("<table><tr><td>x</td></tr></table>"),
  },
  {
    name: "border-longhand-style-other",
    inner: '<table><tr><td style="border-top-style: wavy">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "border-longhand-negative-width",
    inner: '<table><tr><td style="border-left-width: -2pt">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "top-in-percent",
    inner: '<p>a<span style="position: relative; top: -50%">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "bottom-in-percent",
    inner: '<p>a<span style="position: relative; bottom: 50%">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "top-without-position",
    inner: '<p>a<span style="top: -5pt">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "top-and-bottom",
    inner: '<p>a<span style="position: relative; top: -5pt; bottom: 1pt">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "position-without-an-offset",
    inner: '<p>a<span style="position: relative">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "overline",
    inner: '<p style="text-decoration: overline">x</p>',
    expected: no("css-value"),
  },
  // --- shifts ---
  {
    name: "cell-vertical-align",
    inner: '<table><tr><td style="vertical-align: middle">x</td></tr></table>',
    expected: ok("<table><tr><td>x</td></tr></table>"),
  },
  {
    name: "cell-vertical-align-super",
    inner: '<table><tr><td style="vertical-align: super">x</td></tr></table>',
    expected: no("css-value"),
  },
  {
    name: "vertical-align-and-position",
    inner: '<p>a<span style="position: relative; top: -5pt; vertical-align: super">b</span></p>',
    expected: no("css-value"),
  },
  {
    name: "vertical-align-on-a-block",
    inner: '<p style="vertical-align: super">x</p>',
    expected: no("css-value"),
  },
  {
    name: "vertical-align-super-folded",
    inner: '<p>x<span style="vertical-align: super">2</span></p>',
    expected: ok("<p>x<sup>2</sup></p>"),
  },
  {
    name: "vertical-align-sub-folded",
    inner: '<p>CO<span style="vertical-align: sub">2</span></p>',
    expected: ok("<p>CO<sub>2</sub></p>"),
  },
  {
    name: "vertical-align-length-folded",
    inner: '<p style="font-size: 12pt">10<span style="vertical-align: 5pt">6</span></p>',
    expected: ok("<p>10<sup>6</sup></p>"),
  },
  {
    name: "vertical-align-percent",
    inner: '<p>10<span style="vertical-align: 50%">6</span></p>',
    expected: no("css-value"),
  },
  {
    name: "vertical-align-baseline-kept-on-the-line",
    inner: '<p>10<span style="vertical-align: baseline">6</span></p>',
    expected: ok("<p>106</p>"),
  },
  {
    name: "position-on-sup",
    inner: '<p>10<sup style="position: relative; top: -1pt">6</sup></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "em-shift-folded",
    inner: '<p>10<span style="position: relative; top: -0.5em">2</span></p>',
    expected: ok("<p>10<sup>2</sup></p>"),
  },
  {
    name: "em-shift-deleted",
    inner: '<p><span style="position: relative; top: -0.05em">Dose</span> daily</p>',
    expected: ok("<p>Dose daily</p>"),
  },
  {
    name: "fold-over-an-unwrapped-span",
    inner:
      '<p style="font-size: 11pt">10<span style="font-size: 7pt; position: relative; top: -5pt"><span>9</span></span>/l</p>',
    expected: ok("<p>10<sup>9</sup>/l</p>"),
  },
  {
    name: "footnote-mark-before-a-word",
    inner:
      '<p style="font-size: 11pt"><span style="font-size: 7pt; position: relative; top: -5pt">1 </span>Haematological</p>',
    expected: ok("<p><sup>1 </sup>Haematological</p>"),
  },
  {
    name: "shift-on-a-picture",
    inner:
      '<p>Dose <span style="position: relative; top: -22pt"><img src="data:image/png;base64,iVBORw0KGgo="/></span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "shift-on-emphasis",
    inner: '<p>10<em style="position: relative; top: -5pt">2</em></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "shift-inside-sup",
    inner: '<p>10<sup><span style="position: relative; top: -3pt">2</span></sup></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "nested-shifts",
    inner:
      '<p>10<span style="position: relative; top: -3pt"><span style="position: relative; top: -3pt">2</span></span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "shifted-run-holding-strong",
    inner: '<p>10<span style="position: relative; top: -5pt"><strong>2</strong></span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "shifted-run-larger-than-its-parent",
    inner:
      '<p style="font-size: 10pt">10<span style="font-size: 12pt; position: relative; top: -4pt">9</span></p>',
    expected: no("baseline-shift"),
  },
  {
    name: "shifted-run-too-long",
    inner: '<p>a<span style="position: relative; top: -5pt">12345</span></p>',
    expected: no("baseline-shift"),
  },
  // --- attributes ---
  {
    name: "namespace-below-the-root",
    inner: '<p xmlns="http://www.w3.org/1999/xhtml">x</p>',
    expected: no("attribute"),
  },
  {
    name: "language-outside-the-eu",
    inner: '<p><span lang="zh">x</span></p>',
    expected: no("attribute"),
  },
  { name: "name-on-a-link", inner: '<p><a name="top">x</a></p>', expected: ok("<p>x</p>") },
  { name: "name-elsewhere", inner: '<p name="x">x</p>', expected: no("attribute") },
  { name: "title-deleted", inner: '<p title="x" id="y">x</p>', expected: ok("<p>x</p>") },
  { name: "valign-outside-a-table", inner: '<p valign="top">x</p>', expected: no("attribute") },
  {
    name: "valign-other",
    inner: '<table><tr><td valign="left">x</td></tr></table>',
    expected: no("attribute"),
  },
  {
    name: "width-attribute-outside-a-table",
    inner: '<p width="10">x</p>',
    expected: no("attribute"),
  },
  {
    name: "cell-width-and-nowrap-deleted",
    inner:
      '<table width="100"><tr><td width="50" nowrap="nowrap" align="center">x</td></tr></table>',
    expected: ok("<table><tr><td>x</td></tr></table>"),
  },
  {
    name: "paragraph-align-deleted",
    inner: '<p align="center">x</p><h1 align="left">y</h1>',
    expected: ok("<p>x</p><h1>y</h1>"),
  },
  {
    name: "align-on-inline",
    inner: '<p><span align="center">x</span></p>',
    expected: no("attribute"),
  },
  { name: "align-other", inner: '<p align="middle">x</p>', expected: no("attribute") },
  {
    name: "centred-table",
    inner: '<table align="center" summary="s"><tr><td>x</td></tr></table>',
    expected: ok("<table><tr><td>x</td></tr></table>"),
  },
  {
    name: "border-attribute-outside-a-table",
    inner: '<p border="1">x</p>',
    expected: no("attribute"),
  },
  {
    name: "cellspacing-not-a-number",
    inner: '<table cellspacing="1px"><tr><td>x</td></tr></table>',
    expected: no("attribute"),
  },
  {
    name: "href-outside-a-link",
    inner: '<p><span href="x">x</span></p>',
    expected: no("attribute"),
  },
  { name: "src-outside-a-picture", inner: '<p src="x">x</p>', expected: no("attribute") },
  { name: "colspan-outside-a-cell", inner: '<p colspan="2">x</p>', expected: no("attribute") },
  {
    name: "scope-kept-on-a-header-cell",
    inner: '<table><tr><th scope="col">x</th></tr></table>',
    expected: ok('<table><tr><th scope="col">x</th></tr></table>'),
  },
  {
    name: "scope-on-a-data-cell",
    inner: '<table><tr><td scope="col">x</td></tr></table>',
    expected: no("attribute"),
  },
  {
    name: "type-on-a-bullet-list",
    inner: '<ul type="disc"><li>x</li></ul>',
    expected: no("attribute"),
  },
  {
    name: "link-in-its-default-colours",
    inner: '<p><a href="https://www.ema.europa.eu">ema.europa.eu</a></p>',
    expected: ok("<p>ema.europa.eu</p>"),
  },
  {
    name: "link-in-a-faint-colour",
    inner: '<p><a href="https://www.ema.europa.eu" style="color: #dddddd">ema</a></p>',
    expected: no("contrast"),
  },
  // --- offsets ---
  {
    name: "negative-padding",
    inner: '<p style="padding-left: -2pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "negative-top-margin",
    inner: '<p style="margin-top: -2pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "indent-beyond-bound",
    inner: '<p style="text-indent: 200pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "root-pulled-left",
    root: '<div xmlns="http://www.w3.org/1999/xhtml" style="margin-left: -2pt">',
    inner: "<p>x</p>",
    expected: no("offset"),
  },
  {
    name: "row-with-padding",
    inner: '<table><tr style="padding: 1pt"><td>x</td></tr></table>',
    expected: no("offset"),
  },
  {
    name: "nested-sums-beyond-bound",
    inner: '<div style="margin-left: 100pt"><div style="margin-left: 100pt"><p>x</p></div></div>',
    expected: no("offset"),
  },
  {
    name: "block-in-a-list-item-pulled-left",
    inner: '<ol><li><p style="margin-left: -1pt">x</p></li></ol>',
    expected: no("list"),
  },
  {
    name: "list-item-with-negative-padding",
    inner: '<ul><li style="padding-left: -1pt">x</li></ul>',
    expected: no("list"),
  },
  {
    name: "block-pulled-out-of-the-section",
    inner: '<p style="margin-left: -5pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "right-overhang-within-bound",
    inner: '<p style="margin: 0cm -0.1pt 0.0001pt 0cm">x</p>',
    expected: ok("<p>x</p>"),
  },
  {
    name: "right-overhang-beyond-bound",
    inner: '<p style="margin-right: -2pt">x</p>',
    expected: no("offset"),
  },
  {
    name: "marker-wider-than-its-padding",
    inner: '<ol start="1000"><li style="font-size: 20pt">x</li></ol>',
    expected: no("list"),
  },
  // --- tables ---
  {
    name: "cells-under-a-row-group-and-a-row-span",
    inner:
      '<table cellspacing="0"><thead><tr><th style="padding: 0 5.4pt">A</th><th style="padding: 0 5.4pt">B</th></tr></thead><tbody><tr><td rowspan="2" style="padding: 0 5.4pt">1</td><td style="padding: 0 5.4pt">2</td></tr><tr><td style="padding: 0 5.4pt">3</td></tr></tbody></table>',
    expected: ok(
      '<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td rowspan="2">1</td><td>2</td></tr><tr><td>3</td></tr></tbody></table>',
    ),
  },
  {
    name: "cells-side-by-side-under-a-faint-border",
    inner:
      '<table cellspacing="0" cellpadding="0"><tr><td style="border-right: 1pt solid #eeeeee">Dose 1</td><td>5 mg</td></tr></table>',
    expected: no("table-edge"),
  },
  {
    name: "cells-side-by-side-under-a-drawn-border",
    inner:
      '<table cellspacing="0" cellpadding="0"><tr><td style="border-right: 1pt solid black">Dose 1</td><td>5 mg</td></tr></table>',
    expected: ok("<table><tr><td>Dose 1</td><td>5 mg</td></tr></table>"),
  },
  // --- underlines ---
  {
    name: "underline-over-a-raised-run",
    inner: "<p><u>x<sup>2</sup></u></p>",
    expected: no("underline"),
  },
  {
    name: "plus-waived-only-in-a-subheading",
    inner: "<table><tr><td><u>Ph+ ALL</u></td></tr></table>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "plus-waived-only-in-a-whole-underline",
    inner: "<p>Study <u>Ph+ ALL</u></p>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "plus-waived-only-for-its-own-token",
    inner: "<p><u>Studies in ER+ ALL</u></p>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "plus-after-one-letter",
    inner: "<p><u>Range x+ 2</u></p>",
    evidence: ["x+"],
    expected: no("underline"),
  },
  {
    name: "plus-before-a-digit",
    inner: "<p><u>Ph+2 ALL</u></p>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "plus-in-a-list-item",
    inner: "<ul><li><u>Ph+ ALL</u></li></ul>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "underline-in-a-block-holding-a-block",
    inner: "<div><u>Ph+ ALL</u><p>x</p></div>",
    evidence: ["Ph+"],
    expected: no("underline"),
  },
  {
    name: "underline-on-a-shaded-inline",
    inner:
      '<p style="line-height: normal"><span style="background: lightgrey; position: relative; top: .5pt">x</span></p>',
    expected: no("css-value"),
  },
  {
    name: "shading-under-a-tight-line",
    inner:
      '<p style="line-height: 13pt; font-size: 11pt"><span style="background: lightgrey">x</span></p>',
    expected: no("line-height"),
  },
  // --- markup ---
  {
    name: "definition-inside-a-definition",
    inner: "<dl><dd>a<dd>b</dd></dd></dl>",
    expected: no("markup"),
  },
  {
    name: "link-inside-a-link",
    inner: '<p><a href="x">a<a href="y">b</a></a></p>',
    expected: no("markup"),
  },
  { name: "paragraph-directly-in-a-list", inner: "<ul><p>x</p></ul>", expected: no("markup") },
  {
    name: "unknown-entity-in-an-attribute",
    inner: '<p title="&nbsp;">x</p>',
    expected: no("markup"),
  },
  { name: "reference-beyond-unicode", inner: "<p>&#x110000;</p>", expected: no("markup") },
  { name: "reference-to-a-surrogate", inner: "<p>&#xD800;</p>", expected: no("markup") },
  { name: "end-tag-that-does-not-match", inner: "<p>x</span>", expected: no("markup") },
  { name: "malformed-start-tag", inner: "<p =x>y</p>", expected: no("markup") },
  { name: "upper-case-element", inner: "<P>x</P>", expected: no("markup") },
  { name: "bare-ampersand", inner: "<p>a &bogus b</p>", expected: no("markup") },
  {
    name: "line-feed-in-text",
    inner: "<p>Take one\ntablet</p>",
    expected: ok("<p>Take one\ntablet</p>"),
  },
  {
    name: "text-directly-in-a-table",
    inner: "<table>x<tr><td>y</td></tr></table>",
    expected: no("markup"),
  },
  { name: "element-left-open", inner: "<p>x", expected: no("markup") },
];
