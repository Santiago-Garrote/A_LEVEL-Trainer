import { unzipSync, strFromU8 } from "../vendor/fflate.js";
import * as db from "./db.js";

/** Where the Subjects page looks for index.json + packs. Relative = next to the app. */
export const DEFAULT_SOURCE = "packs/";

export async function source() {
  return (await db.setting("packSource")) || DEFAULT_SOURCE;
}

/** The catalog written by `papers.py pack` / `papers.py catalog`. */
export async function fetchCatalog(src) {
  const url = new URL("index.json", new URL(src, location.href));
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  const cat = await res.json();
  if (cat.format !== 1) throw new Error(`Unsupported catalog format ${cat.format}`);
  return cat.subjects;
}

/** Download one catalog entry and import it. onProgress(phase, ratio). */
export async function install(entry, src, onProgress = () => {}) {
  const url = new URL(entry.file, new URL(src, location.href));
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length")) || entry.bytes || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    if (total) onProgress("Downloading", got / total);
  }
  return importPack(new Blob(chunks), (p) => onProgress("Installing", p));
}

/** Import a pack zip (File or Blob). Replaces an older version of the same subject. Returns {code, name, count}. */
export async function importPack(file, onProgress = () => {}) {
  const files = unzipSync(new Uint8Array(await file.arrayBuffer()));
  if (!files["pack.json"]) throw new Error("Not a study pack: pack.json missing");
  const pack = JSON.parse(strFromU8(files["pack.json"]));
  if (pack.format !== 1) throw new Error(`Unsupported pack format ${pack.format}`);

  const { questions, ...meta } = pack;
  const before = await db.byIndex("questions", "subject", pack.code);
  const writes = [];
  for (const q of questions) writes.push(["questions", q]);
  const TYPES = { png: "image/png", webp: "image/webp" };
  for (const n of Object.keys(files)) {
    const type = TYPES[n.split(".").pop()];
    if (n.startsWith("img/") && type) writes.push(["images", new Blob([files[n]], { type }), n.slice(4)]);
  }
  // pack record last, so a half-finished import never looks installed
  writes.push(["packs", { ...meta, count: questions.length }]);

  // chunk so huge packs don't build one giant transaction
  const CHUNK = 200;
  for (let i = 0; i < writes.length; i += CHUNK) {
    await db.putMany(writes.slice(i, i + CHUNK));
    onProgress(Math.min(1, (i + CHUNK) / writes.length));
  }

  // updating: drop questions and images the new build no longer has (progress is kept)
  const now = new Map(questions.map((q) => [q.id, q]));
  for (const q of before) {
    if (!now.has(q.id)) await removeQuestion(q);
    else for (const img of [q.image, q.msImage]) {
      if (img && img !== now.get(q.id).image && img !== now.get(q.id).msImage) await db.del("images", img);
    }
  }
  return { code: pack.code, name: pack.name, count: questions.length };
}

async function removeQuestion(q) {
  await db.del("questions", q.id);
  for (const img of [q.image, q.msImage]) if (img) await db.del("images", img);
}

/** Remove a subject's questions and images (progress is kept). */
export async function deletePack(code) {
  for (const q of await db.byIndex("questions", "subject", code)) await removeQuestion(q);
  await db.del("packs", code);
}
