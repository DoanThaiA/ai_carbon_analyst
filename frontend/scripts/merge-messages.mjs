// Gộp messages/parts/*.json ({ "<namespace>": { "vi": {...}, "en": {...} } }) thành messages/vi.json + en.json.
import fs from "node:fs";
import path from "node:path";
const dir = "messages/parts";
const out = { vi: {}, en: {} };
for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort()) {
  const part = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  for (const [ns, v] of Object.entries(part)) {
    for (const l of ["vi", "en"]) {
      if (!v[l]) throw new Error(`${f}:${ns} thiếu ${l}`);
      if (out[l][ns]) throw new Error(`namespace trùng: ${ns}`);
      out[l][ns] = v[l];
    }
  }
}
for (const l of ["vi", "en"]) fs.writeFileSync(`messages/${l}.json`, JSON.stringify(out[l], null, 2) + "\n");
console.log("merged", Object.keys(out.vi).length, "namespaces");
