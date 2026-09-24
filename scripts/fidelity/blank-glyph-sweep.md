# Blank-glyph sweep

`fidelity-norm/3.0.0` section 6 treats as a gap every code point a reader sees as empty space.
Most of the gap set is defined by Unicode, but the blank glyphs, U+2800 and seven Mongolian and
Yi letters, come from a measurement. This file records that measurement so it can be repeated
when the fonts change.

## Method

These two review rounds, both on 2026-09-23, drew every assigned code point in Chrome and
measured its ink:

- round 15 found U+2800;
- round 16 swept the Basic Multilingual Plane, the Supplementary Multilingual and Ideographic
  Planes, and planes 3 and 14.

Round 16's sweep, in detail:

1. Chrome on macOS, headless, drew each code point on a canvas. The faces were the defaults
   (`serif`, `sans-serif`, `monospace`), each in regular, bold, italic and bold italic.
2. A code point is a blank glyph when a face draws it with no pixel of alpha above 40 while
   advancing the pen.
3. Each hit, and every Cf, Zs, Zl and Zp code point, was checked again as DOM text in both HTML
   and XML documents, with screenshots.

It found:

| Code points                                            | What Chrome draws                                                                 | Where it goes        |
| ------------------------------------------------------ | --------------------------------------------------------------------------------- | -------------------- |
| U+2800                                                 | a blank as wide as a letter, in every face                                        | gap                  |
| U+1878, U+18AA, U+A4A2, U+A4A3, U+A4B4, U+A4C1, U+A4C5 | an em-wide blank in the default serif face; inked in `sans-serif` and `monospace` | gap                  |
| U+FFF9–U+FFFB                                          | a blank 0.6 em wide, in every face                                                | refused in section 2 |

Nothing else was found: planes 3 and 14 hold only code points that were already gaps. The
Default_Ignorable code points need no measurement, because Unicode already puts them in the gap
set; some fonts draw a few of them, and reading past those only refuses more.

## Repeating it

The sweep needs `puppeteer-core` and a local Chrome. Neither is a dependency of this
repository. Run it outside the repository:

```js
// npm i puppeteer-core@24 in a scratch directory, then: node sweep.mjs > blanks.json
import puppeteer from "puppeteer-core";

const browser = await puppeteer.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage();
await page.setContent("<canvas id=c width=160 height=96></canvas>");
const faces = ["serif", "sans-serif", "monospace"].flatMap((face) =>
  ["", "bold ", "italic ", "bold italic "].map((style) => `${style}40px ${face}`),
);
const blanks = await page.evaluate((faces) => {
  const canvas = document.getElementById("c");
  const g = canvas.getContext("2d", { willReadFrequently: true });
  const unassigned = /^[\p{Cn}\p{Co}\p{Cs}]$/u;
  const ranges = [
    [0x0000, 0x30000],
    [0x30000, 0x32400],
    [0xe0000, 0xe0200],
  ];
  const found = [];
  for (const [low, high] of ranges) {
    for (let codePoint = low; codePoint < high; codePoint += 1) {
      const character = String.fromCodePoint(codePoint);
      if (unassigned.test(character)) continue;
      for (const font of faces) {
        g.font = font;
        g.clearRect(0, 0, 160, 96);
        g.fillText(character, 40, 64);
        const pixels = g.getImageData(0, 0, 160, 96).data;
        let ink = 0;
        for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 40) ink += 1;
        if (ink === 0 && g.measureText(character).width > 0) {
          found.push({ codePoint: codePoint.toString(16), font });
        }
      }
    }
  }
  return found;
}, faces);
console.log(JSON.stringify(blanks, null, 1));
await browser.close();
```

Some results need a second look before they go into the gap set:

- **Already handled.** Code points that are already whitespace, gaps, forbidden (section 2) or
  reserved.
- **Canvas artefacts.** Anything the canvas draws blank but DOM text draws inked, for example
  Tibetan signs under a `lang="zh"` root.

Confirm every remaining hit as DOM text in both HTML and XML documents before changing
`BLANK_GLYPHS` in:

- `src/fidelity/normalize.ts`
- `zone-a/src/zone_a/fidelity/normalize.py`
- `agent/src/verifiable_answer_agent/quote_edge.py`

Adding a blank glyph there only refuses more. Section 8 of the specification says what version
change it needs.
