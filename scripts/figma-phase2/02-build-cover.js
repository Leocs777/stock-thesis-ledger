const page = figma.root.children.find(candidate => candidate.name === "Cover");
if (!page) throw new Error("Cover page is missing. Run 01-create-pages.js first.");
await figma.setCurrentPageAsync(page);

const existing = page.findOne(node => node.type === "FRAME" && node.name === "P2/Cover");
if (existing) return { createdNodeIds: [], coverId: existing.id, alreadyExisted: true };

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
  text.fills = [boundPaint(fillName, { r: 1, g: 1, b: 1 })];
  parent.appendChild(text);
  createdNodeIds.push(text.id);
  return text;
};

const root = figma.createAutoLayout("VERTICAL");
root.name = "P2/Cover";
root.resize(1440, 900);
root.layoutSizingHorizontal = "FIXED";
root.layoutSizingVertical = "FIXED";
root.primaryAxisAlignItems = "SPACE_BETWEEN";
root.paddingTop = 64;
root.paddingRight = 64;
root.paddingBottom = 64;
root.paddingLeft = 64;
root.fills = [boundPaint("color/bg/inverse", { r: 0.09, g: 0.13, b: 0.12 })];
root.x = 0;
root.y = 0;
page.appendChild(root);
createdNodeIds.push(root.id);

const meta = figma.createAutoLayout("HORIZONTAL");
meta.name = "Cover/Meta";
meta.resize(1312, 28);
meta.layoutSizingHorizontal = "FIXED";
meta.layoutSizingVertical = "FIXED";
meta.primaryAxisAlignItems = "SPACE_BETWEEN";
meta.fills = [];
root.appendChild(meta);
createdNodeIds.push(meta.id);
addText(meta, "Cover/Eyebrow", "PRIVATE FINANCIAL RESEARCH SYSTEM", "Label/Eyebrow", "color/text/accent", 600);
addText(meta, "Cover/Version", "V0.2 / PHASE 2", "Body/Base", "color/text/inverse", 240).textAlignHorizontal = "RIGHT";

const hero = figma.createAutoLayout("VERTICAL");
hero.name = "Cover/Hero";
hero.itemSpacing = 24;
hero.fills = [];
root.appendChild(hero);
createdNodeIds.push(hero.id);
addText(hero, "Cover/Title", "Investor\nLab.", "Display/Hero", "color/text/inverse", 920);
addText(
  hero,
  "Cover/Tagline",
  "An editorial decision console for paper portfolios, intraday planning, options research, and an immutable trade journal.",
  "Body/Base",
  "color/text/inverse",
  720,
);

const footer = figma.createAutoLayout("HORIZONTAL");
footer.name = "Cover/Status";
footer.itemSpacing = 80;
footer.fills = [];
root.appendChild(footer);
createdNodeIds.push(footer.id);
addText(footer, "Cover/Execution", "EXECUTION\nPaper only", "Body/Base", "color/text/inverse", 250);
addText(footer, "Cover/Platforms", "PLATFORMS\nWeb + iOS", "Body/Base", "color/text/inverse", 250);
addText(footer, "Cover/System", "SYSTEM\n50 tokens", "Body/Base", "color/text/inverse", 250);

return { createdNodeIds, coverId: root.id, alreadyExisted: false };
