// Leitner-style spaced repetition per question.
import * as db from "./db.js";

const DAY = 86400e3;
const INTERVALS = [1, 3, 7, 16, 35, 80]; // days, by box

export async function record({ q, score, max, method, answer }) {
  const ratio = max ? score / max : 0;
  const ts = Date.now();
  await db.put("attempts", { qid: q.id, subject: q.subject, ts, score, max, method, answer: answer || undefined });
  const prev = (await db.get("srs", q.id)) ?? { qid: q.id, box: 0 };
  let box = prev.box;
  if (ratio >= 0.85) box = Math.min(box + 1, INTERVALS.length - 1);
  else if (ratio < 0.5) box = 0;
  await db.put("srs", { qid: q.id, subject: q.subject, box, due: ts + INTERVALS[box] * DAY, last: ratio, ts });
}

/** Map qid -> srs state for a subject. */
export async function states(subject) {
  const map = new Map();
  for (const s of await db.all("srs")) if (s.subject === subject) map.set(s.qid, s);
  return map;
}

export const isDue = (s, now = Date.now()) => s && s.due <= now;
export const isWeak = (s) => s && s.last < 0.6;
