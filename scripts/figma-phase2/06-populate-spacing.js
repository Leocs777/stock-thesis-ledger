const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const section = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Spacing");
if (!section) throw new Error("Spacing section is missing.");
const existing = section.findOne(node => node.type === "FRAME" && node.name === "Spacing/Stack");
if (existing) return { createdNodeIds: [], stackId: existing.id, alreadyExisted: true };

await figma.loadFontAsync({ family: "SF Pro", style: "Regular" });
const variables = await figma.variables.getLocalVariablesAsync();
const bodyStyle = (await figma.getLocalTextStylesAsync()).find(style => style.name === "Body/Base");
const accent = variables.find(variable => variable.name === "color/bg/accent");
const secondary = variables.find(variable => variable.name === "color/text/secondary");
if (!bodyStyle || !accent || !secondary) throw new Error("Spacing documentation dependencies are missing.");
const spacing = [
  ["spacing/xs", 4], ["spacing/sm", 8], ["spacing/md", 12], ["spacing/lg", 16],
  ["spacing/xl", 24], ["spacing/2xl", 32], ["spacing/3xl", 48],
];
const createdNodeIds = [];
const stack = figma.createAutoLayout("VERTICAL");
stack.name = "Spacing/Stack";
stack.itemSpacing = 14;
stack.fills = [];
section.appendChild(stack);
stack.layoutSizingHorizontal = "FILL";
createdNodeIds.push(stack.id);

for (const [name, value] of spacing) {
  const variable = variables.find(item => item.name === name);
  if (!variable) throw new Error(`Missing variable: ${name}`);
  const row = figma.createAutoLayout("HORIZONTAL");
  row.name = `Spacing/Row/${name}`;
  row.counterAxisAlignItems = "CENTER";
  row.itemSpacing = 20;
  row.fills = [];
  stack.appendChild(row);
  row.layoutSizingHorizontal = "FILL";
  createdNodeIds.push(row.id);

  const bar = figma.createRectangle();
  bar.name = `Spacing/Bar/${name}`;
  bar.resize(value, 14);
  bar.cornerRadius = 4;
  bar.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 0.91, g: 0.36, b: 0.16 } },
    "color",
    accent,
  )];
  bar.setBoundVariable("width", variable);
  row.appendChild(bar);
  createdNodeIds.push(bar.id);

  const label = figma.createText();
  label.name = `Spacing/Label/${name}`;
  label.fontName = { family: "SF Pro", style: "Regular" };
  label.characters = `${name}  ${value}px  ${variable.codeSyntax.WEB || ""}`;
  label.textStyleId = bodyStyle.id;
  label.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 0.41, g: 0.45, b: 0.44 } },
    "color",
    secondary,
  )];
  row.appendChild(label);
  createdNodeIds.push(label.id);
}
section.placeholder = false;

return { createdNodeIds, mutatedNodeIds: [section.id], stackId: stack.id, spacingCount: spacing.length };
