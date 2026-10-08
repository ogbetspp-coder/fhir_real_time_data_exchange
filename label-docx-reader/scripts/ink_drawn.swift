// What Word drew in a PDF of a drawing case (scripts/word_drawn.py).
//
//     swiftc -O scripts/ink_drawn.swift -o ink_drawn
//     ink_drawn rows FILE.pdf TOP LINE ROWS SCALE
//     ink_drawn picture FILE.pdf SCALE
//
// Each page is drawn at SCALE pixels a point on white; the bitmap's memory holds the page's top
// row first, so rows are read as they are, never flipped.
//
// rows: band b of a page runs from TOP + b * LINE to TOP + (b + 1) * LINE points below the page's
// top edge (its first two pixel rows left out, which the row above may paint); for each of ROWS bands of each page it prints the page (from 1), the band (from 0),
// where the red ink and the blue ink start and end across it (points from the page's left edge,
// -1 where there is none), how many pixels that are not white stand between the red's end and
// the blue's start (-1 where either is missing), and the three colours most pixels of the band
// have other than white, as RRGGBB:count. Red ink is a pixel whose red is at least 64 above its
// green and blue, blue alike; white is FFFFFF exactly.
//
// picture: on the first page, the box of the pixels that are clearly red or blue (the probe's
// checkerboard): its left and top, width and height in pixels, the first 16 hex digits of the
// SHA-256 of its pixels' RGB, how many pixels 3 to 12 from it are not white (any channel under
// 250), and how many more than 2 from it are coloured (channels more than 40 apart). The two
// pixels next to the box are left out: they are the picture's own edge, where it is drawn off
// the pixel grid.
import CoreGraphics
import CryptoKit
import Foundation

func fail() -> Never {
  FileHandle.standardError.write(
    "usage: ink_drawn rows FILE.pdf TOP LINE ROWS SCALE | picture FILE.pdf SCALE\n".data(using: .utf8)!)
  exit(2)
}

let arguments = CommandLine.arguments
guard arguments.count >= 4, let document = CGPDFDocument(URL(fileURLWithPath: arguments[2]) as CFURL)
else { fail() }

func draw(_ number: Int, _ scale: Double) -> ([UInt8], Int, Int) {
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
  let data = context.data!.bindMemory(to: UInt8.self, capacity: width * height * 4)
  return (Array(UnsafeBufferPointer(start: data, count: width * height * 4)), width, height)
}

if arguments[1] == "rows" {
  guard arguments.count == 7, let top = Double(arguments[3]), let line = Double(arguments[4]),
    let rows = Int(arguments[5]), let scale = Double(arguments[6])
  else { fail() }
  for number in 1...document.numberOfPages {
    let (pixels, width, height) = draw(number, scale)
    func points(_ column: Int?) -> String {
      column.map { String(format: "%.3f", Double($0) / scale) } ?? "-1"
    }
    for band in 0..<rows {
      // The band's first two pixel rows are left out: the row above paints down into them where
      // its shading's edge is off the pixel grid.
      let first = Int(((top + Double(band) * line) * scale).rounded()) + 2
      let last = min(height, Int(((top + Double(band + 1) * line) * scale).rounded()))
      var red: (Int, Int)? = nil
      var blue: (Int, Int)? = nil
      var colours: [Int: Int] = [:]
      for row in first..<max(first, last) {
        for column in 0..<width {
          let at = (row * width + column) * 4
          let (r, g, b) = (Int(pixels[at]), Int(pixels[at + 1]), Int(pixels[at + 2]))
          if r - max(g, b) >= 64 { red = (min(red?.0 ?? column, column), max(red?.1 ?? column, column)) }
          if b - max(r, g) >= 64 { blue = (min(blue?.0 ?? column, column), max(blue?.1 ?? column, column)) }
          let colour = r << 16 | g << 8 | b
          if colour != 0xFFFFFF { colours[colour, default: 0] += 1 }
        }
      }
      var between = -1
      if let red = red, let blue = blue {
        between = 0
        for row in first..<max(first, last) {
          for column in (red.1 + 1)..<max(red.1 + 1, blue.0) {
            let at = (row * width + column) * 4
            if pixels[at] != 255 || pixels[at + 1] != 255 || pixels[at + 2] != 255 { between += 1 }
          }
        }
      }
      let most = colours.sorted { $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key }
        .prefix(3).map { String(format: "%06X:%d", $0.key, $0.value) }.joined(separator: " ")
      // An ink run's end is the right edge of its last column.
      print(
        "\(number)\t\(band)\t\(points(red?.0))\t\(points(red.map { $0.1 + 1 }))"
          + "\t\(points(blue?.0))\t\(points(blue.map { $0.1 + 1 }))\t\(between)\t\(most)")
    }
  }
} else if arguments[1] == "picture" {
  guard arguments.count == 4, let scale = Double(arguments[3]) else { fail() }
  let (pixels, width, height) = draw(1, scale)
  func at(_ x: Int, _ y: Int) -> (Int, Int, Int) {
    let i = (y * width + x) * 4
    return (Int(pixels[i]), Int(pixels[i + 1]), Int(pixels[i + 2]))
  }
  var (x0, y0, x1, y1) = (width, height, -1, -1)
  for y in 0..<height {
    for x in 0..<width {
      let (r, g, b) = at(x, y)
      if (r > 150 && g < 100 && b < 100) || (b > 150 && r < 100 && g < 100) {
        (x0, y0, x1, y1) = (min(x0, x), min(y0, y), max(x1, x), max(y1, y))
      }
    }
  }
  if x1 < 0 {
    print("-1\t-1\t0\t0\t-\t0\t0")
    exit(0)
  }
  var inside = [UInt8]()
  for y in y0...y1 {
    for x in x0...x1 {
      let (r, g, b) = at(x, y)
      inside += [UInt8(r), UInt8(g), UInt8(b)]
    }
  }
  let digest = SHA256.hash(data: Data(inside)).map { String(format: "%02x", $0) }.joined().prefix(16)
  var (ring, elsewhere) = (0, 0)
  for y in 0..<height {
    for x in 0..<width {
      // The box's own edge, two pixels deep, is the picture's: drawn off the pixel grid, its
      // last row or column is partly covered.
      if x >= x0 - 2 && x <= x1 + 2 && y >= y0 - 2 && y <= y1 + 2 { continue }
      let (r, g, b) = at(x, y)
      if x >= x0 - 12 && x <= x1 + 12 && y >= y0 - 12 && y <= y1 + 12 && (r < 250 || g < 250 || b < 250) {
        ring += 1
      }
      if max(r, g, b) - min(r, g, b) > 40 { elsewhere += 1 }
    }
  }
  print("\(x0)\t\(y0)\t\(x1 - x0 + 1)\t\(y1 - y0 + 1)\t\(digest)\t\(ring)\t\(elsewhere)")
} else {
  fail()
}
