const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const section = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Typography");
if (!section) throw new Error("Typography section is missing.");
const existing = section.findOne(node => node.type === "FRAME" && node.name === "Typography/Stack");
if (existing) return { createdNodeIds: [], stackId: existing.id, alreadyExisted: true };

await Promise.all([
  figma.loadFontAsync({ family: "SF Pro", style: "Regular" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Semibold" }),
  figma.loadFontAsync({ family: "SF Pro", style: "Bold" }),
]);
const variables = await figma.variables.getLocalVariablesAsync();
const styles = await figma.getLocalTextStylesAsync();
const textPrimary = variables.find(variable => variable.name === "color/text/primary");
const textSecondary = variables.find(variable => variable.name === "color/text/secondary");
if (!textPrimary || !textSecondary) throw new Error("Text color variables are missing.");
const paint = variable => figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: { r: 0.1, g: 0.1, b: 0.1 } },
  "color",
  variable,
);
const styleNames = ["Display/Hero", "Heading/Section", "Title/Page", "Body/Base", "Label/Eyebrow", "Data/Value"];
const createdNodeIds = [];

const stack = figma.createAutoLayout("VERTICAL");
stack.name = "Typography/Stack";
stack.itemSpacing = 0;
stack.fills = [];
section.appendChild(stack);
stack.layoutSizingHorizontal = "FILL";
createdNodeIds.push(stack.id);

for (const styleName of styleNames) {
  const style = styles.find(item => item.name === styleName);
  if (!style) throw new Error(`Missing text style: ${styleName}`);
  const row = figma.createAutoLayout("VERTICAL");
  row.name = `Typography/Specimen/${styleName}`;
  row.itemSpacing = 8;
  row.paddingTop = 18;
  row.paddingBottom = 18;
  row.fills = [];
  stack.appendChild(row);
  row.layoutSizingHorizontal = "FILL";
  createdNodeIds.push(row.id);

  const label = figma.createText();
  label.name = `Typography/Label/${styleName}`;
  label.fontName = { family: "SF Pro", style: "Regular" };
  label.characters = styleName;
  label.fontSize = 11;
  label.fills = [paint(textSecondary)];
  row.appendChild(label);
  createdNodeIds.push(label.id);

  const sample = figma.createText();
  sample.name = `Typography/Sample/${styleName}`;
  sample.fontName = style.fontName;
  sample.characters = styleName === "Data/Value" ? "$12,480.50" : "Capital before conviction.";
  sample.textStyleId = style.id;
  sample.textAutoResize = "HEIGHT";
  sample.resize(1120, 1);
  sample.fills = [paint(textPrimary)];
  row.appendChild(sample);
  createdNodeIds.push(sample.id);
}
section.placeholder = false;

return { createdNodeIds, mutatedNodeIds: [section.id], stackId: stack.id, specimenCount: styleNames.length };
