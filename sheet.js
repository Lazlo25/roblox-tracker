// Contact sheet for game artwork, drawn with macOS AppKit (no extra installs).
// Usage: osascript -l JavaScript sheet.js spec.json out.jpg
// spec.json: [{"label": "Q01 · Game name", "thumbs": ["a.png", ...up to 3], "icon": "icon.png"}, ...] (up to 5 rows)
ObjC.import('AppKit');
function run(argv) {
  const spec = JSON.parse($.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null).js);
  const out = argv[1], ROW = 215, W = 1000, H = spec.length * ROW + 10;
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(null, W, H, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  const ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext(ctx);
  $.NSColor.colorWithCalibratedWhiteAlpha(0.12, 1).setFill;
  $.NSRectFill($.NSMakeRect(0, 0, W, H));
  const attrs = $.NSMutableDictionary.alloc.init;
  attrs.setObjectForKey($.NSFont.boldSystemFontOfSize(20), $.NSFontAttributeName);
  attrs.setObjectForKey($.NSColor.whiteColor, $.NSForegroundColorAttributeName);
  spec.forEach((row, i) => {
    const top = 10 + i * ROW; // distance from the top edge
    $(row.label).drawAtPointWithAttributes($.NSMakePoint(10, H - top - 26), attrs);
    let x = 10;
    const draw = (path, w, h) => {
      if (!path) return;
      const img = $.NSImage.alloc.initWithContentsOfFile(path);
      if (img && !img.isNil()) img.drawInRectFromRectOperationFraction($.NSMakeRect(x, H - top - 34 - h, w, h), $.NSZeroRect, $.NSCompositingOperationSourceOver, 1.0);
      x += w + 10;
    };
    draw(row.icon, 146, 146);
    (row.thumbs || []).slice(0, 3).forEach(p => draw(p, 260, 146));
  });
  ctx.flushGraphics;
  $.NSGraphicsContext.restoreGraphicsState;
  const props = $.NSMutableDictionary.alloc.init;
  props.setObjectForKey($.NSNumber.numberWithFloat(0.8), $.NSImageCompressionFactor);
  rep.representationUsingTypeProperties($.NSBitmapImageFileTypeJPEG, props).writeToFileAtomically(out, true);
  return 'ok';
}
