const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const outputDirectory = path.join(root, "www");

fs.mkdirSync(outputDirectory, { recursive: true });
const source = path.join(root, "index.html");
const target = path.join(outputDirectory, "index.html");
const sourceContent = fs.readFileSync(source);
const targetContent = fs.existsSync(target) ? fs.readFileSync(target) : null;
if (!targetContent || !sourceContent.equals(targetContent)) {
  fs.writeFileSync(target, sourceContent);
}
console.log(`Prepared ${path.relative(root, target)} from ${path.relative(root, source)}`);
