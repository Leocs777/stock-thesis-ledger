const page = figma.root.children.find(candidate => candidate.name === "Cover");
if (!page) throw new Error("Cover page is missing.");
await figma.setCurrentPageAsync(page);

const root = page.findOne(node => node.type === "FRAME" && node.name === "P2/Cover");
if (!root) return { pass: false, issues: ["P2/Cover is missing"], pageId: page.id };
const textNodes = root.findAllWithCriteria({ types: ["TEXT"] });
const issues = [];
if (root.width !== 1440 || root.height !== 900) issues.push("Cover must be 1440x900");
if (textNodes.length < 7) issues.push("Cover is missing expected text content");
if (!root.fills[0] || !root.fills[0].boundVariables || !root.fills[0].boundVariables.color) {
  issues.push("Cover background is not bound to a color variable");
}

return {
  pass: issues.length === 0,
  issues,
  pageId: page.id,
  rootId: root.id,
  textCount: textNodes.length,
  screenshotNodeId: root.id,
};
