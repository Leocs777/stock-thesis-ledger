const page = figma.root.children.find(candidate => candidate.name === "Documentation");
if (!page) throw new Error("Documentation page is missing.");
await figma.setCurrentPageAsync(page);

const radiusSection = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Radius");
const elevationSection = page.findOne(node => node.type === "FRAME" && node.name === "Documentation/Elevation");
if (!radiusSection || !elevationSection) throw new Error("Radius or Elevation section is missing.");
const existing = radiusSection.findOne(node => node.type === "FRAME" && node.name === "Radius/Row");
if (existing) return { createdNodeIds: [], radiusRowId: existing.id, alreadyExisted: true };

await figma.loadFontAsync({ family: "SF Pro", style: "Regular" });
const variables = await figma.variables.getLocalVariablesAsync();
const styles = await figma.getLocalEffectStylesAsync();
const bodyStyle = (await figma.getLocalTextStylesAsync()).find(style => style.name === "Body/Base");
const accent = variables.find(variable => variable.name === "color/bg/accent");
const cardFill = variables.find(variable => variable.name === "color/bg/card");
const primary = variables.find(variable => variable.name === "color/text/primary");
const panelShadow = styles.find(style => style.name === "Shadow/Panel");
if (!bodyStyle || !accent || !cardFill || !primary || !panelShadow) throw new Error("Radius/Elevation dependencies are missing.");
const radii = [["radius/xs", 8], ["radius/sm", 11], ["radius/md", 17], ["radius/lg", 24], ["radius/xl", 30], ["radius/full", 999]];
const createdNodeIds = [];

const row = figma.createAutoLayout("HORIZONTAL");
row.name = "Radius/Row";
row.itemSpacing = 24;
row.fills = [];
radiusSection.appendChild(row);
row.layoutSizingHorizontal = "FILL";
createdNodeIds.push(row.id);

for (const [name, value] of radii) {
  const variable = variables.find(item => item.name === name);
  if (!variable) throw new Error(`Missing variable: ${name}`);
  const card = figma.createAutoLayout("VERTICAL");
  card.name = `Radius/Card/${name}`;
  card.itemSpacing = 8;
  card.counterAxisAlignItems = "CENTER";
  card.fills = [];
  row.appendChild(card);
  createdNodeIds.push(card.id);

  const shape = figma.createRectangle();
  shape.name = `Radius/Shape/${name}`;
  shape.resize(96, 96);
  shape.cornerRadius = Math.min(value, 48);
  shape.setBoundVariable("cornerRadius", variable);
  shape.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 0.91, g: 0.36, b: 0.16 }, opacity: 0.16 },
    "color",
    accent,
  )];
  card.appendChild(shape);
  createdNodeIds.push(shape.id);

  const label = figma.createText();
  label.name = `Radius/Label/${name}`;
  label.fontName = { family: "SF Pro", style: "Regular" };
  label.characters = `${name}\n${value === 999 ? "full" : `${value}px`}`;
  label.textStyleId = bodyStyle.id;
  label.textAlignHorizontal = "CENTER";
  label.fills = [figma.variables.setBoundVariableForPaint(
    { type: "SOLID", color: { r: 0.09, g: 0.13, b: 0.12 } },
    "color",
    primary,
  )];
  card.appendChild(label);
  createdNodeIds.push(label.id);
}
radiusSection.placeholder = false;

const shadowCard = figma.createAutoLayout("VERTICAL");
shadowCard.name = "Elevation/ShadowCard";
shadowCard.resize(260, 140);
shadowCard.layoutSizingHorizontal = "FIXED";
shadowCard.layoutSizingVertical = "FIXED";
shadowCard.primaryAxisAlignItems = "CENTER";
shadowCard.counterAxisAlignItems = "CENTER";
shadowCard.cornerRadius = 17;
shadowCard.fills = [figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: { r: 1, g: 1, b: 1 } },
  "color",
  cardFill,
)];
shadowCard.effectStyleId = panelShadow.id;
elevationSection.appendChild(shadowCard);
createdNodeIds.push(shadowCard.id);

const shadowLabel = figma.createText();
shadowLabel.name = "Elevation/Label";
shadowLabel.fontName = { family: "SF Pro", style: "Regular" };
shadowLabel.characters = "Shadow/Panel\n0 · 22 · 60 · 10%";
shadowLabel.textStyleId = bodyStyle.id;
shadowLabel.textAlignHorizontal = "CENTER";
shadowLabel.fills = [figma.variables.setBoundVariableForPaint(
  { type: "SOLID", color: { r: 0.09, g: 0.13, b: 0.12 } },
  "color",
  primary,
)];
shadowCard.appendChild(shadowLabel);
createdNodeIds.push(shadowLabel.id);
elevationSection.placeholder = false;

return {
  createdNodeIds,
  mutatedNodeIds: [radiusSection.id, elevationSection.id],
  radiusRowId: row.id,
  shadowCardId: shadowCard.id,
  radiusCount: radii.length,
};
