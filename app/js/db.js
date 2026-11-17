// IndexedDB wrapper. Stores:
//   packs      code -> pack metadata (name, components, topics, built)
//   questions  id -> question (indexed by subject)
//   images     name -> Blob
//   attempts   auto id -> {qid, subject, ts, score, max, method}
//   srs        qid -> {qid, box, due, last}
//   kv         k -> v   (settings, current session)

const DB_NAME = "alevel-trainer";
let dbp;

function open() {
  dbp ??= new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      db.createObjectStore("packs", { keyPath: "code" });
      db.createObjectStore("questions", { keyPath: "id" }).createIndex("subject", "subject");
      db.createObjectStore("images");
      db.createObjectStore("attempts", { keyPath: "id", autoIncrement: true }).createIndex("qid", "qid");
      db.createObjectStore("srs", { keyPath: "qid" });
      db.createObjectStore("kv");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbp;
}

const done = (req) => new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
const txDone = (tx) => new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error); });

async function store(name, mode = "readonly") {
  return (await open()).transaction(name, mode).objectStore(name);
}

export const get = async (s, key) => done((await store(s)).get(key));
export const all = async (s) => done((await store(s)).getAll());
export const put = async (s, value, key) => done((await store(s, "readwrite")).put(value, key));
export const del = async (s, key) => done((await store(s, "readwrite")).delete(key));
export const byIndex = async (s, index, key) => done((await store(s)).index(index).getAll(key));

/** Write many records to several stores in one transaction. writes: [[store, value, key?], ...] */
export async function putMany(writes) {
  const names = [...new Set(writes.map((w) => w[0]))];
  const tx = (await open()).transaction(names, "readwrite");
  for (const [s, value, key] of writes) tx.objectStore(s).put(value, key);
  return txDone(tx);
}

export async function clearStores(names) {
  const tx = (await open()).transaction(names, "readwrite");
  for (const n of names) tx.objectStore(n).clear();
  return txDone(tx);
}

export const setting = async (k, fallback = null) => (await get("kv", k)) ?? fallback;
export const setSetting = (k, v) => put("kv", v, k);

const urlCache = new Map();
/** Object URL for a stored image (cached for the page's lifetime). */
export async function imageURL(name) {
  if (!name) return null;
  if (!urlCache.has(name)) {
    const blob = await get("images", name);
    if (!blob) return null;
    urlCache.set(name, URL.createObjectURL(blob));
  }
  return urlCache.get(name);
}
