// Where the red and the blue ink are in each band of each page of a PDF (scripts/word_gaps.py).
//
//     swiftc -O scripts/ink_bands.swift -o ink_bands
//     ink_bands FILE.pdf TOP LINE ROWS SCALE
//
// Each page is drawn at SCALE pixels a point on white. Band b of a page runs from TOP + b * LINE
// to TOP + (b + 1) * LINE points below the page's top edge; for each of ROWS bands it prints the
// page (from 1), the band (from 0), where the red ink and the blue ink start and end across it, in
// points from the page's left edge, then where the red and the blue ink start and end down it, in
// points from the page's top edge; -1 where there is none. A pixel is red ink where its
// red is at least 64 above its green and blue (a quarter of a red glyph's coverage), blue alike.
// The bitmap's memory holds the page's top row first: rows are read as they are, never flipped.
import CoreGraphics
import Foundation

let arguments = CommandLine.arguments
guard arguments.count == 6,
  let document = CGPDFDocument(URL(fileURLWithPath: arguments[1]) as CFURL),
  let top = Double(arguments[2]), let line = Double(arguments[3]),
  let rows = Int(arguments[4]), let scale = Double(arguments[5])
else {
  FileHandle.standardError.write("usage: ink_bands FILE.pdf TOP LINE ROWS SCALE\n".data(using: .utf8)!)
  exit(2)
}
for number in 1...document.numberOfPages {
  let page = document.page(at: number)!
  let box = page.getBoxRect(.mediaBox)
  let width = Int((box.width * scale).rounded(.up))
  let height = Int((box.height * scale).rounded(.up))
  let context = CGContext(
    data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
    space: CGColorSpace(name: CGColorSpace.sRGB)!,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
  context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
  context.fill(CGRect(x: 0, y: 0, width: width, height: height))
  context.scaleBy(x: scale, y: scale)
  context.translateBy(x: -box.minX, y: -box.minY)
  context.drawPDFPage(page)
  let pixels = context.data!.bindMemory(to: UInt8.self, capacity: width * height * 4)
  func points(_ column: Int?) -> String {
    column.map { String(format: "%.3f", Double($0) / scale) } ?? "-1"
  }
  for band in 0..<rows {
    let first = Int(((top + Double(band) * line) * scale).rounded())
    let last = min(height, Int(((top + Double(band + 1) * line) * scale).rounded()))
    var red: (Int, Int)? = nil
    var blue: (Int, Int)? = nil
    var redRows: (Int, Int)? = nil
    var blueRows: (Int, Int)? = nil
    for row in first..<last {
      for column in 0..<width {
        let at = (row * width + column) * 4
        let (r, g, b) = (Int(pixels[at]), Int(pixels[at + 1]), Int(pixels[at + 2]))
        if r - max(g, b) >= 64 {
          red = (min(red?.0 ?? column, column), max(red?.1 ?? column, column))
          redRows = (min(redRows?.0 ?? row, row), max(redRows?.1 ?? row, row))
        }
        if b - max(r, g) >= 64 {
          blue = (min(blue?.0 ?? column, column), max(blue?.1 ?? column, column))
          blueRows = (min(blueRows?.0 ?? row, row), max(blueRows?.1 ?? row, row))
        }
      }
    }
    // An ink run's end is the right edge of its last column, or the bottom edge of its last row.
    print(
      "\(number)\t\(band)\t\(points(red?.0))\t\(points(red.map { $0.1 + 1 }))"
        + "\t\(points(blue?.0))\t\(points(blue.map { $0.1 + 1 }))"
        + "\t\(points(redRows?.0))\t\(points(redRows.map { $0.1 + 1 }))"
        + "\t\(points(blueRows?.0))\t\(points(blueRows.map { $0.1 + 1 }))")
  }
}
