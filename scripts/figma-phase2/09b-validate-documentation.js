const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const root = page.findOne(node => node.type === "FRAME" && node.name === "P2/Documentation");
if (!root) return { pass: false, issues: ["P2/Documentation is missing"], pageId: page.id };
const expectedSections = ["Getting Started", "Color", "Typography", "Spacing", "Radius", "Elevation"];
const sections = expectedSections.map(name => page.findOne(
  node => node.type === "FRAME" && node.name === `Documentation/${name}`,
));
const swatchFills = page.findAll(node => node.type === "RECTANGLE" && node.name.startsWith("Color/SwatchFill/"));
const spacingBars = page.findAll(node => node.type === "RECTANGLE" && node.name.startsWith("Spacing/Bar/"));
const radiusShapes = page.findAll(node => node.type === "RECTANGLE" && node.name.startsWith("Radius/Shape/"));
const issues = [];
if (sections.some(section => !section)) issues.push("One or more documentation sections are missing");
if (sections.some(section => section && section.placeholder)) issues.push("One or more documentation sections still show a placeholder");
if (swatchFills.length !== 8) issues.push(`Expected 8 color swatches, found ${swatchFills.length}`);
if (spacingBars.length !== 7) issues.push(`Expected 7 spacing bars, found ${spacingBars.length}`);
if (radiusShapes.length !== 6) issues.push(`Expected 6 radius shapes, found ${radiusShapes.length}`);
if (swatchFills.some(node => !node.fills[0].boundVariables || !node.fills[0].boundVariables.color)) {
  issues.push("A color swatch is missing its fill-variable binding");
}
if (spacingBars.some(node => !node.boundVariables || !node.boundVariables.width)) {
  issues.push("A spacing bar is missing its width-variable binding");
}
if (radiusShapes.some(node => !node.boundVariables || !node.boundVariables.cornerRadius)) {
  issues.push("A radius shape is missing its radius-variable binding");
}

return {
  pass: issues.length === 0,
  issues,
  pageId: page.id,
  rootId: root.id,
  counts: {
    sections: sections.filter(Boolean).length,
    swatches: swatchFills.length,
    spacingBars: spacingBars.length,
    radiusShapes: radiusShapes.length,
  },
  screenshotNodeId: root.id,
};
