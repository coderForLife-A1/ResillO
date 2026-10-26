const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const outputDirectory = path.join(root, "www");

fs.mkdirSync(outputDirectory, { recursive: true });
fs.copyFileSync(
  path.join(root, "index.html"),
  path.join(outputDirectory, "index.html")
);
