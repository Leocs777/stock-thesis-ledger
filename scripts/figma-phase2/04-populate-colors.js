const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const section = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Color");
if (!section) throw new Error("Color section is missing. Run 03-build-documentation-shell.js first.");
const existing = section.findOne(node => node.type === "FRAME" && node.name === "Color/Grid");
if (existing) return { createdNodeIds: [], colorGridId: existing.id, alreadyExisted: true };

await Promise.all([
  figma.loadFontAsync({ family: "SF Pro", style: "Regular" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Semibold" }),
]);
const variables = await figma.variables.getLocalVariablesAsync();
const textStyles = await figma.getLocalTextStylesAsync();
const byName = name => {
  const variable = variables.find(item => item.name === name);
  if (!variable) throw new Error(`Missing variable: ${name}`);
  return variable;
};
const headingStyle = textStyles.find(style => style.name === "Heading/Section");
const bodyStyle = textStyles.find(style => style.name === "Body/Base");
if (!headingStyle || !bodyStyle) throw new Error("Required text styles are missing.");
const textPaint = figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: { r: 0.09, g: 0.13, b: 0.12 } },
  "color",
  byName("color/text/primary"),
);
const createdNodeIds = [];

const heading = figma.createText();
heading.name = "Color/Heading";
heading.fontName = { family: "SF Pro", style: "Semibold" };
heading.characters = "Color";
heading.textStyleId = headingStyle.id;
heading.fills = [textPaint];
section.appendChild(heading);
createdNodeIds.push(heading.id);

const grid = figma.createAutoLayout("HORIZONTAL");
grid.name = "Color/Grid";
grid.layoutWrap = "WRAP";
grid.itemSpacing = 12;
grid.counterAxisSpacing = 12;
grid.fills = [];
section.appendChild(grid);
grid.layoutSizingHorizontal = "FILL";
createdNodeIds.push(grid.id);

const tokenNames = [
  "color/text/primary",
  "color/bg/canvas",
  "color/bg/card",
  "color/bg/sidebar",
  "color/bg/accent",
  "color/bg/signal-soft",
  "color/text/positive",
  "color/text/negative",
];
for (const tokenName of tokenNames) {
  const token = byName(tokenName);
  const card = figma.createAutoLayout("VERTICAL");
  card.name = `Color/Swatch/${tokenName}`;
  card.resize(292, 150);
  card.layoutSizingHorizontal = "FIXED";
  card.layoutSizingVertical = "FIXED";
  card.itemSpacing = 10;
  card.paddingTop = 12;
  card.paddingRight = 12;
  card.paddingBottom = 12;
  card.paddingLeft = 12;
  card.cornerRadius = 17;
  card.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 1, g: 1, b: 1 } },
    "color",
    byName("color/bg/card"),
  )];
  grid.appendChild(card);
  createdNodeIds.push(card.id);

  const fill = figma.createRectangle();
  fill.name = `Color/SwatchFill/${tokenName}`;
  fill.resize(268, 82);
  fill.cornerRadius = 11;
  fill.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 0.5, g: 0.5, b: 0.5 } },
    "color",
    token,
  )];
  card.appendChild(fill);
  createdNodeIds.push(fill.id);

  const label = figma.createText();
  label.name = `Color/Label/${tokenName}`;
  label.fontName = { family: "SF Pro", style: "Regular" };
  label.characters = tokenName;
  label.textStyleId = bodyStyle.id;
  label.fills = [textPaint];
  card.appendChild(label);
  createdNodeIds.push(label.id);
}
section.placeholder = false;

return { createdNodeIds, mutatedNodeIds: [section.id], colorGridId: grid.id, swatchCount: tokenNames.length };
