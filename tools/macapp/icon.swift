// Draws the ICC Editor icon (a cricket ball on mown grass) as a 1024x1024 PNG.
//     swift tools/macapp/icon.swift out.png
// Used by tools/build_macos.py and tools/build_windows.py; the result is also kept as
// icc/editor/static/icon.png for the page's favicon and header.
import AppKit
import CoreGraphics

let size = 1024.0
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let space = CGColorSpaceCreateDeviceRGB()
let ctx = CGContext(data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8, bytesPerRow: 0,
                    space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: a)
}

// --- rounded-square tile (Apple icon grid: 824 px body, 100 px margin)
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let tilePath = CGPath(roundedRect: tile, cornerWidth: 186, cornerHeight: 186, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -14), blur: 30, color: rgb(0x000000, 0.35))
ctx.addPath(tilePath); ctx.setFillColor(rgb(0x1d6b45)); ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(tilePath); ctx.clip()
let grass = CGGradient(colorsSpace: space, colors: [rgb(0x3a9a62), rgb(0x1b5c3b)] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(grass, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
// mowing stripes
for i in stride(from: 0, to: 8, by: 2) {
    ctx.setFillColor(rgb(0xffffff, 0.06))
    ctx.fill(CGRect(x: 100 + Double(i) * 103, y: 100, width: 103, height: 824))
}
// the ball's shadow on the grass
ctx.setFillColor(rgb(0x000000, 0.28))
ctx.fillEllipse(in: CGRect(x: 300, y: 214, width: 440, height: 90))
ctx.restoreGState()

// --- the ball
let c = CGPoint(x: 512, y: 540), r = 262.0
let ball = CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)
ctx.saveGState()
ctx.addEllipse(in: ball); ctx.clip()
let leather = CGGradient(colorsSpace: space, colors: [rgb(0xe8394d), rgb(0xb3172b), rgb(0x6e0b17)] as CFArray,
                         locations: [0, 0.55, 1])!
ctx.drawRadialGradient(leather, startCenter: CGPoint(x: c.x - 90, y: c.y + 100), startRadius: 10,
                       endCenter: c, endRadius: r * 1.05, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])

// seam: a curved band across the ball, with stitching either side
ctx.translateBy(x: c.x, y: c.y)
ctx.rotate(by: -0.55)
func seamCurve(_ dx: Double) -> CGPath {
    let p = CGMutablePath()
    p.move(to: CGPoint(x: dx - 95, y: -r - 20))
    p.addCurve(to: CGPoint(x: dx - 95, y: r + 20), control1: CGPoint(x: dx + 25, y: -r * 0.4),
               control2: CGPoint(x: dx + 25, y: r * 0.4))
    return p
}
ctx.setLineCap(.round)
ctx.addPath(seamCurve(0)); ctx.setStrokeColor(rgb(0x5e0812, 0.55)); ctx.setLineWidth(40); ctx.strokePath()
ctx.addPath(seamCurve(0)); ctx.setStrokeColor(rgb(0xf6e7c8)); ctx.setLineWidth(16); ctx.strokePath()
for side in [-26.0, 26.0] {
    ctx.addPath(seamCurve(side))
    ctx.setStrokeColor(rgb(0xf6e7c8, 0.95)); ctx.setLineWidth(9)
    ctx.setLineDash(phase: 0, lengths: [14, 16]); ctx.strokePath()
}
ctx.setLineDash(phase: 0, lengths: [])
ctx.restoreGState()

// shine
ctx.saveGState()
ctx.addEllipse(in: ball); ctx.clip()
let shine = CGGradient(colorsSpace: space, colors: [rgb(0xffffff, 0.55), rgb(0xffffff, 0)] as CFArray, locations: [0, 1])!
ctx.drawRadialGradient(shine, startCenter: CGPoint(x: c.x - 110, y: c.y + 130), startRadius: 0,
                       endCenter: CGPoint(x: c.x - 110, y: c.y + 130), endRadius: 120, options: [.drawsBeforeStartLocation])
ctx.restoreGState()
ctx.addEllipse(in: ball); ctx.setStrokeColor(rgb(0x4a0610, 0.6)); ctx.setLineWidth(5); ctx.strokePath()

let image = ctx.makeImage()!
let rep = NSBitmapImageRep(cgImage: image)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
