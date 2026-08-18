const page = figma.root.children.find(candidate => candidate.name === "Library");
if (!page) throw new Error("Library page is missing.");
await figma.setCurrentPageAsync(page);

const root = page.findOne(node => node.type === "FRAME" && node.name === "P2/Library");
if (!root) return { pass: false, issues: ["P2/Library is missing"], pageId: page.id };
const componentCards = page.findAll(node => node.type === "FRAME" && node.name.startsWith("Library/Card/"));
const utility = page.findOne(node => node.type === "FRAME" && node.name === "Library/Utilities");
const issues = [];
if (componentCards.length !== 6) issues.push(`Expected 6 component-index cards, found ${componentCards.length}`);
if (!utility) issues.push("Library utility contract is missing");
if (componentCards.some(node => !node.fills[0].boundVariables || !node.fills[0].boundVariables.color)) {
  issues.push("A component-index card is missing its background-variable binding");
}

return {
  pass: issues.length === 0,
  issues,
  pageId: page.id,
  rootId: root.id,
  componentCardCount: componentCards.length,
  screenshotNodeId: root.id,
};
