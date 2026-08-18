const pageNames = figma.root.children.map(page => page.name);
const expectedPages = ["Cover", "Documentation", "Library"];
const pageIssues = expectedPages.filter((name, index) => pageNames[index] !== name);
const variables = await figma.variables.getLocalVariablesAsync();
const textStyles = await figma.getLocalTextStylesAsync();
const effectStyles = await figma.getLocalEffectStylesAsync();

const pageIds = {};
for (const page of figma.root.children) pageIds[page.name] = page.id;

return {
  pass:
    pageIssues.length === 0 &&
    variables.length === 50 &&
    textStyles.length >= 6 &&
    effectStyles.some(style => style.name === "Shadow/Panel"),
  pageIssues,
  pageIds,
  phase1Totals: {
    variables: variables.length,
    textStyles: textStyles.length,
    effectStyles: effectStyles.length,
  },
  nextValidations: [
    "Inspect P2/Cover on the Cover page and capture a screenshot.",
    "Inspect P2/Documentation and verify all six sections have placeholder=false.",
    "Verify Color/SwatchFill nodes use bound fill variables.",
    "Verify Spacing/Bar nodes bind width and Radius/Shape nodes bind cornerRadius.",
    "Inspect P2/Library and capture a screenshot.",
  ],
};
