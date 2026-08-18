const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const section = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Getting Started");
if (!section) throw new Error("Getting Started section is missing.");
const existing = section.findOne(node => node.type === "FRAME" && node.name === "GettingStarted/Grid");
if (existing) return { createdNodeIds: [], gridId: existing.id, alreadyExisted: true };

await Promise.all([
  figma.loadFontAsync({ family: "SF Pro", style: "Regular" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Semibold" }),
]);
const variables = await figma.variables.getLocalVariablesAsync();
const styles = await figma.getLocalTextStylesAsync();
const byName = name => {
  const variable = variables.find(item => item.name === name);
  if (!variable) throw new Error(`Missing variable: ${name}`);
  return variable;
};
const headingStyle = styles.find(style => style.name === "Heading/Section");
const bodyStyle = styles.find(style => style.name === "Body/Base");
if (!headingStyle || !bodyStyle) throw new Error("Required text styles are missing.");
const paint = (name, fallback) => figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: fallback },
  "color",
  byName(name),
);
const createdNodeIds = [];

const heading = figma.createText();
heading.name = "GettingStarted/Heading";
heading.fontName = { family: "SF Pro", style: "Semibold" };
heading.characters = "Getting started";
heading.textStyleId = headingStyle.id;
heading.fills = [paint("color/text/primary", { r: 0.09, g: 0.13, b: 0.12 })];
section.appendChild(heading);
createdNodeIds.push(heading.id);

const grid = figma.createAutoLayout("HORIZONTAL");
grid.name = "GettingStarted/Grid";
grid.itemSpacing = 16;
grid.fills = [];
section.appendChild(grid);
grid.layoutSizingHorizontal = "FILL";
createdNodeIds.push(grid.id);

const cards = [
  ["01 / TOKENS", "Semantic first\nComponents consume semantic variables instead of primitive values."],
  ["02 / PLATFORMS", "Shared intent\nWeb and iOS keep one information model with native interaction behavior."],
  ["03 / SAFETY", "Research before execution\nSignals remain gated until data, rules, and risk controls are auditable."],
];
for (const [label, copy] of cards) {
  const card = figma.createAutoLayout("VERTICAL");
  card.name = `GettingStarted/Card/${label}`;
  card.resize(405, 170);
  card.layoutSizingHorizontal = "FIXED";
  card.layoutSizingVertical = "FIXED";
  card.primaryAxisAlignItems = "SPACE_BETWEEN";
  card.paddingTop = 20;
  card.paddingRight = 20;
  card.paddingBottom = 20;
  card.paddingLeft = 20;
  card.cornerRadius = 17;
  card.setBoundVariable("cornerRadius", byName("radius/md"));
  card.fills = [paint("color/bg/card", { r: 0.98, g: 0.98, b: 0.96 })];
  grid.appendChild(card);
  createdNodeIds.push(card.id);

  const labelNode = figma.createText();
  labelNode.name = `GettingStarted/Label/${label}`;
  labelNode.fontName = { family: "SF Pro", style: "Semibold" };
  labelNode.characters = label;
  labelNode.fontSize = 11;
  labelNode.fills = [paint("color/text/primary", { r: 0.09, g: 0.13, b: 0.12 })];
  card.appendChild(labelNode);
  createdNodeIds.push(labelNode.id);

  const copyNode = figma.createText();
  copyNode.name = `GettingStarted/Copy/${label}`;
  copyNode.fontName = { family: "SF Pro", style: "Regular" };
  copyNode.characters = copy;
  copyNode.textStyleId = bodyStyle.id;
  copyNode.textAutoResize = "HEIGHT";
  copyNode.resize(365, 1);
  copyNode.fills = [paint("color/text/secondary", { r: 0.41, g: 0.45, b: 0.44 })];
  card.appendChild(copyNode);
  createdNodeIds.push(copyNode.id);
}
section.placeholder = false;

return { createdNodeIds, mutatedNodeIds: [section.id], gridId: grid.id, cardCount: cards.length };
