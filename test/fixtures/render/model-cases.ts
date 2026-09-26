// Synthetic sections that exercise every default and rule of T's model output
// (docs/design/authority-import-renderer-model.md, M3) beyond what the pinned labels hold: each is
// drawn by scripts/render/check.ts in the renderer image and compared with Chrome, so a default the
// addendum states wrongly fails CI before it fails a label. Not for clinical use.

export type ModelCase = { name: string; inner: string };

export const MODEL_CASES: ModelCase[] = [
  {
    // A `border="0"` attribute sets the table's widths to 0 whatever style is declared (the fifth
    // review); `align` is matched without case but not trimmed.
    name: "border-zero-and-align",
    inner:
      '<table border="0" style="border-style:solid"><tr><td>a</td></tr></table>' +
      '<table border="00" style="border-top-style:dashed"><tr><td>b</td></tr></table>' +
      '<table align=" center "><tr><td>c</td></tr></table><table align="CENTER"><tr><td>d</td></tr></table>',
  },
  {
    // The elements no other case drew (the first code review of PR 3c-B): `abbr`, a carried
    // picture, a `tfoot` with its own alignment and border colour.
    name: "abbr-picture-and-footer",
    inner:
      '<p><abbr>SmPC</abbr> x <img src="data:image/png;base64,iVBORw0KGgo="/> y</p>' +
      '<table><tbody><tr><td>y</td></tr></tbody><tfoot valign="bottom" style="border-color:#009900">' +
      '<tr><td style="border-top-style:solid">x</td></tr></tfoot></table>',
  },
  {
    // Row groups and rows inherit border colours always, cells under the attribute too, and a
    // colour a shorthand omits is `currentcolor`, resolved by each element (the third review).
    name: "border-colour-inheritance",
    inner:
      '<table style="border-color:#990000"><thead style="border-bottom-style:solid"><tr style="border-top-style:solid"><td>a</td></tr></thead></table>' +
      '<table border="1"><tr style="border:1px solid"><td style="color:#990000">b</td></tr>' +
      '<tr style="color:#000099;border:1px solid"><td style="color:#990000">c</td></tr></table>' +
      '<table border="1" style="color:#000099;border:2px solid"><tr><td style="color:#990000">d</td></tr></table>' +
      '<table border="1"><tbody style="border:1px solid;color:#000099"><tr style="color:#990000"><td style="color:#006600">e</td></tr></tbody></table>',
  },
  {
    // A table's `border` attribute merged with declarations one longhand at a time, and a cell's
    // attribute border colour inherited through its row, row group and table (the second review).
    name: "border-attribute-longhands",
    inner:
      '<table border="1"><tr><td style="border-style:solid">a</td><td style="border-color:blue">b</td>' +
      '<td style="border-width:2px">c</td><td style="border-top-style:dotted;border-left-color:blue">d</td></tr>' +
      '<tr style="border-color:#008000"><td>e</td><td>f</td><td>g</td><td>h</td></tr></table>' +
      '<table border="2" style="border-color:blue;border-top-width:1px"><thead style="border-color:#800080">' +
      "<tr><td>i</td></tr></thead><tbody><tr><td>j</td></tr></tbody></table>" +
      '<table border="2" style="border-style:solid"><tr><td>k</td></tr></table>',
  },
  {
    // M1: a CR or LF by reference is the DOM's own code point; only raw CR LF and CR are joined.
    name: "line-ends-by-reference",
    inner: "<p>a&#13;&#10;b\r&#10;c&#13;\nd</p>",
  },
  {
    // M1: whitespace between table parts and list items, and CR LF, lone CR and a CR reference.
    name: "line-ends-and-inter-element-text",
    inner: "<table>\r\n <tr>\n<td>a\r\nb\rc&#13;d</td></tr></table><ul> <li>e</li>\n</ul>",
  },
  {
    name: "weights-and-styles",
    inner:
      '<p style="font-weight:lighter">a<b>b<strong>c</strong></b><span style="font-weight:bolder">d</span></p>' +
      '<p style="font-weight:900"><span style="font-weight:lighter">e</span><b>f</b></p>' +
      '<p style="font-weight:600"><span style="font-weight:lighter">g</span><span style="font-weight:bolder">h</span></p>' +
      '<p><i>i</i><em>j</em><cite>k</cite><span style="font-style:oblique">l</span><i style="font-style:normal">m</i></p>',
  },
  {
    name: "headings-and-rules",
    inner:
      "<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6><hr/>" +
      '<hr style="color:red"/><blockquote>g</blockquote><p style="margin-top:3pt">h</p>',
  },
  {
    name: "nested-lists",
    inner:
      "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul><ul><li>c<ol><li>d</li></ol></li></ul>" +
      '<ol type="a" start="3"><li>e</li><li>f<dl><dt>g</dt><dd>h</dd></dl></li></ol>' +
      '<dl><dt>k</dt><dd>l<ul><li>i</li></ul></dd></dl><ol type="i"><li>j</li></ol>',
  },
  {
    name: "sizes",
    inner:
      '<p style="font-size:9pt;line-height:1.15">a<sup>2</sup><sub>4</sub><small>3</small>' +
      '<span style="font-size:120%;line-height:1.5em">b</span></p>' +
      '<p style="font-size:7pt;line-height:120%">c<small>d</small></p><h4 style="font-size:11pt">e</h4>',
  },
  {
    name: "shifts",
    inner:
      '<p style="font-size:11pt">x <span style="position:relative;top:-3pt">a</span> y ' +
      '<span style="position:relative;bottom:0.3em">b</span> z <span style="vertical-align:4pt">c</span> w ' +
      '<span style="vertical-align:-0.2em">d</span> v <span style="vertical-align:super">e</span> u ' +
      '<span style="vertical-align:sub">f</span> t</p>',
  },
  {
    name: "bordered-table",
    inner:
      '<table border="2" cellspacing="3" cellpadding="4"><caption>a</caption>' +
      '<thead valign="top"><tr><th>b</th><td style="border-bottom:1pt solid #333333">c</td></tr></thead>' +
      '<tbody><tr valign="bottom"><td>d</td><td valign="middle">e</td></tr>' +
      '<tr><td>g</td><td style="vertical-align:top">f</td></tr></tbody></table>',
  },
  {
    name: "collapsed-and-centred-tables",
    inner:
      '<table style="border-collapse:collapse" cellspacing="1"><tr><td style="border:0.5pt solid black">a</td>' +
      '<td style="border:3pt dotted black;padding:2.4pt">c</td></tr></table>' +
      '<table><tr><td style="border:1.5pt double black">b</td></tr></table>' +
      '<table align="center"><tr><td>d</td></tr></table>' +
      '<table align="center" style="margin-left:6pt"><tr><td>e</td></tr></table>',
  },
  {
    name: "backgrounds-and-colours",
    inner:
      '<p style="background:#eeeeee;color:#333333">a<span style="background-color:#ffff00">b</span>' +
      '<a href="https://example.org/">c</a><a href="https://example.org/" style="color:#006600">d</a></p>' +
      '<table><tr><td style="background:#dddddd;border:1pt solid">e<u>f</u></td></tr></table>',
  },
  {
    name: "offsets",
    inner:
      '<div style="margin-left:12pt;padding-left:6pt;text-indent:-6pt">a' +
      '<p style="text-indent:10pt;margin-right:4pt">b</p><table><tr><td>c</td></tr></table></div>' +
      '<dl><dd style="margin-left:20pt">d</dd></dl><blockquote style="margin:0">e</blockquote>',
  },
];
