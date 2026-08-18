const page = figma.root.children.find(candidate => candidate.name === "Library");
if (!page) throw new Error("Library page is missing. Run 01-create-pages.js first.");
await figma.setCurrentPageAsync(page);

const existing = page.findOne(node => node.type === "FRAME" && node.name === "P2/Library");
if (existing) return { createdNodeIds: [], libraryId: existing.id, alreadyExisted: true };

await Promise.all([
  figma.loadFontAsync({ family: "SF Pro", style: "Regular" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Semibold" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Bold" }),
]);
const variables = await figma.variables.getLocalVariablesAsync();
const styles = await figma.getLocalTextStylesAsync();
const byName = name => {
  const variable = variables.find(item => item.name === name);
  if (!variable) throw new Error(`Missing variable: ${name}`);
  return variable;
};
const styleByName = name => {
  const style = styles.find(item => item.name === name);
  if (!style) throw new Error(`Missing text style: ${name}`);
  return style;
};
const boundPaint = (name, fallback) => figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: fallback },
  "color",
  byName(name),
);
const createdNodeIds = [];
const addText = (parent, name, value, styleName, fillName, width) => {
  const text = figma.createText();
  text.name = name;
  text.fontName = { family: "SF Pro", style: "Regular" };
  text.characters = value;
  text.textStyleId = styleByName(styleName).id;
  text.textAutoResize = "HEIGHT";
  text.resize(width, 1);
  text.fills = [boundPaint(fillName, { r: 0.09, g: 0.13, b: 0.12 })];
  parent.appendChild(text);
  createdNodeIds.push(text.id);
  return text;
};

const root = figma.createAutoLayout("VERTICAL");
root.name = "P2/Library";
root.resize(1440, 1);
root.layoutSizingHorizontal = "FIXED";
root.itemSpacing = 56;
root.paddingTop = 80;
root.paddingRight = 80;
root.paddingBottom = 120;
root.paddingLeft = 80;
root.fills = [boundPaint("color/bg/canvas", { r: 0.95, g: 0.94, b: 0.91 })];
root.x = 0;
root.y = 0;
page.appendChild(root);
createdNodeIds.push(root.id);

const header = figma.createAutoLayout("VERTICAL");
header.name = "Library/Header";
header.itemSpacing = 16;
header.paddingTop = 36;
header.paddingRight = 36;
header.paddingBottom = 36;
header.paddingLeft = 36;
header.cornerRadius = 30;
header.fills = [boundPaint("color/bg/signal-soft", { r: 0.97, g: 0.85, b: 0.8 })];
root.appendChild(header);
header.layoutSizingHorizontal = "FILL";
createdNodeIds.push(header.id);
  addText(header, "Library/Eyebrow", "COMPONENTS + UTILITIES", "Label/Eyebrow", "color/text/primary", 800);
addText(header, "Library/Title", "Build the language once.", "Display/Hero", "color/text/primary", 1040);
addText(header, "Library/Description", "Phase 3 turns these contracts into variable-bound component sets and platform APIs.", "Body/Base", "color/text/secondary", 840);

const grid = figma.createAutoLayout("HORIZONTAL");
grid.name = "Library/ComponentIndex";
grid.layoutWrap = "WRAP";
grid.itemSpacing = 16;
grid.counterAxisSpacing = 16;
grid.fills = [];
root.appendChild(grid);
grid.layoutSizingHorizontal = "FILL";
createdNodeIds.push(grid.id);

const components = [
  ["Button", "Style × 4 · Size × 2 · State × 3"],
  ["Input", "State × 4 · Helper · Prefix"],
  ["Tag / Badge", "Tone × 5 · Label"],
  ["Metric card", "Tone × 3 · Trend · Footnote"],
  ["Data table", "Density × 2 · State × 3"],
  ["Navigation", "Platform × 2 · State × 2"],
];
for (const [name, axes] of components) {
  const card = figma.createAutoLayout("VERTICAL");
  card.name = `Library/Card/${name}`;
  card.resize(405, 180);
  card.layoutSizingHorizontal = "FIXED";
  card.layoutSizingVertical = "FIXED";
  card.primaryAxisAlignItems = "SPACE_BETWEEN";
  card.paddingTop = 22;
  card.paddingRight = 22;
  card.paddingBottom = 22;
  card.paddingLeft = 22;
  card.cornerRadius = 24;
  card.fills = [boundPaint("color/bg/card", { r: 0.98, g: 0.98, b: 0.96 })];
  grid.appendChild(card);
  createdNodeIds.push(card.id);
  addText(card, `Library/CardTitle/${name}`, name, "Title/Page", "color/text/primary", 340);
  addText(card, `Library/CardAxes/${name}`, axes, "Body/Base", "color/text/secondary", 340);
}

const utility = figma.createAutoLayout("VERTICAL");
utility.name = "Library/Utilities";
utility.itemSpacing = 12;
utility.paddingTop = 28;
utility.paddingRight = 28;
utility.paddingBottom = 28;
utility.paddingLeft = 28;
utility.cornerRadius = 24;
utility.fills = [boundPaint("color/bg/inverse", { r: 0.09, g: 0.13, b: 0.12 })];
root.appendChild(utility);
utility.layoutSizingHorizontal = "FILL";
createdNodeIds.push(utility.id);
addText(utility, "Library/UtilitiesTitle", "Utility contract", "Heading/Section", "color/text/inverse", 900);
addText(utility, "Library/UtilitiesCopy", "Web focus visibility, iOS Dynamic Type and VoiceOver behavior, and tabular financial digits remain platform-native.", "Body/Base", "color/text/inverse", 980);

return { createdNodeIds, libraryId: root.id, componentIndexId: grid.id, componentCount: components.length };
