const createdNodeIds = [];
const mutatedNodeIds = [];

let cover = figma.root.children.find(page => page.name === "Cover");
if (!cover) {
  const starter = figma.root.children.find(page => page.name === "Page 1");
  if (!starter) throw new Error("Expected Page 1 or an existing Cover page.");
  starter.name = "Cover";
  cover = starter;
  mutatedNodeIds.push(starter.id);
}

function ensurePage(name) {
  let page = figma.root.children.find(candidate => candidate.name === name);
  if (!page) {
    page = figma.createPage();
    page.name = name;
    createdNodeIds.push(page.id);
  }
  return page;
}

const documentation = ensurePage("Documentation");
const library = ensurePage("Library");
const orderedPages = [cover, documentation, library];

for (let index = 0; index < orderedPages.length; index += 1) {
  figma.root.insertChild(index, orderedPages[index]);
  mutatedNodeIds.push(orderedPages[index].id);
}

return {
  createdNodeIds,
  mutatedNodeIds: [...new Set(mutatedNodeIds)],
  pages: orderedPages.map((page, index) => ({ index, id: page.id, name: page.name })),
};
