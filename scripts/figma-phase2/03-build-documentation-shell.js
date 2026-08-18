const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing. Run 01-create-pages.js first.");
await figma.setCurrentPageAsync(page);

const existing = page.findOne(node => node.type === "FRAME" && node.name === "P2/Documentation");
if (existing) return { createdNodeIds: [], rootId: existing.id, alreadyExisted: true };

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
const styleByName = name => {
  const style = textStyles.find(item => item.name === name);
  if (!style) throw new Error(`Missing text style: ${name}`);
  return style;
};
const bindFill = (name, fallback) => figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: fallback },
  "color",
  byName(name),
);
const createdNodeIds = [];

const root = figma.createAutoLayout("VERTICAL");
root.name = "P2/Documentation";
root.resize(1440, 1);
root.layoutSizingHorizontal = "FIXED";
root.itemSpacing = 80;
root.paddingTop = 80;
root.paddingRight = 80;
root.paddingBottom = 120;
root.paddingLeft = 80;
root.fills = [bindFill("color/bg/canvas", { r: 0.95, g: 0.94, b: 0.91 })];
root.x = 0;
root.y = 0;
page.appendChild(root);
createdNodeIds.push(root.id);

const header = figma.createAutoLayout("VERTICAL");
header.name = "Documentation/Header";
header.itemSpacing = 14;
header.fills = [];
root.appendChild(header);
header.layoutSizingHorizontal = "FILL";
createdNodeIds.push(header.id);

const title = figma.createText();
title.name = "Documentation/Title";
title.fontName = { family: "SF Pro", style: "Semibold" };
title.characters = "One language. Two surfaces.";
title.textStyleId = styleByName("Display/Hero").id;
title.textAutoResize = "HEIGHT";
title.resize(1040, 1);
title.fills = [bindFill("color/text/primary", { r: 0.09, g: 0.13, b: 0.12 })];
header.appendChild(title);
createdNodeIds.push(title.id);

const description = figma.createText();
description.name = "Documentation/Description";
description.fontName = { family: "SF Pro", style: "Regular" };
description.characters = "Getting Started and Foundations share one page under the Figma Starter three-page constraint.";
description.textStyleId = styleByName("Body/Base").id;
description.textAutoResize = "HEIGHT";
description.resize(840, 1);
description.fills = [bindFill("color/text/secondary", { r: 0.41, g: 0.45, b: 0.44 })];
header.appendChild(description);
createdNodeIds.push(description.id);

const sectionNames = ["Getting Started", "Color", "Typography", "Spacing", "Radius", "Elevation"];
const sectionIds = {};
for (const sectionName of sectionNames) {
  const section = figma.createAutoLayout("VERTICAL");
  section.name = `Documentation/${sectionName}`;
  section.itemSpacing = 20;
  section.fills = [];
  section.placeholder = true;
  root.appendChild(section);
  section.layoutSizingHorizontal = "FILL";
  createdNodeIds.push(section.id);
  sectionIds[sectionName] = section.id;
}

return { createdNodeIds, rootId: root.id, sectionIds, alreadyExisted: false };
